"""Compare support gradient modes at the first exact-tail boundary.

The frozen production source is imported without changing any file. Run the
original H2 prefix, compare repeated first-order and recomputed gradients at
identical adapters and token batches for all three routes, then stop before
the first exact transition. No optimizer or scientific approval is involved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))


class ProbeComplete(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    import torch
    import torch_musa
    from gaosi import horizon, meta
    from gaosi.runtime import atomic_save
    torch.set_num_threads(8)
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    assert not Path(args.out).exists()
    original = json.loads(Path(args.config).read_text())
    assert original['K'] == 4 and original['tail'] == 2 and original['device'] == 'musa'
    assert original['data'] == 'data/synthetic/dev_parent.jsonl'
    check = horizon.check_config
    plan = check(original)
    config = dict(original, out=args.out)
    incident = json.loads(Path('records/pipeline_recovery_20261109_2152/incident_review.json').read_text())
    pinned = {}
    for name in ['gaosi/horizon.py', 'gaosi/meta.py', 'gaosi/recompute.py', 'gaosi/transition.py']:
        expected = incident['remote_expected_sha256'][name]
        actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        assert actual == expected
        pinned[name] = actual
    support = meta.support_gradients
    routes, evidence = [], {}

    def compare(left, right):
        details = {}
        difference_sq = left_sq = 0.
        max_abs = 0.
        for name in left:
            x, y = left[name].double(), right[name].double()
            delta = x - y
            absolute = float(delta.abs().max())
            numerator = float(delta.square().sum())
            denominator = float(x.square().sum())
            details[name] = {'max_abs': absolute,
                'relative_l2': (numerator / max(denominator, 1e-300)) ** .5,
                'strict_close': bool(torch.allclose(x, y, atol=1e-6, rtol=1e-5)),
                'bitwise_equal': bool(torch.equal(x, y))}
            difference_sq += numerator
            left_sq += denominator
            max_abs = max(max_abs, absolute)
        return {'max_abs': max_abs, 'relative_l2': (difference_sq / max(left_sq, 1e-300)) ** .5,
            'all_bitwise_equal': all(v['bitwise_equal'] for v in details.values()),
            'all_strict_close': all(v['strict_close'] for v in details.values()),
            'per_parameter': details}

    def compute(model, parameters, batches, mode):
        loss, gradients = support(model, parameters, batches, mode)
        value = float(loss.detach().cpu())
        cpu = {name: gradient.detach().cpu().clone() for name, gradient in gradients.items()}
        assert all(torch.isfinite(gradient).all() for gradient in cpu.values())
        return value, cpu

    def probe_support(model, parameters, batches, create_graph=False):
        if not create_graph:
            return support(model, parameters, batches, False)
        index = len(routes)
        assert index < 3
        route = ['task', 'language', 'keep'][index]
        loss_a, first_a = compute(model, parameters, batches, False)
        loss_b, first_b = compute(model, parameters, batches, False)
        first_repeat = compare(first_a, first_b)
        del first_b
        loss_c, exact_a = compute(model, parameters, batches, True)
        cross = compare(first_a, exact_a)
        loss_d, exact_b = compute(model, parameters, batches, True)
        exact_repeat = compare(exact_a, exact_b)
        del exact_b
        worst = max(cross['per_parameter'], key=lambda key: cross['per_parameter'][key]['max_abs'])
        evidence[route] = {'parameter': worst, 'first_order': first_a[worst], 'recomputed': exact_a[worst]}
        routes.append({'route': route, 'adapter_sha256': horizon.tree_digest(parameters),
            'token_batches_sha256': horizon.tree_digest(batches),
            'loss_first_a': loss_a, 'loss_first_b': loss_b,
            'loss_exact_a': loss_c, 'loss_exact_b': loss_d,
            'first_order_repeat': first_repeat, 'recomputed_repeat': exact_repeat,
            'first_order_vs_recomputed': cross})
        print(json.dumps({'event': 'support_mode_probe', 'route': route,
            'loss_first': loss_a, 'loss_exact': loss_c,
            'first_order_repeat_equal': first_repeat['all_bitwise_equal'],
            'recomputed_repeat_equal': exact_repeat['all_bitwise_equal'],
            'cross_max_abs': cross['max_abs'], 'cross_relative_l2': cross['relative_l2'],
            'cross_strict_close': cross['all_strict_close']}), flush=True)
        del first_a, exact_a
        if len(routes) == 3:
            raise ProbeComplete()
        return support(model, parameters, batches, True)

    def checked(candidate):
        assert candidate == config
        return check(original)

    with patch.object(horizon, 'check_config', checked), patch.object(meta, 'support_gradients', probe_support):
        try:
            horizon.run(config)
        except ProbeComplete:
            pass
        else:
            raise AssertionError('Probe must stop at the first exact step, before its transition')
    assert len(routes) == 3 and len({route['adapter_sha256'] for route in routes}) == 1
    atomic_save(evidence, Path(args.out) / 'worst_gradient_pairs.pt')
    result = {'status': 'PASS', 'diagnostic_status': 'SUPPORT_MODE_COMPARISON_COMPLETE',
        'original_frozen_config': original, 'config': config,
        'production_source_sha256': pinned, 'probe_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'plan_artifact_sha256': plan['artifact_sha256'], 'routes': routes,
        'first_exact_transition_executed': False, 'optimizer_updates': 0,
        'limitations': ['Compares numerical support gradients only; no H2 derivative or full-trajectory efficacy claim.',
            'First exact-boundary adapter comes from the unchanged production first-order prefix.',
            'Repeat equality can localize deterministic mode differences but does not identify a low-level kernel.'],
        'training_settings_changed': False, 'method_validated': False, 'full_goal_complete': False}
    Path(args.out, 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'diagnostic_status': result['diagnostic_status'],
        'out': args.out, 'result_sha256': hashlib.sha256(Path(args.out, 'result.json').read_bytes()).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()
