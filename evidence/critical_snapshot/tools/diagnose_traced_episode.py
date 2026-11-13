"""Capture support HVP boundaries for one pinned development episode.

The underlying replay never clips gradients or updates phi. Captures and CPU
statistics add synchronization, so this is a separate diagnostic trajectory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate(config):
    output = (ROOT / config['out']).resolve()
    allowed = (ROOT / 'runs/diagnostics').resolve()
    if output == allowed or not output.is_relative_to(allowed) or output.exists():
        raise ValueError('Use a new dedicated diagnostic output directory')
    if config.get('formal_data_used') is not False or config['epsilons']:
        raise ValueError('One analytic development replay only')
    required = {str(p.relative_to(ROOT)) for p in (ROOT / 'gaosi').glob('*.py')}
    pins = config['source_code_sha256']
    if set(pins) != required:
        raise ValueError('Pin all numerical source files')
    for name, expected in {**pins, **config['diagnostic_tool_sha256']}.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Diagnostic source changed: ' + name)
    source = ROOT / config['source_config']
    if hashlib.sha256(source.read_bytes()).hexdigest() != config['source_config_sha256']:
        raise ValueError('Source configuration changed')
    source = json.loads(source.read_text())
    episode = config['episode']
    if (type(episode) is not int or not 0 <= episode < len(source['episode_K'])
            or source['episode_K'][episode] not in (8, 16)
            or source['horizon'] != 'EXACT_H2_tail'
            or source['selection_data'] != 'development_only'
            or source['device'] != 'musa'
            or source['architecture'] != 'L' or source['organization'] != 'O1'
            or (episode >= source['known_warm_episodes'] and episode % 2)
            or any('final' in str(p) for p in source['parent_data'] + source['parents'])):
        raise ValueError('Only a pinned L/O1/H2 development episode')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    output = validate(config)
    if args.validate_only:
        print(json.dumps({'status': 'VALIDATED_NOT_EXECUTED', 'episode': config['episode'],
                          'output': str(output), 'phi_updates': 0}))
        return
    from diagnose_prefix_gradient import require_inherited_lock
    require_inherited_lock('/tmp/gaosi_musa_device0.lock')
    import diagnose_meta_gradient as diagnosis
    import hvp_trace
    original = hvp_trace.install(output)
    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0], '--config', args.config]
        diagnosis.main()
    finally:
        hvp_trace.recompute._RecomputedSupport.backward = staticmethod(original)
        sys.argv = original_argv


if __name__ == '__main__':
    main()
