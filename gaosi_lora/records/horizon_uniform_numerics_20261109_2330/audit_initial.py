"""Independently check saved uniform H1/H2 endpoints before any full H4/H8 run."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))


def main():
    import torch
    from gaosi.horizon import compare_trees, gradient_comparison, tree_digest
    torch.set_num_threads(2)
    directory = Path(__file__).resolve().parent
    target = directory / 'initial_saved_tensor_review.json'
    assert not target.exists()
    plan = json.loads((directory / 'plan.json').read_text())
    gate = json.loads((directory / 'initial_gate_completed.json').read_text())
    assert gate['status'] == 'H1_H2_EXECUTED_PENDING_INDEPENDENT_SAVED_TENSOR_REVIEW'
    controller = ROOT / 'records/jobs/horizon_uniform_initial_gate_observed_blocking_v8.json'
    assert json.loads(controller.read_text())['status'] == 'PASS'
    files = {Path(__file__).resolve(), directory / 'plan.json', directory / 'initial_gate_completed.json',
             directory / 'launch_receipt.json', directory / 'probe_tensor_audit.json',
             directory / 'uniform_direct_chain_cpu.json', controller}
    for name, expected in plan['source_sha256'].items():
        path = ROOT / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        files.add(path)

    def finite(tree):
        if torch.is_tensor(tree):
            assert tree.device.type == 'cpu'
            return bool(torch.isfinite(tree).all())
        if isinstance(tree, dict):
            return all(finite(value) for value in tree.values())
        if isinstance(tree, (list, tuple)):
            return all(finite(value) for value in tree)
        return not isinstance(tree, float) or math.isfinite(tree)

    endpoints, gradients, rows = [], [], []
    for index in (0, 1):
        config = plan['configs'][index]['uniform']
        run = ROOT / config['out']
        paths = [run / name for name in ['config.json', 'result.json', 'events.jsonl', 'endpoint.pt', 'gradient.pt']]
        paths += [ROOT / 'records/jobs' / (run.name + '.json'), ROOT / 'records/logs' / (run.name + '.log')]
        files.update(paths)
        result = json.loads((run / 'result.json').read_text())
        job = json.loads((ROOT / 'records/jobs' / (run.name + '.json')).read_text())
        assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
        assert result['diagnostic_status'] == 'COMPLETE'
        assert result['config'] == config == json.loads((run / 'config.json').read_text())
        assert result['original_frozen_config'] == plan['configs'][index]['original']
        assert result['base_unchanged'] and result['phi_unchanged'] and result['optimizer_updates'] == 0
        assert result['diagnostic_variant'] == 'uniform_recomputed_support_forward_v1'
        assert not result['same_original_mixed_path_numerical_trajectory']
        end = torch.load(run / 'endpoint.pt', map_location='cpu', weights_only=False)
        saved = torch.load(run / 'gradient.pt', map_location='cpu', weights_only=False)
        assert finite(end) and finite(saved)
        assert saved['config'] == config and saved['terminal'] == result['terminal']
        assert saved['endpoint_sha256'] == result['endpoint_sha256'] == tree_digest(end)
        gradient = saved['gradient']
        assert gradient.ndim == 1 and gradient.numel() > 0
        assert math.isclose(float(gradient.double().norm()), result['gradient_norm'], rel_tol=1e-14)
        events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
        inner = [event for event in events if event['event'] == 'inner']
        assert [event['k'] for event in inner] == [1, 2, 3, 4]
        assert [event['exact_window'] for event in inner] == ([False] * 4 if index == 0 else [False, False, True, True])
        assert all(event[key] >= config['effective_tokens'] for event in inner
                   for key in ['task_tokens', 'language_tokens', 'keep_tokens'])
        endpoints.append(end)
        gradients.append(gradient)
        rows.append({'run': config['out'], 'tail': config['tail'], 'gradient_norm': result['gradient_norm'],
                     'endpoint_sha256': result['endpoint_sha256'], 'terminal': result['terminal'],
                     'support_mode_calls': result['support_mode_calls']})
    comparison = compare_trees(endpoints[1], endpoints[0])
    derivative = gradient_comparison(gradients[0], gradients[1])
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    value = {'status': 'UNIFORM_H1_H2_SAVED_ENDPOINT_GATE_INDEPENDENTLY_PASSED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'runs': rows,
             'full_state_and_sample_flow_comparison': comparison,
             'H1_gradient_relative_to_H2': derivative, 'evidence_sha256': hashes,
             'remaining_ten_permitted_by_this_gate': True, 'remaining_ten_launched': False,
             'production_training_modified': False, 'method_validated': False, 'full_goal_complete': False,
             'limitations': ['Strict equality applies to the new common recomputed-forward trajectory, not the original failed mixed-path replay.',
                            'CPU saved-state inspection and earlier tiny direct-AD checks do not independently recompute a real-model derivative.',
                            'H1 versus H2 gradient difference does not quantify error relative to full H4 until that derivative completes.']}
    with target.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'full_state_comparison': comparison,
                      'H1_gradient_relative_to_H2': derivative,
                      'review_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
