"""CPU audit of the saved gradient-mode probe; no model or tokenizer forward."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import torch


def main():
    torch.set_num_threads(2)
    root = Path('/root/autodl-tmp/gaosi_lora')
    directory = root / 'records/horizon_uniform_numerics_20261109_2330'
    target = directory / 'probe_tensor_audit.json'
    assert not target.exists()
    run = root / 'runs/horizon_support_modes_K4_boundary3_s17_observed_blocking_v8'
    result = json.loads((run / 'result.json').read_text())
    jobpath = root / 'records/jobs' / (run.name + '.json')
    job = json.loads(jobpath.read_text())
    assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
    assert not result['first_exact_transition_executed'] and result['optimizer_updates'] == 0
    assert result['config']['K'] == 4 and result['config']['tail'] == 2
    assert result['config']['effective_tokens'] == result['config']['seq'] == 512
    assert result['original_frozen_config'] == json.loads((root / 'configs/horizon_known_K4_H2_s17_observed_blocking_v8.json').read_text())
    assert result['probe_source_sha256'] == hashlib.sha256((root / 'records/horizon_numerical_diagnosis_20261109_2200/probe.py').read_bytes()).hexdigest()
    for name, expected in result['production_source_sha256'].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    pairs_path = run / 'worst_gradient_pairs.pt'
    pairs = torch.load(pairs_path, map_location='cpu', weights_only=False)
    assert set(pairs) == {'task', 'language', 'keep'}
    reviewed = []
    adapters = set()
    for route in result['routes']:
        label = route['route']
        pair = pairs[label]
        name = pair['parameter']
        first, exact = pair['first_order'], pair['recomputed']
        assert first.device.type == exact.device.type == 'cpu'
        assert first.shape == exact.shape and first.dtype == exact.dtype
        assert torch.isfinite(first).all() and torch.isfinite(exact).all()
        assert len({route[key] for key in ['loss_first_a', 'loss_first_b', 'loss_exact_a', 'loss_exact_b']}) == 1
        for group in ('first_order_repeat', 'recomputed_repeat'):
            repeat = route[group]
            assert repeat['all_bitwise_equal'] and repeat['all_strict_close']
            assert repeat['max_abs'] == repeat['relative_l2'] == 0
            assert len(repeat['per_parameter']) == 504
            assert all(item['bitwise_equal'] and item['max_abs'] == 0 for item in repeat['per_parameter'].values())
        cross = route['first_order_vs_recomputed']
        delta = first.double() - exact.double()
        absolute = float(delta.abs().max())
        relative = float(delta.square().sum() / first.double().square().sum()) ** .5
        observed = cross['per_parameter'][name]
        assert math.isclose(absolute, observed['max_abs'], rel_tol=1e-14)
        assert math.isclose(relative, observed['relative_l2'], rel_tol=1e-14)
        assert absolute == cross['max_abs']
        assert not torch.equal(first, exact)
        assert not torch.allclose(first.double(), exact.double(), atol=1e-6, rtol=1e-5)
        assert not cross['all_strict_close'] and not cross['all_bitwise_equal']
        adapters.add(route['adapter_sha256'])
        reviewed.append({'route': label, 'saved_worst_parameter': name,
                         'worst_tensor_max_abs_independently_recomputed': absolute,
                         'worst_tensor_relative_l2_independently_recomputed': relative,
                         'reported_all_parameter_relative_l2': cross['relative_l2'],
                         'reported_nonclose_parameters': sum(not item['strict_close'] for item in cross['per_parameter'].values()),
                         'same_mode_repeat_bitwise_equal_in_probe_report': True})
    assert len(adapters) == 1
    for key in ('first_order', 'recomputed'):
        assert torch.equal(pairs['task'][key], pairs['keep'][key])
    paths = [Path(__file__).resolve(), run / 'result.json', pairs_path, run / 'events.jsonl',
             jobpath, root / 'records/logs' / (run.name + '.log'),
             root / 'records/horizon_numerical_diagnosis_20261109_2200/probe.py']
    paths += [root / name for name in result['production_source_sha256']]
    evidence = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    value = {'status': 'SAVED_PROBE_WORST_GRADIENT_PAIRS_INDEPENDENTLY_AUDITED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'routes': reviewed, 'shared_boundary_adapter_sha256': next(iter(adapters)),
             'evidence_sha256': evidence, 'method_validated': False, 'full_goal_complete': False,
             'limitations': ['Only the saved worst tensor pair per route is independently compared; whole-route and repeat statistics are from the executed probe report.',
                            'No low-level operator root cause or H2/full derivative correctness is established.',
                            'Task and keep use identical batches in this known-parent diagnostic.']}
    with target.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'routes': reviewed,
                      'audit_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
