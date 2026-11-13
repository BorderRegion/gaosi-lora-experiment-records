"""Review all twelve saved uniform-path replays on CPU after the queue completes.

This never launches a replay, changes a tolerance, or publishes a promotion.
Missing full derivatives remain unavailable and cannot establish H2 accuracy.
"""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import torch
    from gaosi.horizon import compare_trees, gradient_comparison, limitation, tree_digest
    torch.set_num_threads(2)
    target = HERE / 'completed_saved_tensor_review.json'
    assert not target.exists(), 'Preserve any existing review'
    evidence = {}

    def remember(path):
        path = path.resolve()
        assert path.is_relative_to(ROOT.resolve())
        evidence[str(path.relative_to(ROOT))] = sha(path)
        return path

    plan_path = remember(HERE / 'plan.json')
    plan = read(plan_path)
    assert plan['status'] == 'DECLARED_UNIFORM_SUPPORT_NUMERICS_DEVELOPMENT_DIAGNOSTIC'
    assert len(plan['configs']) == 12
    for name, expected in plan['source_sha256'].items():
        assert sha(remember(ROOT / name)) == expected, name
    original_path = remember(ROOT / plan['original_plan'])
    assert sha(original_path) == plan['original_plan_sha256']
    original_plan = read(original_path)
    assert original_plan['status'] == 'FROZEN_DEVELOPMENT_HORIZON_PLAN'
    assert original_plan['formal_data_used'] is False
    assert [entry['original'] for entry in plan['configs']] == original_plan['configs']
    for group in ('artifact_sha256', 'data_sha256'):
        for name, expected in original_plan[group].items():
            assert sha(remember(ROOT / name)) == expected, name

    initial_path = remember(HERE / 'initial_saved_tensor_review.json')
    initial = read(initial_path)
    assert initial['status'] == 'UNIFORM_H1_H2_SAVED_ENDPOINT_GATE_INDEPENDENTLY_PASSED'
    assert initial['remaining_ten_permitted_by_this_gate']
    for name, expected in initial['evidence_sha256'].items():
        assert sha(remember(ROOT / name)) == expected, name
    started = read(remember(HERE / 'remaining_queue_started.json'))
    finished = read(remember(HERE / 'remaining_queue_completed.json'))
    assert started['status'] == 'TEN_UNIFORM_REPLAYS_STARTED_AFTER_INDEPENDENT_SAVED_TENSOR_GATE'
    assert started['indices'] == list(range(2, 12))
    assert started['initial_review_sha256'] == sha(initial_path) == finished['initial_review_sha256']
    assert started['plan_sha256'] == sha(plan_path)
    assert started['queue_source_sha256'] == sha(remember(HERE / 'remaining_queue.py'))
    assert finished['status'] == 'TEN_UNIFORM_REPLAYS_EXECUTED_PENDING_INDEPENDENT_REVIEW'
    assert [item['index'] for item in finished['jobs']] == list(range(2, 12))
    for item in finished['jobs']:
        saved = read(remember(HERE / ('remaining_index_' + str(item['index']) + '.json')))
        assert item == saved
        out = ROOT / plan['configs'][item['index']]['uniform']['out']
        assert saved['job'] == out.name
        assert saved['result_sha256'] == sha(remember(out / 'result.json'))
        assert saved['job_sha256'] == sha(remember(ROOT / 'records/jobs' / (out.name + '.json')))

    # Retain the original mixed-path failure rather than rewriting it as PASS.
    incident_path = remember(ROOT / 'records/pipeline_recovery_20261109_2152/incident_review.json')
    incident = read(incident_path)
    original_failed = ['horizon_known_K4_H2_s17_observed_blocking_v8',
                       'horizon_queue_observed_blocking_v8']
    for name in original_failed:
        group = {p: h for p, h in incident['remote_expected_sha256'].items()
                 if name in p and '/observer_' not in p and '/follower_' not in p}
        assert group
        for path, expected in group.items():
            assert sha(remember(ROOT / path)) == expected, path
        assert read(ROOT / 'records/jobs' / (name + '.json'))['status'] == 'FAIL'

    def finite(tree):
        if torch.is_tensor(tree):
            return tree.device.type == 'cpu' and bool(torch.isfinite(tree).all())
        if isinstance(tree, dict):
            return all(finite(value) for value in tree.values())
        if isinstance(tree, (tuple, list)):
            return all(finite(value) for value in tree)
        return not isinstance(tree, float) or math.isfinite(tree)

    phi_paths = {entry['uniform']['phi'] for entry in plan['configs']}
    assert len(phi_paths) == 1
    phi = torch.load(ROOT / next(iter(phi_paths)), map_location='cpu', weights_only=False)
    assert finite(phi['phi'])
    parameter_count = sum(value.numel() for value in phi['phi'].values())
    del phi
    rows, groups = [], []
    unavailable = ('NOT_AVAILABLE_BACKEND', 'NOT_AVAILABLE_MEMORY')
    for offset in range(0, 12, 3):
        entries = plan['configs'][offset:offset + 3]
        configs = [entry['uniform'] for entry in entries]
        K = configs[0]['K']
        assert [config['tail'] for config in configs] == [1, 2, K]
        assert K in (4, 8)
        common = {key: value for key, value in configs[0].items()
                  if key not in ('out', 'tail', 'reference_endpoint')}
        endpoints, gradients = {}, {}
        group_rows = []
        for index, entry in enumerate(entries, offset):
            config, original = entry['uniform'], entry['original']
            tail = config['tail']
            assert {key: value for key, value in config.items()
                    if key not in ('out', 'tail', 'reference_endpoint')} == common
            assert config.keys() == original.keys()
            assert {key for key in config if config[key] != original[key]} <= {'out', 'reference_endpoint'}
            reference = str(Path(configs[0]['out']) / 'endpoint.pt') if tail != 1 else None
            assert config['reference_endpoint'] == reference
            run = ROOT / config['out']
            result = read(remember(run / 'result.json'))
            job = read(remember(ROOT / 'records/jobs' / (run.name + '.json')))
            remember(ROOT / 'records/logs' / (run.name + '.log'))
            assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
            assert job['device_lock'] == {'path': '/tmp/gaosi_musa_device0.lock', 'version': 1}
            assert job['command'] == ['/usr/bin/env', 'MUSA_LAUNCH_BLOCKING=1', '/usr/bin/python', '-u',
                                      str((HERE / 'run.py').relative_to(ROOT)), '--index', str(index)]
            assert result['config'] == config == read(remember(run / 'config.json'))
            assert result['original_frozen_config'] == original
            assert result['diagnostic_variant'] == 'uniform_recomputed_support_forward_v1'
            assert result['training_settings_changed'] is False and result['production_source_modified'] is False
            status = result['diagnostic_status']
            assert status == 'COMPLETE' or status in unavailable, status
            if index >= 2:
                assert finished['jobs'][index - 2]['diagnostic_status'] == status
            row = {'index': index, 'run': config['out'], 'tail': tail, 'K': K,
                   'diagnostic_status': status, 'derivative_obtained': status == 'COMPLETE'}
            if status in unavailable:
                assert tail != 1, 'A missing H1 cannot be used as reference'
                assert result['derivative_obtained'] is False
                assert limitation(RuntimeError(result['error']), config['device']) == status
                assert result['traceback']
                # A partially saved endpoint is evidence, never a substitute gradient.
                for name in ('endpoint.pt', 'gradient.pt', 'events.jsonl'):
                    if (run / name).exists():
                        remember(run / name)
                row['reason'] = result['error']
            else:
                assert result['base_unchanged'] and result['phi_unchanged'] and result['optimizer_updates'] == 0
                assert result['plan_sha256'] == sha(plan_path)
                assert result['same_original_mixed_path_numerical_trajectory'] is False
                assert result['original_diagnostic_fixed_or_revalidated'] is False
                assert result['method_validated'] is False
                calls = result['support_mode_calls']
                exact_calls = 0 if tail == 1 else 3 * tail
                assert calls == {'detached_prefix_or_H1': 3 * K - exact_calls, 'exact_tail': exact_calls}
                events = [json.loads(line) for line in remember(run / 'events.jsonl').read_text().splitlines()]
                inner = [event for event in events if event['event'] == 'inner']
                assert [event['k'] for event in inner] == list(range(1, K + 1))
                expected_flags = [False] * K if tail == 1 else [False] * (K - tail) + [True] * tail
                assert [event['exact_window'] for event in inner] == expected_flags
                assert all(event[key] >= config['effective_tokens'] for event in inner
                           for key in ('task_tokens', 'keep_tokens', 'language_tokens'))
                assert all(math.isfinite(event[key]) for event in inner for key in ('ld', 'lr', 'step_seconds'))
                endpoint = torch.load(remember(run / 'endpoint.pt'), map_location='cpu', weights_only=False)
                saved = torch.load(remember(run / 'gradient.pt'), map_location='cpu', weights_only=False)
                assert finite(endpoint) and finite(saved)
                assert saved['config'] == config and saved['terminal'] == result['terminal']
                assert tree_digest(endpoint) == saved['endpoint_sha256'] == result['endpoint_sha256']
                gradient = saved['gradient']
                assert gradient.ndim == 1 and gradient.numel() == parameter_count
                assert math.isclose(float(gradient.double().norm()), result['gradient_norm'], rel_tol=1e-14)
                assert all(endpoint['terminal'][key].item() == value for key, value in result['terminal'].items())
                if tail == 1:
                    assert result['endpoint_matches_H1'] is None
                    endpoints[1] = endpoint
                else:
                    comparison = compare_trees(endpoint, endpoints[1], atol=1e-6, rtol=1e-5)
                    assert comparison == result['endpoint_matches_H1']
                    row['full_state_and_sample_flow_comparison_to_H1'] = comparison
                gradients[tail] = gradient
                row.update(gradient_norm=result['gradient_norm'], terminal=result['terminal'],
                           endpoint_sha256=result['endpoint_sha256'], rollout_seconds=result['seconds'],
                           memory=result['memory'], support_mode_calls=calls)
                del endpoint, saved
            group_rows.append(row)
        full = gradients.get(K)
        groups.append({'data': common['data'], 'K': K,
                       'full_derivative_available': full is not None,
                       'H1_and_H2_relative_to_full': {
                           str(tail): gradient_comparison(gradients[tail], full)
                           for tail in (1, 2) if tail in gradients and full is not None},
                       'H1_relative_to_H2_not_full_error': (
                           gradient_comparison(gradients[1], gradients[2]) if 2 in gradients else None)})
        rows.extend(group_rows)
        del endpoints, gradients
    assert finished['all_derivatives_obtained'] == all(row['derivative_obtained'] for row in rows[2:])
    remember(Path(__file__))
    value = {'status': 'TWELVE_UNIFORM_REPLAY_ARTIFACTS_REVIEWED_WITH_EXPLICIT_DERIVATIVE_AVAILABILITY',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'rows': rows, 'comparisons': groups, 'phi_parameter_count': parameter_count,
             'all_derivatives_obtained': all(row['derivative_obtained'] for row in rows),
             'evidence_sha256': evidence, 'original_failed_diagnostic_bytes_unchanged': True,
             'method_validated': False, 'production_training_modified': False,
             'downstream_promotion_allowed': False, 'full_goal_complete': False,
             'limitations': ['Saved states and gradients are audited on CPU; no real-model derivative is independently recomputed.',
                            'Unavailable full H4/H8 derivatives leave the associated truncation error unmeasured.',
                            'The uniform recomputed-forward path differs from the original failed mixed numerical path.',
                            'A few fixed development queries do not establish comparative learning or recovery efficacy.']}
    with target.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'review_sha256': sha(target),
                      'reviewed_runs': len(rows), 'all_derivatives_obtained': value['all_derivatives_obtained'],
                      'comparisons': groups}))


if __name__ == '__main__':
    main()
