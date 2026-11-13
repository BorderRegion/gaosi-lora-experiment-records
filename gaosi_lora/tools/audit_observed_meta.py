"""Audit completed saved meta evidence on CPU; never grants scientific approval.

Supports the existing observed L run and the independent generic wrapper. This
tool rejects incomplete runs and does not inspect telemetry or alter training.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from train_guarded_meta import digest, write_new


def audit(run):
    import torch
    torch.set_num_threads(2)
    config = json.loads((run / 'config.json').read_text())
    result = json.loads((run / 'result.json').read_text())
    sidecar = json.loads((run / 'observed_training_result.json').read_text())
    manifest = json.loads((run / 'observed_training_manifest.json').read_text())
    pins = manifest['pinned_files_sha256']
    start, end = config.get('start_episode', 0), len(config['episode_K'])
    if (result['status'] != 'PASS' or sidecar['status'] != 'COMPLETE_REQUIRES_REVIEW'
            or sidecar['method_validated'] is not False
            or sidecar['downstream_promotion_allowed'] is not False
            or sidecar['formal_data_used'] is not False
            or result['episodes'] != end or result['inner_updates'] != sum(config['episode_K'])
            or result['horizon'] != config['horizon']
            or Path(result['checkpoint']).resolve() != (run / 'latest.pt').resolve()
            or config != json.loads(Path(manifest['config']).read_text())
            or digest(manifest['config']) != pins[manifest['config']]):
        raise ValueError('Require exact completed schedule with unresolved review explicitly recorded')
    evidence = {}

    def remember(path):
        evidence[str(path)] = digest(path)

    for path in [run / 'config.json', run / 'result.json', run / 'observed_training_result.json',
                 run / 'observed_training_manifest.json', run / 'raw_gradients.jsonl',
                 run / 'events.jsonl', run / 'source_sha256.json', Path(manifest['config'])]:
        remember(path)
    for name, expected in pins.items():
        if name.startswith('gaosi/'):
            saved = run / 'source' / Path(name).name
            if digest(saved) != expected:
                raise ValueError('Saved numerical source differs: ' + name)
            remember(saved)
        elif name in ['tools/train_observed_meta.py', 'tools/train_guarded_meta.py']:
            saved = run / Path(name).name
            if digest(saved) != expected:
                raise ValueError('Saved wrapper differs')
            remember(saved)
    source_manifest = json.loads((run / 'source_sha256.json').read_text())
    if any(source_manifest[Path(n).name] != h for n, h in pins.items() if n.startswith('gaosi/')):
        raise ValueError('Numerical source ledger differs from manifest')
    # Current source and pinned inputs must also remain reproducible.
    for name, expected in pins.items():
        if digest(name) != expected:
            raise ValueError('Current pinned input or source changed: ' + name)
        remember(Path(name))
    ledger = [json.loads(line) for line in (run / 'raw_gradients.jsonl').read_text().splitlines()]
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    ends = [x for x in events if x.get('event') == 'episode_end']
    if ([x['episode'] for x in ledger] != list(range(start, end))
            or [x['episode'] for x in ends] != list(range(start, end))):
        raise ValueError('Gradient or committed-episode ledger is incomplete')

    def finite(value):
        if torch.is_tensor(value):
            return bool(torch.isfinite(value).all())
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(v) for v in value)
        return True

    def same_tensors(left, right):
        return left.keys() == right.keys() and all(torch.equal(left[k], right[k]) for k in left)

    rows = []
    for row, terminal in zip(ledger, ends):
        episode = row['episode']
        path = run / f'gradient_episode_{episode:03d}.pt'
        if (digest(path) != row['artifact_sha256'] or not row['raw_all_finite']
                or not row['phi_unchanged_during_rollout'] or not row['clipped_all_finite']
                or not row['update_authorized_by_guard'] or row.get('error')):
            raise ValueError('Gradient guard or artifact differs at episode ' + str(episode))
        remember(path)
        artifact = torch.load(path, map_location='cpu', weights_only=False)
        raw, clipped = artifact['raw_gradient'], artifact['clipped_gradient']
        if (artifact['episode'] != episode or artifact['config_sha256'] != pins[manifest['config']]
                or artifact['phi_unchanged_during_rollout'] is not True
                or raw.keys() != clipped.keys() or raw.keys() != artifact['starting_phi'].keys()
                or not finite(artifact['starting_phi'])
                or not all(g is not None and g.dtype == torch.float32 for g in raw.values())
                or not finite(raw) or not finite(clipped)):
            raise ValueError('Invalid saved tensor evidence at episode ' + str(episode))
        norm = math.sqrt(sum(float(g.double().square().sum()) for g in raw.values()))
        if (not math.isfinite(norm) or norm > manifest['raw_norm_stop_before_update']
                or norm != row['raw_norm_cpu_fp64'] or float(artifact['clip_return']) != norm
                or terminal['phi_grad_norm'] != norm):
            raise ValueError('Saved raw gradient norm differs from terminal/guard')
        expected_clipped = {k: ((g.double() * min(1., 1. / (norm + 1e-6))).float()
                                if norm + 1e-6 > 1. else g) for k, g in raw.items()}
        if not same_tensors(expected_clipped, clipped):
            raise ValueError('Saved clip differs from original stable L2 clip')
        if 'ending_phi_before_update' in artifact and not same_tensors(
                artifact['starting_phi'], artifact['ending_phi_before_update']):
            raise ValueError('Saved phi changed inside episode')
        checkpoint = run / f'before_episode_{episode:03d}.pt'
        if episode > start:
            before = torch.load(checkpoint, map_location='cpu', weights_only=False)
            if (before['config'] != config or before['next_episode'] != episode
                    or before['inner_updates'] != sum(config['episode_K'][:episode])
                    or not finite(before['meta_optimizer'])
                    or not same_tensors(before['phi'], artifact['starting_phi'])):
                raise ValueError('Episode does not start at its recorded committed checkpoint')
            remember(checkpoint)
        elif config.get('initial_phi'):
            initial = Path(config['initial_phi'])
            remember(initial)
            warm = torch.load(initial, map_location='cpu', weights_only=False)
            if not same_tensors(warm['phi'], artifact['starting_phi']):
                raise ValueError('Initial phi differs from the declared warm state')
            if checkpoint.exists():
                if digest(checkpoint) != digest(initial):
                    raise ValueError('Preserved warm state differs')
                remember(checkpoint)
        inner = [x for x in events if x.get('event') == 'inner' and x['episode'] == episode]
        if ([x['k'] for x in inner] != list(range(1, config['episode_K'][episode] + 1))
                or terminal['inner_updates'] != sum(config['episode_K'][:episode + 1])):
            raise ValueError('Inner-step or committed budget ledger differs')
        rows.append({'episode': episode, 'raw_norm_cpu_fp64': norm,
                     'gradient_artifact_sha256': row['artifact_sha256'], 'committed': True})
    checkpoint = run / 'latest.pt'
    final = torch.load(checkpoint, map_location='cpu', weights_only=False)
    remember(checkpoint)
    if (final['config'] != config or final['next_episode'] != end
            or final['inner_updates'] != sum(config['episode_K'])
            or not finite(final['phi']) or not finite(final['meta_optimizer'])
            or (config['organization'] != 'O4' and not result['base_unchanged'])):
        raise ValueError('Final checkpoint is incomplete, nonfinite, or lacks unchanged base evidence')
    for path in sorted(run.glob('phi_*.pt')):
        saved = torch.load(path, map_location='cpu', weights_only=False)
        steps = int(path.stem.split('_')[-1])
        if saved['config'] != config or saved['inner_updates'] != steps or not finite(saved['phi']) or not finite(saved['meta_optimizer']):
            raise ValueError('Learning-curve snapshot differs')
        remember(path)
    required_steps = [s for s in [512, 2048, 2560, 3072]
                      if sum(config['episode_K'][:start]) < s <= sum(config['episode_K'])
                      and s in {sum(config['episode_K'][:i + 1]) for i in range(start, end)}]
    if any(not (run / f'phi_{s:04d}.pt').exists() for s in required_steps):
        raise ValueError('Required learning-curve checkpoint absent')
    return {'status': 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW',
            'audited_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'run': str(run), 'config': config, 'start_episode': start, 'episodes_committed': end - start,
            'executed_inner_updates': sum(config['episode_K'][start:]),
            'cumulative_inner_updates': sum(config['episode_K']),
            'maximum_raw_norm': max(r['raw_norm_cpu_fp64'] for r in rows),
            'episodes': rows, 'evidence_sha256': evidence, 'method_validated': False,
            'downstream_promotion_allowed': False,
            'limitations': ['This verifies artifacts and recorded guards, not MUSA derivative correctness.',
                           'Gradient scale, derivative evidence and scientific eligibility require agent review.',
                           'No checkpoint is trained, resumed, or promoted by this audit.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or args.out.resolve().is_relative_to(args.run.resolve()):
        raise ValueError('Keep a new audit outside preserved run artifacts')
    result = audit(args.run)
    write_new(args.out, (json.dumps(result, indent=2, allow_nan=False) + '\n').encode())
    print(json.dumps({'status': result['status'], 'episodes_committed': result['episodes_committed'],
                      'method_validated': False, 'output': str(args.out)}))


if __name__ == '__main__':
    main()
