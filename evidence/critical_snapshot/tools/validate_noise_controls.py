"""Tiny real CPU controls plus isolated complete schedule/permission fixtures."""
import argparse
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

import gaosi.noise_controls as controls
from gaosi.model import AdapterModel
from gaosi.scale import sha256
from gaosi.transition import Phi
from gaosi.transition import rms, scalar_stats
from gaosi.noise import canonical_noise
import gaosi.recover as recovery
from validate_meta_resume import same_tree
from device_guard import uses_musa
import noise_controls_queue as queue


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2))


def reject(call, fragment):
    try:
        call()
    except ValueError as error:
        assert fragment in str(error), str(error)
    else:
        raise AssertionError('Invalid noise diagnostic was accepted')


def fixture(K, model='SIMULATED_MODEL_NOT_LOADED', adapter=None):
    for p in ['configs', 'records', 'checks', 'runs', 'data/language', 'data/synthetic']:
        Path(p).mkdir(parents=True, exist_ok=True)
    for part in ['inner', 'dev']:
        Path(f'data/language/{part}.jsonl').write_text(json.dumps(
            {'document_id': part, 'text': ' '.join(['alpha', 'beta', 'gamma'] * 400)}) + '\n')
    file = 'data/synthetic/dev_fixture.jsonl'
    values = [{'world_id': 'dev_fixture', 'fact_id': f'dev:{i}', 'sample_id': f'dev:{role}:{i}:0',
               'role': role, 'kind': 'fact', 'prompt': 'alpha beta',
               'answer': ' '.join(['gamma'] * 64),
               'candidates': [' '.join(['gamma'] * 64), ' '.join(['beta'] * 64)]}
              for role in ['support', 'query'] for i in range(2)]
    Path(file).write_text(''.join(json.dumps(v) + '\n' for v in values))
    parent = 'runs/dev_parent/latest.pt'
    Path(parent).parent.mkdir()
    pc = {'model': model, 'rank': 2, 'targets': 'all7', 'organization': 'O1', 'scale': 1.,
          'seq': 512, 'data': file, 'base_revision': 'RANDOM_CPU_FIXTURE'}
    torch.save({'adapter': adapter or {'fixture': torch.tensor([.1])}, 'config': pc}, parent)
    result = {'status': 'PASS', 'acquisition_gate_passed': True, 'checkpoint': parent, 'config': pc,
              'scope': 'RANDOM_UNTRAINED_CPU_FIXTURE_ONLY'}
    write('runs/dev_parent/result.json', result)
    write('records/parents_ready.json', [result])
    base = {'status': 'FROZEN', 'organization': 'O1', 'stopping_rule_frozen': True,
            'formal_data_used': False, 'architecture': 'L', 'recovery_K': K, 'recovery_lr': .0003,
            'q_medium': .7, 'development_parent': parent}
    files = {}
    for label, noise in [('O1', 'mixed'), ('O1_clean', 'clean')]:
        file = f'runs/{noise}_phi.pt'
        torch.manual_seed(923 if noise == 'mixed' else 924)
        phi = Phi('L')
        with torch.no_grad():
            phi.net[-1].bias.copy_(torch.tensor([.07, -.03]))
        torch.save({'phi': phi.state_dict(), 'scope': 'RANDOM_TINY_FIXTURE_NOT_TRAINED'}, file)
        frozen = dict(base, training_noise=noise, phi=file, phi_sha256=sha256(file))
        path = f'records/frozen_{label}.json'
        write(path, frozen)
        files[label] = path
    write('records/all_phi_frozen.json', {'manifests': files})
    write('records/calibration_frozen.json', {'status': 'CALIBRATION_FROZEN', 'formal_data_used': False,
                                             'ordinary_recovery_validated': True})
    write('checks/noise_controls_cpu.json', {'status': 'PASS', 'scope': 'ISOLATED_FIXTURE_GATE'})


