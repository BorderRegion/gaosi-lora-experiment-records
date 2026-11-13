"""Compare direct and recomputed H2 on a tiny Qwen on real MUSA.

This is an operator diagnostic with random data, never training-effect evidence.
The direct path keeps the ordinary two-step autograd graph independently.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from torch.func import functional_call
from transformers import Qwen2Config, Qwen2ForCausalLM

from gaosi.meta import rollout
import gaosi.meta as meta
from gaosi.model import AdapterModel, FactorSpec, NativeLoRA, TARGETS
from gaosi.runtime import atomic_save, seed_all
from gaosi.transition import Phi, detach_tree, leaf_parameters, scalar_stats, transition, zero_state


class Batches:
    def __init__(self, values):
        self.values, self.cursor = values, 0

    def batch(self, device):
        value = self.values[self.cursor]
        self.cursor += 1
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    import torch_musa
    torch.set_num_threads(8)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for dtype in [torch.bfloat16, torch.float32]:
        seed_all(17013)
        config = Qwen2Config(vocab_size=97, hidden_size=32, intermediate_size=64,
                             num_hidden_layers=2, num_attention_heads=2,
                             num_key_value_heads=1, head_dim=16, attention_dropout=0.)
        config._attn_implementation = 'eager'
        model = AdapterModel.__new__(AdapterModel)
        torch.nn.Module.__init__(model)
        model.llm = Qwen2ForCausalLM(config).to(device='musa', dtype=dtype).eval()
        model.llm.requires_grad_(False)
        model._specs = {}
        for name, module in list(model.llm.named_modules()):
            if name.split('.')[-1] not in TARGETS['all7'] or not isinstance(module, torch.nn.Linear):
                continue
            owner, _, child = name.rpartition('.')
            layer = NativeLoRA(module, 4)
            with torch.no_grad():
                layer.branches.single.B.normal_(0., .02)
            setattr(model.llm.get_submodule(owner), child, layer)
            for factor in ['A', 'B']:
                key = 'llm.' + name + '.branches.single.' + factor
                model.specs[key] = FactorSpec(key, int(name.split('.')[2]), child,
                                             factor, 'single', 4, 1.,
                                             key[:-1] + ('B' if factor == 'A' else 'A'))
        initial = {k: v.detach().clone() for k, v in model.adapter().items()}
        stats = detach_tree({k: scalar_stats(v) for k, v in initial.items()})
        phi = Phi('L').to('musa')
        with torch.no_grad():
            phi.net[-1].weight.normal_(0., .01)

        def batch():
            ids = torch.randint(0, config.vocab_size, (1, 16), device='musa')
            labels = ids.clone()
            labels[:, ::4] = -100
            return {'input_ids': ids, 'labels': labels, 'attention_mask': torch.ones_like(ids),
                    'segment_ids': torch.arange(4, device='musa').repeat_interleave(4)[None, :],
                    'position_ids': torch.arange(4, device='musa').repeat(4)[None, :]}

        support = {k: [[batch(), batch()] for _ in range(2)] for k in ['task', 'language', 'keep']}
        queries = {k: [batch(), batch()] for k in support}

        def loss(parameters, batches):
            total = sum(int(b['labels'][:, 1:].ne(-100).sum().cpu()) for b in batches)
            return sum(functional_call(model, parameters, (b,), strict=False)
                       * int(b['labels'][:, 1:].ne(-100).sum().cpu()) / total for b in batches)

        def support_direct(parameters, batches):
            # Match the production FP32 accumulation at adapter leaves. Keep
            # every ordinary microbatch graph for an independent direct H2.
            total = sum(int(b['labels'][:, 1:].ne(-100).sum().cpu()) for b in batches)
            mean = 0.
            grads = {k: torch.zeros_like(v) for k, v in parameters.items()}
            for b in batches:
                value = (functional_call(model, parameters, (b,), strict=False)
                         * int(b['labels'][:, 1:].ne(-100).sum().cpu()) / total)
                partial = torch.autograd.grad(value, tuple(parameters.values()), create_graph=True)
                mean = mean + value
                grads = {k: grads[k] + v for k, v in zip(parameters, partial)}
            return mean, grads

        reference = {k: torch.tensor(0., device='musa') for k in queries}
        for repeat in range(args.repeats):
            phi.zero_grad(set_to_none=True)
            work, state = leaf_parameters(initial), zero_state(initial)
            direct_routes = []
            for step in range(2):
                (ld, gd), (ll, gl), (lk, gk) = [support_direct(work, support[k][step])
                                              for k in ['task', 'language', 'keep']]
                for value, grads in [(ld, gd), (ll, gl), (lk, gk)]:
                    direct_routes.append({'loss': float(value.detach().cpu()),
                                          'parameters': {k: p.detach().cpu().clone() for k, p in work.items()},
                                          'gradients': {k: g.detach().cpu().clone() for k, g in grads.items()}})
                lr = .5 * ll + .5 * lk
                gr = {k: .5 * gl[k] + .5 * gk[k] for k in work}
                work, state, _ = transition(work, gd, gr, state, model.specs, stats,
                                            ld, lr, phi, .0003, 2, .5, layers=2)
            direct = sum(loss(work, queries[k]) for k in queries) - .2
            direct.backward()
            direct_grads = {k: p.grad.detach().cpu().clone() for k, p in phi.named_parameters()}
            phi.zero_grad(set_to_none=True)
            streams = {k: Batches([b for values in support[k] for b in values]) for k in support}
            stages = []
            original_support = meta.support_gradients

            def observe_support(model_arg, parameters, batches, create_graph=False):
                value, grads = original_support(model_arg, parameters, batches, create_graph)
                expected = direct_routes[len(stages)]
                a = torch.cat([g.flatten().double() for g in expected['gradients'].values()])
                b = torch.cat([g.detach().cpu().flatten().double() for g in grads.values()])
                stages.append({'route_call': len(stages),
                               'input_max_difference': max(float((p.detach().cpu() - expected['parameters'][k]).abs().max())
                                                           for k, p in parameters.items()),
                               'loss_difference': float(value.detach().cpu()) - expected['loss'],
                               'gradient_relative_l2_difference': float((a - b).norm() / a.norm().clamp_min(1e-30))})
                return value, grads

            meta.support_gradients = observe_support
            try:
                _, _, terminal = rollout(model, initial, zero_state(initial), phi, streams, stats,
                                         2, .5, .0003, effective_tokens=24,
                                         horizon='EXACT_H2_tail', queries=queries, reference=reference)
            finally:
                meta.support_gradients = original_support
            replay_grads = {k: p.grad.detach().cpu().clone() for k, p in phi.named_parameters()}
            a = torch.cat([g.flatten().double() for g in direct_grads.values()])
            b = torch.cat([g.flatten().double() for g in replay_grads.values()])
            finite = bool(torch.isfinite(a).all() and torch.isfinite(b).all())
            row = {'dtype': str(dtype), 'repeat': repeat, 'finite': finite,
                   'direct_objective': float(direct.detach().cpu()),
                   'recomputed_objective': terminal['objective'],
                   'direct_norm': float(a.norm()) if finite else None,
                   'recomputed_norm': float(b.norm()) if finite else None,
                   'support_stages': stages,
                   'relative_l2_difference': float((a - b).norm() / a.norm().clamp_min(1e-30)) if finite else None}
            atomic_save({'direct': direct_grads, 'recomputed': replay_grads},
                        out / f'{str(dtype).split(".")[-1]}_{repeat}.pt')
            records.append(row)
            (out / 'observations.json').write_text(json.dumps(records, indent=2))
            print(json.dumps(row, allow_nan=False), flush=True)
        del model, initial, work, state, phi
    (out / 'result.json').write_text(json.dumps({'status': 'DIAGNOSTIC_COMPLETE',
        'scope': 'Random tiny MUSA operator comparison; not full-model training or effect',
        'observations': records}, indent=2))


if __name__ == '__main__':
    main()
