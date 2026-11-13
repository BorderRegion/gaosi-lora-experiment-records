"""Versioned development diagnostic with one numerical support-gradient path.

Use the production recomputed/create_graph forward for every support gradient.
Detach its outputs only outside the declared exact tail. This changes the
numerical trajectory from the original mixed-path diagnostic; it does not
change training or retroactively validate that diagnostic. Endpoint tolerances
and the frozen data, K, rank, tokens and derivative windows stay unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))


def uniform_support(original, model, parameters, batches, create_graph=False):
    loss, gradients = original(model, parameters, batches, True)
    if create_graph:
        return loss, gradients
    return loss.detach(), {name: value.detach() for name, value in gradients.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=int, required=True)
    args = parser.parse_args()
    import torch
    import torch_musa
    from gaosi import horizon, meta
    from gaosi.runtime import atomic_save
    torch.set_num_threads(8)
    directory = Path(__file__).resolve().parent
    plan = json.loads((directory / 'plan.json').read_text())
    assert plan['status'] == 'DECLARED_UNIFORM_SUPPORT_NUMERICS_DEVELOPMENT_DIAGNOSTIC'
    assert 0 <= args.index < len(plan['configs'])
    for name, expected in plan['source_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    entry = plan['configs'][args.index]
    original, config = entry['original'], entry['uniform']
    assert {key for key in original if config[key] != original[key]} <= {'out', 'reference_endpoint'}
    assert not Path(config['out']).exists()
    production_check = horizon.check_config
    production_plan = production_check(original)
    assert hashlib.sha256(Path(original['horizon_manifest']).read_bytes()).hexdigest() == plan['original_plan_sha256']
    assert production_plan['configs'][args.index] == original
    support, compare = meta.support_gradients, horizon.compare_trees
    calls = {'detached_prefix_or_H1': 0, 'exact_tail': 0}

    def checked(candidate):
        assert candidate == config
        return production_check(original)

    def one_path(model, parameters, batches, create_graph=False):
        calls['exact_tail' if create_graph else 'detached_prefix_or_H1'] += 1
        return uniform_support(support, model, parameters, batches, create_graph)

    def preserve_and_compare(observed, expected, atol=1e-6, rtol=1e-5):
        assert (atol, rtol) == (1e-6, 1e-5)
        atomic_save(observed, Path(config['out']) / 'endpoint.pt')
        return compare(observed, expected, atol=atol, rtol=rtol)

    try:
        with patch.object(horizon, 'check_config', checked), \
                patch.object(meta, 'support_gradients', one_path), \
                patch.object(horizon, 'compare_trees', preserve_and_compare):
            result = horizon.run(config)
        assert sum(calls.values()) == 3 * config['K']
        assert calls['exact_tail'] == 3 * config['tail'] if config['tail'] > 1 else calls['exact_tail'] == 0
        result.update(diagnostic_variant='uniform_recomputed_support_forward_v1',
                      original_frozen_config=original, support_mode_calls=calls,
                      original_diagnostic_fixed_or_revalidated=False,
                      training_settings_changed=False, production_source_modified=False,
                      same_original_mixed_path_numerical_trajectory=False,
                      method_validated=False, full_goal_complete=False,
                      plan_sha256=hashlib.sha256((directory / 'plan.json').read_bytes()).hexdigest())
    except Exception as error:
        reason = horizon.limitation(error, config['device'])
        result = {'status': 'PASS' if reason else 'FAIL',
                  'diagnostic_status': reason or 'ERROR', 'config': config,
                  'original_frozen_config': original, 'error': repr(error),
                  'traceback': traceback.format_exc(), 'derivative_obtained': False,
                  'diagnostic_variant': 'uniform_recomputed_support_forward_v1',
                  'support_mode_calls': calls, 'training_settings_changed': False,
                  'production_source_modified': False, 'full_goal_complete': False}
        Path(config['out']).mkdir(parents=True, exist_ok=True)
        Path(config['out'], 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        if not reason:
            raise
    Path(config['out'], 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'diagnostic_status': result['diagnostic_status'],
                      'out': config['out'], 'support_mode_calls': calls}), flush=True)


if __name__ == '__main__':
    main()
