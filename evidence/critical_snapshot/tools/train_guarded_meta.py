"""Independent evidence wrapper for fresh and warm-start meta-training leaves.

The numerical trainer, clip and optimizer remain unchanged. This tool is not
used by the currently running L job and never resumes an existing output.
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
LOCK = '/tmp/gaosi_musa_device0.lock'
THRESHOLD = 1e6
ENTRIES = {'meta': None, 'scale': 'scale_action', 'confirmation': 'confirmation_action'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)


def required_inputs(config, entry):
    files = set(config['parents'] + config['parent_data'])
    for key in ['initial_phi', 'calibration_manifest', 'world_manifest',
                'scale_entry_manifest', 'confirmation_entry_manifest', 'meta_reference_config']:
        if config.get(key):
            files.add(config[key])
    for key in ['historical_bases', 'historical_data']:
        files.update(config.get(key, []))
    files.update(p + '.json' for p in config.get('historical_bases', []))
    files.update(['data/language/inner.jsonl', 'data/language/meta_query.jsonl'])
    worlds = json.loads(Path(config['world_manifest']).read_text())['worlds']
    task_data = set(config['parent_data'] + config.get('historical_data', []))
    task_data.update(str(Path('data/synthetic') / w['file'])
                     for w in worlds if w['split'] == 'meta')
    files.update(task_data)
    for path in task_data:
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        if not rows or any(not r.get('world_id', '').startswith('meta') for r in rows):
            raise ValueError('Phi training can consume only meta-world rows: ' + path)
        if not all(any(r.get('role') == role for r in rows) for role in ['support', 'query']):
            raise ValueError('Meta data lacks support or query: ' + path)
    # Pin the original entry validation's declared inputs as well as its file.
    entry_key = {'scale': 'scale_entry_manifest',
                 'confirmation': 'confirmation_entry_manifest'}.get(entry)
    if entry_key:
        value = json.loads(Path(config[entry_key]).read_text())
        for group in ['artifact_sha256', 'data_sha256', 'upstream_freezes']:
            files.update(value.get(group, {}))
        files.update(value.get('freeze_manifests', []))
    for parent in config['parents']:
        result = Path(parent).parent / 'result.json'
        if result.exists():
            files.add(str(result))
    return files


def validate_shape(config, entry):
    schedule = config['episode_K']
    start = config.get('start_episode', 0)
    if (entry not in ENTRIES or config['device'] != 'musa'
            or config['architecture'] not in ['L', 'X']
            or config['organization'] not in ['O1', 'O2', 'O3', 'O4']
            or config['horizon'] not in ['H1', 'EXACT_H2_tail']
            or not isinstance(schedule, list) or not schedule
            or any(type(k) is not int or k <= 0 for k in schedule)
            or type(start) is not int or not 0 <= start < len(schedule)
            or config.get('selection_data', 'development_only') != 'development_only'
            or not config.get('retain_fact_support')
            or len(config['parents']) != len(config['parent_data']) or not config['parents']
            or (start and not config.get('initial_phi'))
            or (config.get('load_initial_meta_optimizer') and not config.get('initial_phi'))):
        raise ValueError('Unsupported or incomplete meta schedule')
    if entry != 'meta' and config.get(ENTRIES[entry]) != 'meta':
        raise ValueError('This wrapper accepts only meta actions')
    for owner, key in [('scale', 'scale_action'), ('confirmation', 'confirmation_action')]:
        if key in config and entry != owner:
            raise ValueError('Do not bypass the original ' + owner + ' entry validator')
    output = Path(config['out']).resolve()
    if output == ROOT / 'runs' or not output.is_relative_to(ROOT / 'runs'):
        raise ValueError('Require a new output under runs/')
    return start, output


def pinned_source_files():
    return {str(p.relative_to(ROOT)) for p in (ROOT / 'gaosi').glob('*.py')} | {
        'tools/train_guarded_meta.py', 'tools/device_guard.py',
        'tools/diagnose_prefix_gradient.py'}


def prepare(config_path, entry, manifest_path):
    config = json.loads(Path(config_path).read_text())
    start, output = validate_shape(config, entry)
    if output.exists():
        raise ValueError('Preserve existing output; no resume or automatic retry')
    sources = pinned_source_files()
    inputs = required_inputs(config, entry)
    files = sources | inputs | {str(config_path)}
    manifest = {'version': 1, 'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'config': str(config_path), 'entry': entry,
                'pinned_files_sha256': {p: digest(p) for p in sorted(files)},
                'input_files': sorted(inputs), 'source_files': sorted(sources),
                'start_episode': start, 'expected_next_episode': len(config['episode_K']),
                'executed_inner_updates': sum(config['episode_K'][start:]),
                'expected_cumulative_inner_updates': sum(config['episode_K']),
                'lock_path': LOCK, 'raw_norm_stop_before_update': THRESHOLD,
                'formal_data_used': False, 'method_validated': False,
                'downstream_promotion_allowed': False,
                'requires_separate_scientific_review': True}
    write_new(manifest_path, (json.dumps(manifest, indent=2) + '\n').encode())
    return manifest


def validate(manifest):
    config = json.loads(Path(manifest['config']).read_text())
    start, output = validate_shape(config, manifest['entry'])
    expected = pinned_source_files() | required_inputs(config, manifest['entry']) | {manifest['config']}
    if (manifest['version'] != 1 or not expected <= manifest['pinned_files_sha256'].keys()
            or manifest['lock_path'] != LOCK or manifest['raw_norm_stop_before_update'] != THRESHOLD
            or manifest['formal_data_used'] is not False or manifest['method_validated'] is not False
            or manifest['downstream_promotion_allowed'] is not False
            or manifest['start_episode'] != start
            or manifest['expected_next_episode'] != len(config['episode_K'])
            or manifest['executed_inner_updates'] != sum(config['episode_K'][start:])
            or manifest['expected_cumulative_inner_updates'] != sum(config['episode_K'])
            or output.exists()):
        raise ValueError('Manifest differs from the pinned new schedule')
    for name, expected_sha in manifest['pinned_files_sha256'].items():
        if digest(name) != expected_sha:
            raise ValueError('Pinned source or input changed: ' + name)
    return config, output


def check_original_entry(config, entry):
    if entry == 'scale':
        from gaosi.scale import check_config
        check_config(config)
    elif entry == 'confirmation':
        from gaosi.confirmation import check_config
        check_config(config)
    elif entry != 'meta':
        raise ValueError('Unknown entry')


def guarded_train(meta, torch, atomic_save, config, manifest, output):
    """Capture raw evidence before calling the trainer's original clip/update.

    start_episode is the absolute source-schedule index. Organization and scale
    adaptation can instead load an initial phi while restarting the index at 0.
    """
    original_rollout, original_clip = meta.rollout, meta.stable_phi_clip_
    start = config.get('start_episode', 0)
    episode, active, before = start, None, None
    ledger = output / 'raw_gradients.jsonl'

    def rollout(*args, **kwargs):
        nonlocal active, before
        if active is not None or episode >= len(config['episode_K']):
            raise RuntimeError('Unexpected repeated rollout or excess episode')
        active = args[3]
        before = {name: p.detach().cpu().clone() for name, p in active.named_parameters()}
        if episode > start:
            checkpoint = output / 'latest.pt'
            saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
            if (saved['next_episode'] != episode or saved['config'] != config
                    or saved['inner_updates'] != sum(config['episode_K'][:episode])):
                raise RuntimeError('Pre-episode checkpoint differs from absolute schedule')
        elif config.get('initial_phi'):
            checkpoint = Path(config['initial_phi'])
            saved = torch.load(checkpoint, map_location='cpu', weights_only=False)
            if (set(before) != set(saved['phi'])
                    or any(not torch.equal(before[n], saved['phi'][n]) for n in before)
                    or (start and (saved['next_episode'] != start
                                   or saved['inner_updates'] != sum(config['episode_K'][:start])))):
                raise RuntimeError('Warm checkpoint phi or episode boundary differs')
        else:
            checkpoint = None
            atomic_save({'phi': before, 'config': config, 'next_episode': start,
                         'origin': 'fresh initialized phi; optimizer not yet stepped'},
                        output / 'initial_phi.pt')
        if checkpoint is not None:
            dest = output / f'before_episode_{episode:03d}.pt'
            with checkpoint.open('rb') as source, dest.open('xb') as target:
                shutil.copyfileobj(source, target)
        return original_rollout(*args, **kwargs)

    def clip(parameters, *args, **kwargs):
        nonlocal episode, active
        if active is None:
            raise RuntimeError('Gradient arrived without its observed rollout')
        named = list(active.named_parameters())
        parameters = list(parameters)
        if [id(p) for _, p in named] != [id(p) for p in parameters]:
            raise RuntimeError('Observed phi differs from clip parameters')
        raw = {n: p.grad.detach().cpu().clone() if p.grad is not None else None for n, p in named}
        current = {n: p.detach().cpu().clone() for n, p in named}
        unchanged = all(torch.equal(before[n], current[n]) for n in before)
        finite = bool(raw) and all(g is not None and bool(torch.isfinite(g).all()) for g in raw.values())
        norm = math.sqrt(sum(float(g.double().square().sum()) for g in raw.values())) if finite else None
        path = output / f'gradient_episode_{episode:03d}.pt'
        if path.exists():
            raise RuntimeError('Preserve previous gradient artifact')
        artifact = {'episode': episode, 'starting_phi': before, 'ending_phi_before_update': current,
                    'raw_gradient': raw, 'phi_unchanged_during_rollout': unchanged,
                    'config_sha256': manifest['pinned_files_sha256'][manifest['config']],
                    'method_validated': False}
        atomic_save(artifact, path)
        row = {'episode': episode, 'raw_all_finite': finite, 'raw_norm_cpu_fp64': norm,
               'phi_unchanged_during_rollout': unchanged, 'gradient_artifact': str(path),
               'update_authorized_by_guard': False}
        try:
            if not unchanged:
                raise RuntimeError('Phi changed during episode; saved before update')
            if not finite or norm is None or not math.isfinite(norm):
                raise RuntimeError('Absent or nonfinite raw phi gradient; saved before update')
            if norm > manifest['raw_norm_stop_before_update']:
                raise RuntimeError('Raw phi norm exceeds review threshold; stopped before clip/update')
            returned = original_clip(parameters, *args, **kwargs)
            clipped = {n: p.grad.detach().cpu().clone() for n, p in named}
            artifact.update(clipped_gradient=clipped, clip_return=returned.detach().cpu())
            atomic_save(artifact, path)
            row.update(clip_return=float(returned.cpu()),
                       clipped_all_finite=all(bool(torch.isfinite(g).all()) for g in clipped.values()))
            if not row['clipped_all_finite'] or not math.isfinite(row['clip_return']):
                raise RuntimeError('Clip produced nonfinite values; stopped before update')
            row['update_authorized_by_guard'] = True
        except BaseException as error:
            row['error'] = type(error).__name__ + ': ' + str(error)
            raise
        finally:
            row['artifact_sha256'] = digest(path)
            with ledger.open('a') as handle:
                handle.write(json.dumps(row, allow_nan=False) + '\n')
        episode += 1
        active = None
        return returned

    meta.rollout, meta.stable_phi_clip_ = rollout, clip
    try:
        meta.train(config)
    finally:
        meta.rollout, meta.stable_phi_clip_ = original_rollout, original_clip
    if episode != len(config['episode_K']) or active is not None:
        raise RuntimeError('Not every scheduled episode passed the guard')


def run(manifest_path, validate_only=False):
    manifest = json.loads(Path(manifest_path).read_text())
    config, output = validate(manifest)
    check_original_entry(config, manifest['entry'])
    if validate_only:
        return {'status': 'VALIDATED_NOT_EXECUTED', 'entry': manifest['entry'],
                'start_episode': manifest['start_episode'],
                'executed_inner_updates': manifest['executed_inner_updates'],
                'method_validated': False}
    if os.environ.get('MUSA_LAUNCH_BLOCKING') != '1':
        raise RuntimeError('Require process-local launch blocking')
    from diagnose_prefix_gradient import require_inherited_lock
    require_inherited_lock(LOCK)
    import torch
    import torch_musa  # noqa: F401
    import gaosi.meta as meta
    from gaosi.runtime import atomic_save
    torch.set_num_threads(8)
    result = {'status': 'RUNNING', 'entry': manifest['entry'], 'method_validated': False,
              'downstream_promotion_allowed': False, 'formal_data_used': False,
              'start_episode': manifest['start_episode'],
              'executed_inner_updates': manifest['executed_inner_updates'],
              'raw_norm_stop_before_update': THRESHOLD}
    try:
        guarded_train(meta, torch, atomic_save, config, manifest, output)
        result.update(status='COMPLETE_REQUIRES_REVIEW',
                      episodes=len(config['episode_K']), inner_updates=sum(config['episode_K']))
    except BaseException:
        result.update(status='FAILED_EVIDENCE_PRESERVED', error=traceback.format_exc())
        raise
    finally:
        if output.exists():
            write_new(output / 'observed_training_manifest.json', Path(manifest_path).read_bytes())
            write_new(output / 'train_guarded_meta.py', Path(__file__).read_bytes())
            write_new(output / 'observed_training_result.json', (json.dumps(result, indent=2) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--config', required=True)
    prep.add_argument('--entry', choices=sorted(ENTRIES), default='meta')
    prep.add_argument('--out', required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--manifest', required=True)
    execute.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.action == 'prepare':
        value = prepare(args.config, args.entry, args.out)
        print(json.dumps({'status': 'PREPARED_NOT_EXECUTED', 'manifest': args.out,
                          'executed_inner_updates': value['executed_inner_updates']}))
    else:
        print(json.dumps(run(args.manifest, args.validate_only)))


if __name__ == '__main__':
    main()