def dispatch(c):
    old = sys.argv
    sys.argv = ['gaosi.noise_controls', '--config', f'configs/{Path(c["out"]).name}.json']
    try:
        controls.main()
    finally:
        sys.argv = old


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='checks/noise_controls_cpu.json')
    args = p.parse_args()
    output, original = Path(args.out).resolve(), Path.cwd()
    torch.set_num_threads(2)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_noise_controls_') as directory, ExitStack() as runtime_patches:
        try:
            os.chdir(directory)
            fixture(32)
            calls = []
            def simulated(name, command, resultfile):
                c = json.loads(Path(command[-1]).read_text())
                controls.check_config(c)
                calls.append(c)
                out = Path(c['out'])
                out.mkdir(parents=True)
                torch.save({'scope': 'SIMULATED_INITIAL'}, out / 'initial.pt')
                torch.save({'scope': 'SIMULATED_ENDPOINT'}, out / 'latest.pt')
                norms = json.loads(Path(c['match_update_norms']).read_text()) if c.get('match_update_norms') else [1.] * c['K']
                events = [{'event': 'evaluation', 'step': 0, 'metrics': {'scope': 'SIMULATED'}}]
                events += [{'event': 'recover', 'step': i + 1, 'update_norm': v, 'step_seconds': 1.}
                           for i, v in enumerate(norms)]
                events += [{'event': 'evaluation', 'step': c['K'], 'metrics': {'scope': 'SIMULATED'}}]
                (out / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
                result = {'status': 'PASS', 'config': c, 'checkpoint': str(out / 'latest.pt'),
                          'initial': {}, 'clean': {}, 'final': {}, 'loss_changes': {},
                          'noise': {}, 'coverage': 512, 'pool': 512, 'base_unchanged': True, 'compute_counters': {}}
                write(resultfile, result)
                return result
            with patch.object(queue, 'run_job', simulated):
                queue.main()
            assert len(calls) == 18 and all(c['K'] == 32 and c['noise_seed'] == 1729 for c in calls)
            for scheme in queue.POLICY['schemes']:
                group = [c for c in calls if c['noise_scheme'] == scheme and not c.get('match_update_norms')]
                assert len(group) == 4 and all(c['initial_adapter'] == str(Path(group[0]['out']) / 'initial.pt') for c in group[1:])
            assert len([c for c in calls if c.get('match_update_norms')]) == 2
            tests['complete_18_arm_actual_primary_K32_and_within_scheme_same_initial_bytes'] = 'PASS'
            ready = json.loads(Path('records/noise_controls_ready_v1.json').read_text())
            assert not ready['distribution_superiority_established'] and not ready['overall_experiment_complete']
            assert all(not c.get('retain_data') for c in calls)
            tests['diagnostic_retains_original_known_support_recipe_and_no_superiority_claim'] = 'PASS'
            c = calls[0]
            entrypath = c['noise_control_entry']
            declaration = json.loads(Path(entrypath).read_text())
            for updates, fragment in [({'K': 64}, 'primary horizon'), ({'q_apply': .3}, 'primary horizon'),
                                       ({'data': 'data/synthetic/final.jsonl'}, 'primary horizon'),
                                       ({'inner_lr': .001}, 'primary horizon')]:
                altered = dict(c, **updates)
                write(entrypath, dict(declaration, config=altered))
                reject(lambda: controls.check_config(altered), fragment)
            write(entrypath, declaration)
            tests['new_rates_doses_test_data_and_K64_substitution_rejected'] = 'PASS'
            text = Path(c['data']).read_text()
            changed = [dict(json.loads(line), world_id='final_FORBIDDEN') for line in text.splitlines()]
            Path(c['data']).write_text(''.join(json.dumps(r) + '\n' for r in changed))
            bad = copy.deepcopy(declaration)
            bad['data_sha256'][c['data']] = sha256(c['data'])
            write(entrypath, bad)
            reject(lambda: controls.check_config(c), 'development worlds')
            Path(c['data']).write_text(text)
            write(entrypath, declaration)
            tests['final_rows_rejected_even_with_updated_hashes'] = 'PASS'
            frozen_path = declaration['phi_freezes']['mixed']
            frozen_text = Path(frozen_path).read_text()
            write(frozen_path, dict(json.loads(frozen_text), status='INVALID'))
            Path(c['data']).unlink()
            reject(lambda: controls.check_config(c), 'freeze bytes changed')
            Path(c['data']).write_text(text)
            Path(frozen_path).write_text(frozen_text)
            tests['Adam_validates_frozen_rules_before_opening_task_data'] = 'PASS'
            result = json.loads(Path(c['out'], 'result.json').read_text())
            trace = Path(c['out'], 'events.jsonl')
            original_trace = trace.read_text()
            trace.write_text(original_trace + json.dumps({'event': 'recover', 'step': 1, 'update_norm': 1.}) + '\n')
            reject(lambda: queue.norms(result), 'exactly once')
            trace.write_text(original_trace)
            assert not uses_musa(['/usr/bin/python', 'tools/noise_controls_queue.py'])
            write('configs/musa_fixture.json', {'device': 'musa'})
            assert uses_musa(['/usr/bin/python', '-m', 'gaosi.noise_controls', '--config', 'configs/musa_fixture.json'])
            tests['complete_norm_sequence_required_and_only_MUSA_leaf_gets_device_lock'] = 'PASS'

            # Real all18 trajectory execution with a declared two-step random
            # tiny CPU fixture, using production recovery and eager Qwen math.
            root = Path(directory) / 'real'
            root.mkdir()
            os.chdir(root)
            impl = Tokenizer(WordLevel({'[UNK]': 0, '[EOS]': 1, 'alpha': 2, 'beta': 3, 'gamma': 4}, unk_token='[UNK]'))
            impl.pre_tokenizer = Whitespace()
            tokenizer = PreTrainedTokenizerFast(tokenizer_object=impl, unk_token='[UNK]', eos_token='[EOS]', pad_token='[EOS]')
            tokenizer.model_input_names = ['input_ids', 'attention_mask']
            runtime_patches.enter_context(patch('gaosi.model.AutoTokenizer.from_pretrained', return_value=tokenizer))
            modelpath = str(root / 'tiny_qwen')
            torch.manual_seed(927)
            tiny = Qwen2ForCausalLM(Qwen2Config(vocab_size=5, hidden_size=8, intermediate_size=16,
                num_hidden_layers=1, num_attention_heads=1, num_key_value_heads=1, head_dim=8,
                eos_token_id=1, pad_token_id=1, attention_dropout=0.))
            tiny.save_pretrained(modelpath)
            tokenizer.save_pretrained(modelpath)
            del tiny
            model = AdapterModel(modelpath, 2, 'all7', 'O1', 'cpu')
            with torch.no_grad():
                for name, value in model.adapter().items():
                    if name.endswith('.B'):
                        value.normal_(0, .03)
            adapter, specs = model.adapter_cpu(), model.specs
            for scheme in queue.POLICY['schemes']:
                corrupted, diagnostics = canonical_noise(adapter, specs, .7, 1729, scheme)
                dense_damage, dense_clean = 0., 0.
                for name, spec in specs.items():
                    if spec.factor != 'A':
                        continue
                    clean_dense = spec.scale * adapter[spec.pair_name].double() @ adapter[name].double()
                    noisy_dense = spec.scale * corrupted[spec.pair_name].double() @ corrupted[name].double()
                    dense_damage += float((noisy_dense - clean_dense).square().sum())
                    dense_clean += float(clean_dense.square().sum())
                assert abs(dense_damage - diagnostics['composed_damage_squared_raw']) < 1e-10
                assert abs(dense_clean - diagnostics['composed_clean_squared_raw']) < 1e-10
            tests['reported_effective_weight_damage_equals_independent_dense_BA_difference_all_schemes'] = 'PASS'
            del model
            fixture(2, modelpath, adapter)
            actual_calls = []
            real_save = recovery.atomic_save
            applied_snapshots = {}
            def capture(value, path):
                real_save(value, path)
                if value.get('config', {}).get('match_update_norms') and 'parameters' in value:
                    applied_snapshots.setdefault(value['config']['out'], []).append(copy.deepcopy(value))
            def real_job(name, command, resultfile):
                c = json.loads(Path(command[-1]).read_text())
                assert c['device'] == 'cpu'
                dispatch(c)
                actual_calls.append(c)
                return json.loads(Path(resultfile).read_text())
            with patch.object(queue, 'run_job', real_job), patch.object(recovery, 'atomic_save', capture):
                queue.main(device='cpu')
            assert len(actual_calls) == 18
            ready = json.loads(Path('records/noise_controls_ready_v1.json').read_text())
            assert all(r['base_unchanged'] and r['coverage'] == r['pool'] for r in ready['outcomes'])
            assert len([r for r in ready['outcomes'] if r.get('max_update_norm_relative_error', 1) < 2e-4]) == 2
            tests['real_18_tiny_CPU_trajectories_production_entry_recovery_frozen_phi_and_full_coverage'] = 'PASS'
            tests['real_phi_and_shrink_controls_match_stock_global_update_norm_each_step'] = 'PASS'
            phi_norm = next(c for c in actual_calls if c.get('match_update_norms') and c['method'] == 'phi')
            previous = torch.load(phi_norm['initial_adapter'], map_location='cpu', weights_only=False)['adapter']
            clean, _ = canonical_noise(adapter, specs, 0., 1729)
            stats = {k: scalar_stats(v) for k, v in clean.items()}
            for snapshot in applied_snapshots[phi_norm['out']]:
                for name, history in snapshot['inner_state']['history'].items():
                    expected = rms(snapshot['parameters'][name] - previous[name]) / (stats[name]['rms_floor'] + 1e-12)
                    torch.testing.assert_close(history['last_update'], expected, rtol=0, atol=0)
                previous = snapshot['parameters']
            tests['norm_matched_phi_next_feature_records_actual_scaled_and_rounded_update'] = 'PASS'
            # Interrupt the matched learned rule between saving/evaluation;
            # resume must restore its corrected history as well as parameters.
            def interrupted_job(name, command, resultfile):
                config = json.loads(Path(command[-1]).read_text())
                def interrupt(value, path):
                    real_save(value, path)
                    if value.get('step') == 1:
                        raise InterruptedError('Norm-matched tiny fixture boundary')
                with patch.object(recovery, 'atomic_save', interrupt):
                    try:
                        dispatch(config)
                    except InterruptedError:
                        pass
                recovery.run_recovery(config, resume=True)
                return json.loads(Path(resultfile).read_text())
            with patch.object(queue, 'run_job', interrupted_job):
                resumed_result = queue.entry('norm_matched_phi_resumed_fixture', phi_norm,
                    json.loads(Path(phi_norm['noise_control_entry']).read_text())['phi_freezes'],
                    ['records/calibration_frozen.json', 'records/noise_controls_policy_v1.json'])
            uninterrupted = torch.load(Path(phi_norm['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            resumed = torch.load(resumed_result['checkpoint'], map_location='cpu', weights_only=False)
            for key in ['parameters', 'inner_state', 'streams', 'step', 'base_digest']:
                same_tree(resumed[key], uninterrupted[key])
            tests['norm_matched_phi_resume_restores_parameters_moments_history_streams_bitwise'] = 'PASS'
            # Every paired method really consumed the same initial tensors.
            for scheme in queue.POLICY['schemes']:
                group = [c for c in actual_calls if c['noise_scheme'] == scheme]
                first = torch.load(Path(group[0]['out']) / 'initial.pt', map_location='cpu', weights_only=False)['adapter']
                for c in group[1:]:
                    initial = torch.load(c.get('initial_adapter', str(Path(c['out']) / 'initial.pt')),
                                         map_location='cpu', weights_only=False)['adapter']
                    assert all(torch.equal(first[k], initial[k]) for k in first)
            tests['real_initial_adapter_tensors_identical_within_each_scheme'] = 'PASS'
        finally:
            os.chdir(original)
    value = {'status': 'PASS', 'scope': 'Isolated K32 schedule plus real random tiny CPU K2 numerical/entry checks; not real3B/MUSA or trained-phi effect evidence',
             'checks': tests, 'count': len(tests)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
