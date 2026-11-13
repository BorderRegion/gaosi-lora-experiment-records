"""Reconstruct a bounded development prefix and capture the actual v7 clip.

This is a new instrumented trajectory, never an exact replay of the historical
episode 7. CPU reads and synchronization can affect backend repeatability.
Execution requires the inherited device_guard lock; validation uses no torch.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(config):
    source_path = ROOT / config['source_config']
    if digest(source_path) != config['source_config_sha256']:
        raise ValueError('Production source configuration changed')
    source = json.loads(source_path.read_text())
    stop = config['stop_after_episode']
    if (type(stop) is not int or not 0 <= stop <= 7
            or type(config['training_repeats']) is not int
            or config['training_repeats'] not in (1, 2)):
        raise ValueError('Only 1–2 reconstructed prefixes of at most 8 episodes')
    if (source['selection_data'] != 'development_only'
            or source['architecture'] != 'L' or source['organization'] != 'O1'
            or source['device'] != 'musa' or source['horizon'] != 'EXACT_H2_tail'
            or source.get('initial_phi') or source.get('start_episode', 0)
            or source['known_warm_episodes'] <= stop
            or source['episode_K'][:stop + 1] != [8] * (stop + 1)
            or config.get('formal_data_used') is not False):
        raise ValueError('Only the fresh, known-task v7 L/O1/H2 development prefix')
    if any('final' in str(p) for p in source['parent_data'] + source['parents']):
        raise ValueError('Formal inputs forbidden')
    expected = config['source_code_sha256']
    required = {str(p.relative_to(ROOT)) for p in (ROOT / 'gaosi').glob('*.py')}
    required.add('tools/device_guard.py')
    if set(expected) != required:
        raise ValueError('Pin the complete gaosi source and device guard')
    for name, sha in expected.items():
        if digest(ROOT / name) != sha:
            raise ValueError('Runtime source changed: ' + name)
    out = (ROOT / config['out']).resolve()
    allowed = (ROOT / 'runs/diagnostics').resolve()
    if not out.is_relative_to(allowed) or out == allowed:
        raise ValueError('Output must be a dedicated runs/diagnostics subdirectory')
    production = (ROOT / source['out']).resolve()
    if out == production or production.is_relative_to(out) or out.is_relative_to(production):
        raise ValueError('Diagnostic output overlaps production')
    if out.exists():
        raise ValueError('Preserve existing diagnostic output; choose a new name')
    if config['lock_path'] != '/tmp/gaosi_musa_device0.lock':
        raise ValueError('Use the existing production device lock')
    return source, out


def require_inherited_lock(lock_path):
    """An exec from device_guard preserves both PID and locked descriptor."""
    st = Path(lock_path).stat()
    key = f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}'
    own = False
    for line in Path('/proc/locks').read_text().splitlines():
        fields = line.split()
        if (len(fields) >= 8 and fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE']
                and fields[4] == str(os.getpid()) and fields[5] == key):
            own = True
    inherited = False
    for fd in Path('/proc/self/fd').iterdir():
        try:
            current = fd.stat()
            inherited |= current.st_dev == st.st_dev and current.st_ino == st.st_ino
        except FileNotFoundError:
            pass
    if not (own and inherited):
        raise RuntimeError('Execute through job.py --musa-lock; do not bypass device_guard')


def instrumented_train(meta, torch, atomic_save, source, config, out):
    original_clip, original_rollout = meta.stable_phi_clip_, meta.rollout
    episode, active_phi = 0, None

    def rollout(*values, **kwargs):
        nonlocal active_phi
        active_phi = values[3]
        if episode:
            previous = out / 'latest.pt'
            state = torch.load(previous, map_location='cpu', weights_only=False)
            if state['next_episode'] != episode or state['config'] != source:
                raise RuntimeError('Reconstructed prefix checkpoint is inconsistent')
            saved = out / f'before_episode_{episode}.pt'
            if saved.exists():
                raise RuntimeError('Refuse to overwrite diagnostic episode checkpoint')
            shutil.copyfile(previous, saved)
        return original_rollout(*values, **kwargs)

    def clip(parameters, *args, **kwargs):
        nonlocal episode
        parameters = list(parameters)
        named = list(active_phi.named_parameters())
        if [id(p) for _, p in named] != [id(p) for p in parameters]:
            raise RuntimeError('Clip parameters differ from the observed phi')
        raw = {n: p.grad.detach().cpu().clone() if p.grad is not None else None
               for n, p in named}
        starting = {n: p.detach().cpu().clone() for n, p in named}
        artifact = {'episode': episode, 'raw_gradient': raw, 'starting_phi': starting,
                    'source_config_sha256': config['source_config_sha256'],
                    'trajectory': 'instrumented reconstruction from initialization'}
        path = out / f'gradient_episode_{episode}.pt'
        if path.exists():
            raise RuntimeError('Refuse to overwrite raw gradient evidence')
        # Save before calling the real guard, including when it rejects nonfinite values.
        atomic_save(artifact, path)
        finite = all(bool(torch.isfinite(g).all()) for g in raw.values() if g is not None)
        norm = (math.sqrt(sum(float(g.double().square().sum())
                              for g in raw.values() if g is not None)) if finite else None)
        row = {'episode': episode, 'all_raw_gradients_finite': finite,
               'raw_norm_cpu_fp64': norm, 'parameters': {}}
        for name, g in raw.items():
            if g is None:
                row['parameters'][name] = {'gradient_present': False}
                continue
            good = bool(torch.isfinite(g).all())
            row['parameters'][name] = {'gradient_present': True, 'all_finite': good,
                                      'norm_fp64': float(g.double().norm()) if good else None,
                                      'max_abs': float(g.abs().max()) if good else None}
        try:
            returned = original_clip(parameters, *args, **kwargs)
            clipped = {n: p.grad.detach().cpu().clone() if p.grad is not None else None
                       for n, p in named}
            artifact.update(clipped_gradient=clipped, clip_return=returned.detach().cpu())
            atomic_save(artifact, path)
            row['clip_return'] = float(returned.cpu())
            row['all_clipped_gradients_finite'] = all(
                bool(torch.isfinite(g).all()) for g in clipped.values() if g is not None)
            row['clipped_norm_cpu_fp64'] = math.sqrt(sum(
                float(g.double().square().sum()) for g in clipped.values() if g is not None))
        except BaseException as error:
            row['clip_error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            with (out / 'gradient_observations.jsonl').open('a') as handle:
                handle.write(json.dumps(row, allow_nan=False) + '\n')
        episode += 1
        return returned

    meta.rollout, meta.stable_phi_clip_ = rollout, clip
    try:
        meta.train(source)
    finally:
        meta.rollout, meta.stable_phi_clip_ = original_rollout, original_clip
    if episode != len(source['episode_K']):
        raise RuntimeError('Not every reconstructed episode reached the current clip')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--validate-only', action='store_true')
    action.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    source, root = validate(config)
    budget = sum(source['episode_K'][:config['stop_after_episode'] + 1]) * config['training_repeats']
    if args.validate_only:
        print(json.dumps({'status': 'VALIDATED_NOT_EXECUTED', 'inner_updates_max': budget,
                          'output': config['out'], 'torch_imported': False}))
        return
    require_inherited_lock(config['lock_path'])
    import torch
    import torch_musa  # noqa: F401
    import gaosi.meta as meta
    from gaosi.runtime import atomic_save
    torch.set_num_threads(8)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'diagnostic_config.json').write_text(json.dumps(config, indent=2) + '\n')
    (root / 'production_source_config.json').write_bytes((ROOT / config['source_config']).read_bytes())
    shutil.copyfile(__file__, root / 'diagnose_prefix_gradient.py')
    result = {'status': 'RUNNING', 'method_validated': False,
              'formal_data_used': False, 'production_modified': False,
              'diagnostic_inner_updates_max': budget,
              'limitations': ['Instrumented reconstruction, not the historical trajectory.',
                              'CPU copies introduce synchronization and can change backend behavior.',
                              'Execution success does not establish gradient correctness.']}
    result_path = root / 'diagnostic_result.json'
    try:
        for repeat in range(config['training_repeats']):
            observed = dict(source, out=str(root / f'repeat_{repeat}'),
                            episode_K=source['episode_K'][:config['stop_after_episode'] + 1],
                            purpose='Independent gradient diagnosis; never promote this phi')
            instrumented_train(meta, torch, atomic_save, observed, config, Path(observed['out']))
        result['status'] = 'EXECUTED_REQUIRES_SCIENTIFIC_REVIEW'
    except BaseException:
        result.update(status='FAILED_EVIDENCE_PRESERVED', error=traceback.format_exc())
        raise
    finally:
        result['finished_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        result_path.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
