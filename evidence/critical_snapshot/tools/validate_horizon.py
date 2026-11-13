"""Independent positive-query direct H4/H8 chains on random tiny Qwen."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from torch.func import functional_call
from transformers import Qwen2Config, Qwen2ForCausalLM
from transformers.models.qwen2 import modeling_qwen2
from gaosi.horizon import (check_config, compare_trees, gradient_comparison,
                           limitation, tree_digest, run)
from gaosi.meta import rollout
from gaosi.model import AdapterModel, FactorSpec, NativeLoRA
from gaosi.scale import sha256
from gaosi.transition import Phi, detach_tree, leaf_parameters, scalar_stats, transition, zero_state
from device_guard import uses_musa
from unittest.mock import patch


class Batches:
    def __init__(self, batches):
        self.batches, self.cursor = batches, 0

    def batch(self, device):
        value = self.batches[self.cursor % len(self.batches)]
        self.cursor += 1
        return value


def direct(model, initial, phi, support, queries, stats, K, tail, gradient=True):
    # No recomputation custom function, rollout, terminal-objective helper, or
    # H1 replay: ordinary forward/grad on the true positive CE endpoint.
    work, state = leaf_parameters(initial), zero_state(initial)
    for k in range(K):
        exact = k >= K - tail
        if not exact or k == K - tail:
            work, state = leaf_parameters(work), detach_tree(state)
        losses = [functional_call(model, work, (support[name][k],), strict=False)
                  for name in ['task', 'language', 'keep']]
        ld, lr = losses[0], .5 * losses[1] + .5 * losses[2]
        gd = dict(zip(work, torch.autograd.grad(ld, tuple(work.values()),
                                              create_graph=exact and tail > 1)))
        gr = dict(zip(work, torch.autograd.grad(lr, tuple(work.values()),
                                              create_graph=exact and tail > 1)))
        if not exact or tail == 1:
            ld, lr = ld.detach(), lr.detach()
        with torch.set_grad_enabled(exact):
            work, state, _ = transition(work, gd, gr, state, model.specs, stats, ld, lr,
                                        phi, .001, K, .7, layers=1, chunk=17)
    loss = sum(functional_call(model, work, (queries[key][0],), strict=False)
               for key in ['task', 'language', 'keep'])
    grads = torch.autograd.grad(loss, tuple(phi.parameters()), allow_unused=True) if gradient else None
    return work, state, loss, grads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='checks/horizon_cpu.json')
    parser.add_argument('--upstream-precision', action='store_true',
                        help='Unmodified upstream forced-FP32 operators, direct AD only; no finite differences')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(4703)
    qc = Qwen2Config(vocab_size=23, hidden_size=8, intermediate_size=16,
                     num_hidden_layers=1, num_attention_heads=1, num_key_value_heads=1,
                     head_dim=8, attention_dropout=0.)
    qc._attn_implementation = 'eager'
    model = AdapterModel.__new__(AdapterModel)
    torch.nn.Module.__init__(model)
    model.llm = Qwen2ForCausalLM(qc).double().eval()
    model.llm.requires_grad_(False)
    if not args.upstream_precision:
        # Mathematical finite-difference reference only; never installed in
        # production models. Stock Qwen forces FP32 norm and softmax on double
        # inputs, making tiny central differences noisy rather than FP64.
        def norm_fp64(self, x):
            return self.weight * x * torch.rsqrt(x.square().mean(-1, keepdim=True) + self.variance_epsilon)
        for module in model.llm.modules():
            if isinstance(module, modeling_qwen2.Qwen2RMSNorm):
                module.forward = types.MethodType(norm_fp64, module)

        def attention_fp64(module, query, key, value, attention_mask, scaling, dropout=0., **kwargs):
            key = modeling_qwen2.repeat_kv(key, module.num_key_value_groups)
            value = modeling_qwen2.repeat_kv(value, module.num_key_value_groups)
            weights = query @ key.transpose(2, 3) * scaling
            if attention_mask is not None:
                weights = weights + attention_mask
            weights = weights.softmax(-1)
            return (weights @ value).transpose(1, 2).contiguous(), weights
        modeling_qwen2.eager_attention_forward = attention_fp64
    model._specs = {}
    for module_name in ['q_proj', 'v_proj']:
        owner = model.llm.model.layers[0].self_attn
        setattr(owner, module_name, NativeLoRA(getattr(owner, module_name), 2).double())
        with torch.no_grad():
            getattr(owner, module_name).branches.single.B.normal_(0, .07)
        for factor in ['A', 'B']:
            key = f'llm.model.layers.0.self_attn.{module_name}.branches.single.' + factor
            model.specs[key] = FactorSpec(key, 0, module_name, factor, 'single', 2, 1.,
                                         key[:-1] + ('B' if factor == 'A' else 'A'))
    initial = model.adapter_cpu()
    stats = {k: scalar_stats(v) for k, v in initial.items()}

    def batch():
        ids = torch.randint(0, 23, (1, 5))
        return {'input_ids': ids, 'labels': ids, 'attention_mask': torch.ones_like(ids)}

    support = {key: [batch() for _ in range(8)] for key in ['task', 'language', 'keep']}
    queries = {key: [batch()] for key in support}
    tests, details = {}, []
    for architecture in ['L', 'X']:
        phi = Phi(architecture).double()
        with torch.no_grad():
            phi.net[-1].weight.normal_(0, .015)
            phi.net[-1].bias.normal_(0, .01)
            if architecture == 'X':
                phi.coord[-1].weight.normal_(0, .015)
        phi_before = tree_digest(phi.state_dict())
        for K in [4, 8]:
            baseline = None
            gradients = {}
            for tail in [1, 2, K]:
                work, state, loss, direct_grads = direct(model, initial, phi, support, queries, stats, K, tail)
                streams = {key: Batches(value) for key, value in support.items()}
                phi.zero_grad(set_to_none=True)
                events = []
                observed, observed_state, diag = rollout(
                    model, initial, zero_state(initial), phi, streams, stats, K, .7, .001,
                    effective_tokens=4, horizon='H1' if tail == 1 else f'EXACT_H{tail}_tail',
                    queries=queries, reference={'language': torch.tensor(-100.), 'keep': torch.tensor(-100.)},
                    chunk=17, log_callback=lambda **kw: events.append(kw))
                endpoint = {'adapter': observed, 'state': observed_state}
                compare_trees(endpoint, {'adapter': work, 'state': state}, atol=1e-11, rtol=1e-9)
                if baseline is None:
                    baseline = endpoint
                else:
                    compare_trees(endpoint, baseline, atol=1e-11, rtol=1e-9)
                for p, expected in zip(phi.parameters(), direct_grads):
                    assert p.grad is not None and expected is not None
                    torch.testing.assert_close(p.grad, expected, atol=1e-9, rtol=2e-6)
                assert abs(diag['objective'] - float(loss.detach()) - 199.8) < 1e-9
                assert all(stream.cursor == K for stream in streams.values())
                assert sum(e['exact_window'] for e in events) == (tail if tail > 1 else 0)
                gradients[tail] = torch.cat([p.grad.flatten() for p in phi.parameters()]).detach().clone()
                tests[f'{architecture}_K{K}_H{tail}_positive_query_direct_chain_and_all_state'] = 'PASS'
            for tail in [1, 2]:
                metric = gradient_comparison(gradients[tail], gradients[K])
                assert metric['relative_l2_error'] > 1e-6
                details.append({'architecture': architecture, 'K': K, 'tail': tail, **metric})
            if args.upstream_precision:
                continue
            # Directional central difference of the complete K-step endpoint.
            direction = [torch.randn_like(p) for p in phi.parameters()]
            norm = sum(v.square().sum() for v in direction).sqrt()
            direction = [v / norm for v in direction]
            expected = sum((g * d.flatten()).sum() for g, d in
                           zip(gradients[K].split([p.numel() for p in phi.parameters()]), direction))
            original = copy.deepcopy(phi.state_dict())
            differences = []
            for eps in [1e-4, 1e-5]:
                endpoints = []
                for sign in [1, -1]:
                    phi.load_state_dict(original)
                    with torch.no_grad():
                        for p, d in zip(phi.parameters(), direction):
                            p.add_(d, alpha=sign * eps)
                    _, _, loss, _ = direct(model, initial, phi, support, queries, stats, K, K, gradient=False)
                    endpoints.append(float(loss.detach()))
                fd = (endpoints[0] - endpoints[1]) / (2 * eps)
                differences.append({'eps': eps, 'observed': fd, 'expected': float(expected)})
                print(json.dumps({'finite_difference': architecture, 'K': K,
                                  **differences[-1]}), flush=True)
            phi.load_state_dict(original)
            for item in differences[-2:]:
                torch.testing.assert_close(torch.tensor(item['observed']), expected.float(), atol=1e-7, rtol=5e-3)
            tests[f'{architecture}_complete_H{K}_central_difference'] = 'PASS'
        assert tree_digest(phi.state_dict()) == phi_before
    tests['trained_nonzero_heads_unchanged_and_truncation_difference_detected'] = 'PASS'
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as temporary:
        try:
            os.chdir(temporary)
            Path('data/synthetic').mkdir(parents=True)
            Path('data/language').mkdir(parents=True)
            dev = 'data/synthetic/dev_parent.jsonl'
            Path(dev).write_text(json.dumps({'world_id': 'dev_parent'}) + '\n')
            for p in ['data/language/inner.jsonl', 'data/language/dev.jsonl']:
                Path(p).write_text('{}\n')
            Path('phi.pt').write_bytes(b'fixture_only')
            c = {'horizon_manifest': 'plan.json', 'data': dev, 'retain_data': dev,
                 'language_support': 'data/language/inner.jsonl',
                 'language_query': 'data/language/dev.jsonl', 'K': 8, 'tail': 8}
            plan = {'status': 'FROZEN_DEVELOPMENT_HORIZON_PLAN', 'formal_data_used': False,
                    'configs': [c], 'artifact_sha256': {'phi.pt': sha256('phi.pt')},
                    'data_sha256': {p: sha256(p) for p in [dev, c['language_support'], c['language_query']]}}
            Path('plan.json').write_text(json.dumps(plan))
            assert check_config(c) == plan
            for mutation in [dict(c, K=16), dict(c, tail=4), dict(c, data='final.jsonl')]:
                try:
                    check_config(mutation)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Altered settings accepted')
            tests['declared_config_budget_and_data_rejected_after_freeze'] = 'PASS'
            Path('phi.pt').write_bytes(b'mutated')
            try:
                check_config(c)
            except ValueError:
                pass
            else:
                raise AssertionError('Changed phi accepted')
            Path('phi.pt').write_bytes(b'fixture_only')
            Path(dev).write_text(json.dumps({'world_id': 'final_forbidden'}) + '\n')
            plan['data_sha256'][dev] = sha256(dev)
            Path('plan.json').write_text(json.dumps(plan))
            try:
                check_config(c)
            except ValueError:
                pass
            else:
                raise AssertionError('Final IDs accepted even with updated data hash')
            tests['phi_bytes_and_development_world_permission_enforced'] = 'PASS'
            Path('config.json').write_text(json.dumps({'device': 'musa'}))
            assert uses_musa(['/usr/bin/python', '-m', 'gaosi.horizon', '--config', 'config.json'])
            tests['same_MUSA_leaf_device_lock'] = 'PASS'
            # Execute the real entry on a random tiny model and real serialized
            # parent/phi/data. Only model loading is replaced. The fixture is
            # explicitly not a trained full-model checkpoint or effect result.
            class TinyTokenizer:
                eos_token_id, pad_token_id = 22, 0

                def encode(self, text, add_special_tokens=False):
                    return [1 + b % 21 for b in text.encode()]

            model.tokenizer = TinyTokenizer()
            model.summary = lambda: {'scope': 'random tiny CPU runtime fixture'}
            model.compute_counters = {'loss_forwards': 0, 'loss_backwards': 0,
                                      'input_tokens': 0, 'supervised_tokens': 0,
                                      'hessian_vector_products': 0}
            # The real entry uses FP32 persistent adapter/phi parameters.
            model.float()
            phi.float()
            records = []
            for index in range(6):
                for role in ['support', 'query']:
                    records.append({'world_id': 'dev_parent', 'fact_id': f'dev_{index}',
                                    'sample_id': f'dev_{index}_{role}', 'role': role,
                                    'prompt': f'Q{index}=', 'answer': str(index)})
            Path(dev).write_text(''.join(json.dumps(r) + '\n' for r in records))
            for p in ['data/language/inner.jsonl', 'data/language/dev.jsonl']:
                Path(p).write_text(json.dumps({'document_id': p, 'text': 'fixture text words'}) + '\n')
            Path('parent').mkdir()
            pc = {'data': dev, 'organization': 'O1', 'model': 'random_tiny_fixture',
                  'rank': 2, 'targets': 'qv', 'scale': 1.}
            torch.save({'adapter': initial, 'config': pc}, 'parent/latest.pt')
            Path('parent/result.json').write_text(json.dumps({'acquisition_gate_passed': True}))
            torch.save({'phi': phi.state_dict(), 'config': {'architecture': 'X'},
                        'inner_updates': 2560, 'scope': 'random untrained fixture'}, 'phi.pt')
            runtime_configs = []
            for tail in [1, 2, 4]:
                runtime_configs.append({
                    'horizon_manifest': 'plan.json', 'out': f'run_h{tail}', 'data': dev,
                    'retain_data': dev, 'language_support': 'data/language/inner.jsonl',
                    'language_query': 'data/language/dev.jsonl', 'K': 4, 'tail': tail,
                    'parent': 'parent/latest.pt', 'phi': 'phi.pt', 'architecture': 'X',
                    'device': 'cpu', 'data_seed': 1701, 'query_seed': 1702,
                    'noise_seed': 1729, 'seq': 32, 'effective_tokens': 8,
                    'q': .7, 'inner_lr': .001, 'retain_weight': .25, 'penalty': 1.,
                    'chunk': 17, 'reference_endpoint': 'run_h1/endpoint.pt' if tail != 1 else None})
            plan = {'status': 'FROZEN_DEVELOPMENT_HORIZON_PLAN', 'formal_data_used': False,
                    'configs': runtime_configs,
                    'artifact_sha256': {p: sha256(p) for p in ['phi.pt', 'parent/latest.pt']},
                    'data_sha256': {p: sha256(p) for p in [dev, c['language_support'], c['language_query']]}}
            Path('plan.json').write_text(json.dumps(plan))
            with patch('gaosi.model.AdapterModel', return_value=model):
                for config in runtime_configs:
                    result = run(config)
                    assert result['diagnostic_status'] == 'COMPLETE' and result['optimizer_updates'] == 0
                    assert result['base_unchanged'] and result['phi_unchanged']
                    assert Path(config['out'], 'gradient.pt').is_file()
            tests['real_diagnostic_entry_files_three_routes_all_state_and_phi_base_freeze'] = 'PASS'
        finally:
            os.chdir(original_cwd)
    assert limitation(RuntimeError('MUSA out of memory'), 'musa') == 'NOT_AVAILABLE_MEMORY'
    assert limitation(RuntimeError('operation not implemented for MUSA'), 'musa') == 'NOT_AVAILABLE_BACKEND'
    assert limitation(RuntimeError('shape mismatch'), 'musa') is None
    assert limitation(RuntimeError('out of memory'), 'cpu') is None
    tests['only_explicit_MUSA_limitations_are_classified_not_unknown_failures'] = 'PASS'
    result = {'status': 'PASS', 'tests': tests, 'comparisons': details,
              'reference_precision': 'upstream forced-FP32 norm/attention' if args.upstream_precision
                                     else 'explicit FP64 norm/attention for mathematical finite differences',
              'scope': 'CPU random tiny Qwen; positive-query H4/H8 versus independent direct AD, plus finite differences only in FP64 reference. Not real MUSA feasibility or phi effectiveness.'}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
