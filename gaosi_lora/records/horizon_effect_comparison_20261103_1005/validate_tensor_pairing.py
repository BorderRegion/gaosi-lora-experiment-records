"""Exercise CPU tensor/stream pairing gates with tiny fixtures, never a GPU."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile

import torch

spec = importlib.util.spec_from_file_location('horizon_pairing', Path(__file__).with_name('compare.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def reject(function):
    try:
        function()
    except ValueError:
        return
    raise AssertionError('Unpaired tensor fixture was accepted')


def main():
    checks = []
    original = Path.cwd()
    validate_result = m.validate_result
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.chdir(folder)
            inputs = {'parent': 'runs/dev_parent/latest.pt', 'retain_data': 'data/dev_parent.jsonl',
                      'recovery_lr': .0003, 'q_medium': .7,
                      'tasks': [{'group': 'known', 'data': 'data/dev_parent.jsonl'}]}
            pair = m.pairs(inputs)[1]
            initial = {k: pair['H1'][k] for k in ['parent', 'noise_seed', 'noise_scheme', 'q_apply']}
            initial.update(organization='O1', adapter={'weight': torch.tensor([1., 2.])})
            metric = {'task': {'candidate_accuracy': .5, 'strict_EM': .25},
                      'language': {'nll': 2.}, 'keep': {'nll': 1.}}
            evaluations = [{'step': k, 'metrics': copy.deepcopy(metric)} for k in [0, 8, 16, 32, 64]]
            steps = [{'route_tokens': {'task': 512, 'keep': 512, 'language': 512}, 'step_seconds': .01} for _ in range(64)]
            result = {'base_digest': 'fixture_base', 'noise': {'q': .7}, 'initial': metric,
                      'clean': metric, 'compute_counters': {'loss_forwards': 192}}
            values = {c['out']: [copy.deepcopy(result), copy.deepcopy(evaluations), copy.deepcopy(steps)]
                      for c in [pair['H2'], pair['H1']]}
            # Ledger/config rejection is covered separately in validate.py.
            # Here isolate actual CPU checkpoint and cross-run pairing checks.
            m.validate_result = lambda c, inputs: values[c['out']]
            for c in [pair['H2'], pair['H1']]:
                out = Path(c['out'])
                out.mkdir(parents=True)
                for name in ['result.json', 'config.json', 'source_sha256.json']:
                    write(out / name, {})
                (out / 'events.jsonl').write_text('{}\n')
                torch.save({'step': 64, 'config': c, 'streams': {'task': {'cursor': 100, 'seed': 17}}}, out / 'latest.pt')
                write('records/jobs/' + out.name + '.json', {'wall_seconds': 1.})
            torch.save(initial, pair['H1']['initial_adapter'])
            report = m.report_pair(pair, inputs)
            assert all(c['H2_minus_H1'] == 0 for c in report['curves'])
            checks.append('accept_identical_CPU_initial_and_stream_pair')
            changed = copy.deepcopy(initial)
            changed['noise_seed'] = 29
            torch.save(changed, pair['H1']['initial_adapter'])
            reject(lambda: m.report_pair(pair, inputs))
            checks.append('reject_changed_initial_noise_metadata')
            torch.save(initial, pair['H1']['initial_adapter'])
            path = Path(pair['H1']['out']) / 'latest.pt'
            saved = torch.load(path, weights_only=False, map_location='cpu')
            changed = copy.deepcopy(saved)
            changed['streams']['task']['cursor'] += 1
            torch.save(changed, path)
            reject(lambda: m.report_pair(pair, inputs))
            checks.append('reject_changed_saved_stream_cursor')
            torch.save(saved, path)
            values[pair['H1']['out']][2][0]['route_tokens']['task'] = 513
            reject(lambda: m.report_pair(pair, inputs))
            checks.append('reject_changed_per_step_effective_tokens')
            values[pair['H1']['out']][2][0]['route_tokens']['task'] = 512
            values[pair['H1']['out']][0]['initial']['language']['nll'] += .01
            reject(lambda: m.report_pair(pair, inputs))
            checks.append('reject_changed_initial_evaluation')
            assert m.tensor_digest({'w': torch.tensor([1., 2.])}) != m.tensor_digest({'w': torch.tensor([1., 3.])})
            checks.append('tensor_digest_changes_with_adapter_bytes')
        finally:
            os.chdir(original)
            m.validate_result = validate_result
    result = {'status': 'PASS', 'scope': 'tiny_CPU_tensor_pairing_fixtures_only',
              'checks': checks, 'GPU_used': False, 'method_validated': False}
    m.preserve(Path(__file__).with_name('tensor_validation.json'), result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
