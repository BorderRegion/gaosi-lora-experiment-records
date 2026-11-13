"""CPU integration checks against direct stock Adam, not recovery evidence.

The temporary parent gate is a test fixture; no production acquisition result
is changed or inferred from these tiny random-model checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.data import FactStream, LanguageStream, collate, qa_tokens, route_batches, rows, token_count
from gaosi.evaluate import evaluate_qa
from gaosi.model import AdapterModel
from gaosi.noise import canonical_noise
from gaosi.runtime import atomic_save
import gaosi.recover as recovery


def direct_adam(config, parent_config, adapter, variance_floor=0.):
    model = AdapterModel(parent_config['model'], 2, 'all7', 'O1', 'cpu')
    initial, _ = canonical_noise(adapter, model.specs, config['q_apply'], config['noise_seed'])
    model.load_adapter(initial)
    data = rows(config['data'], 'support')
    if config['task_layout'] == 'all':
        slots = [('task', 1., data)]
    else:
        F = [r for r in data if r['fact_id'] in config['target_ids']]
        R = [r for r in data if r['fact_id'] not in config['target_ids']]
        slots = ([('F', .5, F)] if config['D'] == 'plus' else []) + [('R', .5, R)]
    streams = {name: FactStream(data, model.tokenizer, config['data_seed'], 128)
               for name, _, data in slots}
    streams['language'] = LanguageStream('data/language/inner.jsonl', model.tokenizer, 128, config['data_seed'])
    if config.get('retain_data'):
        streams['keep'] = FactStream(rows(config['retain_data'], 'support'), model.tokenizer,
                                     config['data_seed'], 128)
    if config.get('method') == 'factor_lr':
        groups = [{'params': [value for name, value in model.adapter(True).items() if name.endswith('.' + factor)],
                   'lr': config['inner_lr'] * (config['ba_ratio'] if factor == 'B' else 1.)}
                  for factor in ['A', 'B']]
    else:
        groups = list(model.adapter(True).values())
    optimizer = torch.optim.Adam(groups, lr=config['inner_lr'], foreach=False)
    for step in range(config['K']):
        optimizer.zero_grad(set_to_none=True)
        retain_routes = ([('language', .5 * config['retain_weight']), ('keep', .5 * config['retain_weight'])]
                         if 'keep' in streams else [('language', config['retain_weight'])])
        for name, mass in [(n, m) for n, m, _ in slots] + retain_routes:
            batches, count = route_batches(streams[name], 'cpu', config['effective_tokens'])
            for batch in batches:
                (mass * token_count(batch) / count * model(batch)).backward()
        torch.nn.utils.clip_grad_norm_(list(model.adapter(True).values()), 1.)
        if variance_floor:
            # An independent stock-Adam reference for the contract denominator
            # sqrt(v_hat + floor) + eps: add (1-beta2)*floor to the second
            # moment each step, including bias correction. Production does
            # not use this reference helper or this optimizer state.
            for group in optimizer.param_groups:
                beta2 = group['betas'][1]
                for parameter in group['params']:
                    state = optimizer.state[parameter]
                    if not state:
                        state.update(step=torch.tensor(0.), exp_avg=torch.zeros_like(parameter),
                                     exp_avg_sq=torch.zeros_like(parameter))
                    state['exp_avg_sq'].add_((1 - beta2) / beta2 * variance_floor)
        optimizer.step()
    return model.adapter_cpu()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/recovery_integration_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(197)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_dev_recovery_') as directory:
        root = Path(directory)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        modelpath = root / 'tiny_qwen'
        tiny = Qwen2ForCausalLM(Qwen2Config(vocab_size=len(tokenizer), hidden_size=8,
                                          intermediate_size=16, num_hidden_layers=1,
                                          num_attention_heads=1, num_key_value_heads=1,
                                          head_dim=8, attention_dropout=0.,
                                          eos_token_id=tokenizer.eos_token_id,
                                          pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(modelpath)
        tokenizer.save_pretrained(modelpath)
        del tiny
        source = rows('data/synthetic/dev_parent.jsonl')
        ids = sorted({r['fact_id'] for r in source})[:2]
        data = [r for r in source if r['fact_id'] in ids]
        datafile = root / 'dev_facts.jsonl'
        datafile.write_text(''.join(json.dumps(r) + '\n' for r in data))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, value in model.adapter().items():
                if name.endswith('.B'):
                    value.normal_(0, .01)
        adapter = model.adapter_cpu()
        parent_config = {'model': str(modelpath), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
                         'scale': 1., 'seq': 128, 'data': str(datafile), 'base_revision': 'random_cpu_fixture'}
        parent_dir = root / 'parent'
        parent_dir.mkdir()
        parent = parent_dir / 'latest.pt'
        atomic_save({'adapter': adapter, 'config': parent_config}, parent)
        (parent_dir / 'result.json').write_text(json.dumps({'acquisition_gate_passed': True,
                                                         'scope': 'TEST FIXTURE ONLY'}))
        query = [r for r in data if r['role'] == 'query' and r['sample_id'].endswith(':0')]
        qa = evaluate_qa(model, query, generate=False)
        batch = collate([qa_tokens(model.tokenizer, r) for r in query], model.tokenizer, 'cpu')
        torch.testing.assert_close(torch.tensor(qa['nll']), model(batch).detach(), atol=2e-6, rtol=2e-6)
        tests['candidate_correct_answer_NLL_equals_direct_masked_CE'] = 'PASS'
        del model
        plan = root / 'plan.json'
        plan.write_text(json.dumps({'status': 'DEVELOPMENT_PLAN', 'allowed_data': [str(datafile)],
                                    'allowed_parents': [str(parent)], 'lr_candidates': [.001],
                                    'K_candidates': [2], 'q_candidates': [.03],
                                    'noise_schemes': ['subspace']}))
        config = {'out': '', 'freeze_manifest': str(plan), 'phase': 'development', 'data_seed': 17,
                  'noise_seed': 1729, 'parent': str(parent), 'device': 'cpu', 'q_apply': .03,
                  'noise_scheme': 'subspace', 'architecture': 'L', 'method': 'standard_adam',
                  'data': str(datafile), 'target_ids': [ids[0]], 'retain_subset_fraction': 1.,
                  'subset_seed': 91, 'D': 'plus', 'task_layout': 'selective',
                  'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 32,
                  'inner_lr': .001, 'effective_tokens': 16, 'retain_weight': .25, 'K': 2,
                  'eval_steps': [2]}
        outputs = {}
        for label, layout, D in [('plus', 'selective', 'plus'), ('minus', 'selective', 'minus'),
                                 ('all', 'all', 'plus')]:
            c = dict(config, out=str(root / label), task_layout=layout, D=D)
            expected = direct_adam(c, parent_config, adapter)
            recovery.run_recovery(c)
            actual = torch.load(root / label / 'latest.pt', map_location='cpu', weights_only=False)
            for name in expected:
                torch.testing.assert_close(actual['parameters'][name], expected[name], atol=2e-6, rtol=2e-6)
            result = json.loads((root / label / 'result.json').read_text())
            assert result['coverage'] == result['pool']
            assert all(abs(x['residual'] - (x['injected'] - x['recovered'])) < 1e-10
                       for x in result['loss_changes'].values())
            outputs[label] = actual
            tests[f'{label}_two_steps_match_independent_stock_Adam'] = 'PASS'
        events = {}
        for label in ['plus', 'minus']:
            events[label] = [json.loads(s) for s in (root / label / 'events.jsonl').read_text().splitlines()]
            events[label] = [e for e in events[label] if e['event'] == 'recover']
        for plus, minus in zip(events['plus'], events['minus']):
            assert minus['route_tokens']['F'] == 0
            assert plus['route_tokens']['R'] == minus['route_tokens']['R']
            assert plus['route_tokens']['language'] == minus['route_tokens']['language']
        tests['Dminus_preserves_R_and_language_exposure_and_half_mass'] = 'PASS'
        c = dict(config, out=str(root / 'resume'), task_layout='all', eval_steps=[1, 2])
        original_save = recovery.atomic_save

        def interrupt_after_step_one(value, path):
            original_save(value, path)
            if value.get('step') == 1:
                raise InterruptedError('simulated interruption after atomic checkpoint')

        recovery.atomic_save = interrupt_after_step_one
        try:
            try:
                recovery.run_recovery(c)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected simulated interruption')
        finally:
            recovery.atomic_save = original_save
        recovery.run_recovery(c, resume=True)
        resumed = torch.load(root / 'resume/latest.pt', map_location='cpu', weights_only=False)
        for name in outputs['all']['parameters']:
            torch.testing.assert_close(resumed['parameters'][name], outputs['all']['parameters'][name], atol=0, rtol=0)
        resumed_result = json.loads((root / 'resume/result.json').read_text())
        assert resumed_result['initial'] is not None and resumed_result['loss_changes']
        tests['resume_restores_optimizer_streams_initial_metrics_bitwise'] = 'PASS'
        resumed_events = [json.loads(line) for line in (root / 'resume/events.jsonl').read_text().splitlines()]
        assert [event['step'] for event in resumed_events if event['event'] == 'evaluation'] == [0, 1, 2]
        assert next(i for i, event in enumerate(resumed_events) if event['event'] == 'evaluation' and event['step'] == 1) < next(
            i for i, event in enumerate(resumed_events) if event['event'] == 'recover' and event['step'] == 2)
        tests['resume_completes_pending_boundary_evaluation_before_next_update'] = 'PASS'
        norms = [e['update_norm'] / 2 for e in [json.loads(s) for s in (root / 'all/events.jsonl').read_text().splitlines()]
                 if e['event'] == 'recover']
        normfile = root / 'norms.json'
        normfile.write_text(json.dumps(norms))
        c = dict(config, out=str(root / 'norm_match'), task_layout='all', match_update_norms=str(normfile))
        snapshots = []

        def capture_parameters(value, path):
            original_save(value, path)
            state = value.get('parameters', value.get('adapter'))
            if state is not None:
                snapshots.append({name: v.detach().cpu().clone() for name, v in state.items()})

        recovery.atomic_save = capture_parameters
        try:
            recovery.run_recovery(c)
        finally:
            recovery.atomic_save = original_save
        actual_norms = [torch.linalg.vector_norm(torch.cat([(b[k] - a[k]).reshape(-1) for k in a]))
                        for a, b in zip(snapshots, snapshots[1:])]
        torch.testing.assert_close(torch.stack(actual_norms), torch.tensor(norms), atol=2e-7, rtol=2e-5)
        tests['fixed_update_norm_control'] = 'PASS'
        newfile = root / 'dev_new_task.jsonl'
        newfile.write_text(''.join(json.dumps(r) + '\n' for r in data if r['fact_id'] == ids[0]))
        keepfile = root / 'dev_known_facts.jsonl'
        keepfile.write_text(''.join(json.dumps(r) + '\n' for r in data if r['fact_id'] == ids[1]))
        current_plan = json.loads(plan.read_text())
        current_plan['allowed_data'] += [str(newfile), str(keepfile)]
        plan.write_text(json.dumps(current_plan))
        c = dict(config, out=str(root / 'new_task'), task_layout='all', data=str(newfile),
                 retain_data=str(keepfile))
        expected = direct_adam(c, parent_config, adapter)
        recovery.run_recovery(c)
        actual = torch.load(root / 'new_task/latest.pt', map_location='cpu', weights_only=False)
        for name in expected:
            torch.testing.assert_close(actual['parameters'][name], expected[name], atol=2e-6, rtol=2e-6)
        new_result = json.loads((root / 'new_task/result.json').read_text())
        assert new_result['keep_coverage'] == {'covered': 1, 'pool': 1}
        assert new_result['final']['task']['n'] == new_result['final']['keep']['n'] == 1
        tests['new_task_half_language_half_known_retention_matches_independent_Adam'] = 'PASS'
        from gaosi.transition import Phi
        phi_file = root / 'untrained_phi.pt'
        atomic_save({'phi': Phi('L').state_dict()}, phi_file)
        current_plan.update(status='PHI_DEVELOPMENT_PLAN', allowed_phis={
            str(phi_file): hashlib.sha256(phi_file.read_bytes()).hexdigest()})
        phiplan = root / 'phi_plan.json'
        phiplan.write_text(json.dumps(current_plan))
        learned = dict(c, out=str(root / 'phi_dev'), method='phi', phi=str(phi_file),
                       phase='phi_development', freeze_manifest=str(phiplan))
        recovery.run_recovery(learned)
        paired = dict(learned, out=str(root / 'paired_dev'), method='paired_adam')
        recovery.run_recovery(paired)
        a = torch.load(root / 'phi_dev/latest.pt', map_location='cpu', weights_only=False)
        b = torch.load(root / 'paired_dev/latest.pt', map_location='cpu', weights_only=False)
        for name in a['parameters']:
            torch.testing.assert_close(a['parameters'][name], b['parameters'][name], atol=0, rtol=0)
        tests['declared_dev_phi_matches_paired_Adam_at_zero_head'] = 'PASS'
        current_plan['allowed_phis'][str(phi_file)] = 'invalid_hash'
        phiplan.write_text(json.dumps(current_plan))
        try:
            recovery.run_recovery(dict(learned, out=str(root / 'undeclared_phi')))
        except ValueError as error:
            assert 'phi hash differs' in str(error)
        else:
            raise AssertionError('Undeclared development checkpoint must be rejected')
        tests['unlisted_dev_phi_rejected'] = 'PASS'
        from build_rule_development import generate
        rule_rows, _ = generate('dev_cpu_rule', 37)
        rulefile = root / 'dev_rule.jsonl'
        rulefile.write_text(''.join(json.dumps(r) + '\n' for r in rule_rows))
        current_plan['allowed_data'].append(str(rulefile))
        current_plan['status'] = 'DEVELOPMENT_PLAN'
        plan.write_text(json.dumps(current_plan))
        recovery.run_recovery(dict(c, out=str(root / 'rule'), data=str(rulefile)))
        rule_result = json.loads((root / 'rule/result.json').read_text())
        assert rule_result['final']['task']['n'] == 4
        tests['all_disjoint_rule_queries_are_evaluated'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU tiny random Qwen integration only; no acquisition or recovery effect evidence',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
