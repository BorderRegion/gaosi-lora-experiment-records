"""Run the unchanged full meta schedule with saved gradients and fail-fast review.

Launch blocking is a candidate mitigation. Execution success alone does not
validate the method or qualify the resulting phi for downstream promotion.
"""
import argparse
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


def validate(manifest):
    for name, expected in manifest['pinned_files_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Pinned input changed: ' + name)
    required = {str(p.relative_to(ROOT)) for p in (ROOT / 'gaosi').glob('*.py')}
    required.update(['tools/train_observed_meta.py', 'tools/device_guard.py',
                     'tools/diagnose_prefix_gradient.py', manifest['config'],
                     manifest['source_config']])
    if not required.issubset(manifest['pinned_files_sha256']):
        raise ValueError('Pin the complete numerical source, tools and configurations')
    config = json.loads((ROOT / manifest['config']).read_text())
    source = json.loads((ROOT / manifest['source_config']).read_text())
    expected = dict(source, out=config['out'], purpose=config['purpose'])
    if config != expected or config['out'] == source['out']:
        raise ValueError('Only output and provenance purpose may differ from the full source schedule')
    if (config['device'] != 'musa' or config['architecture'] != 'L'
            or config['organization'] != 'O1' or config['horizon'] != 'EXACT_H2_tail'
            or config['episode_K'] != [8] * 64 + [16] * 128
            or config['selection_data'] != 'development_only'
            or config.get('initial_phi') or config.get('start_episode', 0)
            or manifest['formal_data_used'] is not False
            or manifest['method_validated'] is not False
            or manifest['raw_norm_stop_before_update'] != 1e6
            or manifest['lock_path'] != '/tmp/gaosi_musa_device0.lock'):
        raise ValueError('Require a fresh complete L/O1/H2 schedule with explicit review gate')
    if any('final' in str(p) for p in config['parents'] + config['parent_data']):
        raise ValueError('Formal inputs forbidden')
    output = (ROOT / config['out']).resolve()
    if (output == ROOT / 'runs' or not output.is_relative_to(ROOT / 'runs')
            or output.exists()):
        raise ValueError('Use a new run output; existing state cannot be resumed here')
    return config, output


def observed_train(meta, torch, atomic_save, config, manifest, output):
    original_rollout, original_clip = meta.rollout, meta.stable_phi_clip_
    episode, active, before = 0, None, None
    ledger = output / 'raw_gradients.jsonl'

    def rollout(*args, **kwargs):
        nonlocal active, before
        active = args[3]
        if episode:
            checkpoint = output / 'latest.pt'
            saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
            if saved['next_episode'] != episode or saved['config'] != config:
                raise RuntimeError('Pre-episode checkpoint does not match schedule')
            destination = output / f'before_episode_{episode:03d}.pt'
            if destination.exists():
                raise RuntimeError('Preserve existing checkpoint evidence')
            shutil.copyfile(checkpoint, destination)
        before = {name: p.detach().cpu().clone() for name, p in active.named_parameters()}
        return original_rollout(*args, **kwargs)

    def clip(parameters, *args, **kwargs):
        nonlocal episode
        parameters = list(parameters)
        named = list(active.named_parameters())
        if [id(p) for _, p in named] != [id(p) for p in parameters]:
            raise RuntimeError('Observed phi does not match clip parameters')
        raw = {name: p.grad.detach().cpu().clone() if p.grad is not None else None
               for name, p in named}
        starting = {name: p.detach().cpu().clone() for name, p in named}
        unchanged = all(torch.equal(before[name], starting[name]) for name in before)
        finite = all(g is not None and bool(torch.isfinite(g).all()) for g in raw.values())
        norm = math.sqrt(sum(float(g.double().square().sum()) for g in raw.values())) if finite else None
        artifact = {'episode': episode, 'starting_phi': starting, 'raw_gradient': raw,
                    'phi_unchanged_during_rollout': unchanged,
                    'config_sha256': manifest['pinned_files_sha256'].get(manifest['config']),
                    'method_validated': False}
        path = output / f'gradient_episode_{episode:03d}.pt'
        if path.exists():
            raise RuntimeError('Preserve existing gradient evidence')
        atomic_save(artifact, path)
        row = {'episode': episode, 'raw_all_finite': finite, 'raw_norm_cpu_fp64': norm,
               'phi_unchanged_during_rollout': unchanged,
               'gradient_artifact': str(path), 'update_authorized_by_guard': False}
        try:
            if not unchanged:
                raise RuntimeError('Phi changed inside the rollout; evidence saved before update')
            if not finite:
                raise RuntimeError('Nonfinite or absent raw phi gradient; evidence saved before update')
            if norm > manifest['raw_norm_stop_before_update']:
                raise RuntimeError('Raw phi norm exceeds review threshold; stopped before clipping/update')
            returned = original_clip(parameters, *args, **kwargs)
            clipped = {name: p.grad.detach().cpu().clone() for name, p in named}
            artifact.update(clipped_gradient=clipped, clip_return=returned.detach().cpu())
            atomic_save(artifact, path)
            row.update(clip_return=float(returned.cpu()), update_authorized_by_guard=True,
                       clipped_all_finite=all(bool(torch.isfinite(g).all()) for g in clipped.values()))
            if not row['clipped_all_finite']:
                raise RuntimeError('Clipped gradient nonfinite; stopped before optimizer update')
        except BaseException as error:
            row['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            row['artifact_sha256'] = digest(path)
            with ledger.open('a') as handle:
                handle.write(json.dumps(row, allow_nan=False) + '\n')
        episode += 1
        return returned

    meta.rollout, meta.stable_phi_clip_ = rollout, clip
    try:
        meta.train(config)
    finally:
        meta.rollout, meta.stable_phi_clip_ = original_rollout, original_clip
    if episode != len(config['episode_K']):
        raise RuntimeError('Not every full-schedule episode reached its guard')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    config, output = validate(manifest)
    if args.validate_only:
        print(json.dumps({'status': 'VALIDATED_NOT_EXECUTED', 'episodes': 192,
                          'inner_updates': 2560, 'fresh_phi': True, 'method_validated': False}))
        return
    if os.environ.get('MUSA_LAUNCH_BLOCKING') != '1':
        raise RuntimeError('Require process-local launch blocking')
    from diagnose_prefix_gradient import require_inherited_lock
    require_inherited_lock(manifest['lock_path'])
    import torch
    import torch_musa  # noqa: F401
    import gaosi.meta as meta
    from gaosi.runtime import atomic_save
    torch.set_num_threads(8)
    result = {'status': 'RUNNING', 'method_validated': False,
              'downstream_promotion_allowed': False, 'formal_data_used': False,
              'purpose': 'Full fresh schedule under candidate synchronization mitigation',
              'raw_norm_stop_before_update': manifest['raw_norm_stop_before_update'],
              'limitations': ['Execution success requires separate numerical and scientific review.',
                              'The gradient threshold stops for review; gradients are never skipped or replaced.']}
    try:
        observed_train(meta, torch, atomic_save, config, manifest, output)
        result.update(status='COMPLETE_REQUIRES_REVIEW', episodes=192, inner_updates=2560)
    except BaseException:
        result.update(status='FAILED_EVIDENCE_PRESERVED', error=traceback.format_exc())
        raise
    finally:
        if output.exists():
            (output / 'observed_training_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            shutil.copyfile(__file__, output / 'train_observed_meta.py')
            (output / 'observed_training_result.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
