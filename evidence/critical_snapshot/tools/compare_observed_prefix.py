"""Compare a saved observed-training prefix with a completed diagnostic on CPU.

This reads completed episode artifacts only. It neither polls the device nor
updates or qualifies a phi checkpoint for downstream use.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

from compare_prefix_gradients import compare_tensors, read_prefix, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic', type=Path, required=True)
    parser.add_argument('--observed', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--reference-comparison', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists() or any(args.out.resolve().is_relative_to(p.resolve())
                                for p in [args.diagnostic, args.observed]):
        raise ValueError('Preserve run artifacts and previous comparison results')
    diagnostic, observations, reference_inner = read_prefix(args.diagnostic)
    manifest = json.loads(args.manifest.read_text())
    reference = json.loads(args.reference_comparison.read_text())
    pins = manifest['pinned_files_sha256']
    if (manifest['formal_data_used'] is not False
            or diagnostic['diagnostic_environment'].get('MUSA_LAUNCH_BLOCKING') != '1'
            or diagnostic['source_config_sha256'] != pins[manifest['source_config']]
            or any(pins.get(name) != value
                   for name, value in diagnostic['source_code_sha256'].items())):
        raise ValueError('Require the same full source configuration and numerical code')
    config = json.loads((args.observed / 'config.json').read_text())
    source = json.loads((args.diagnostic / 'production_source_config.json').read_text())
    pinned_config = Path(manifest['config'])
    if (config != dict(source, out=config['out'], purpose=config['purpose'])
            or config != json.loads(pinned_config.read_text())
            or sha256(pinned_config) != pins[manifest['config']]):
        raise ValueError('Observed run changed more than output and purpose')
    evidence = {str(args.manifest): sha256(args.manifest),
                str(args.reference_comparison): sha256(args.reference_comparison),
                str(args.observed / 'config.json'): sha256(args.observed / 'config.json')}
    for name, expected in pins.items():
        if name.startswith('gaosi/'):
            preserved = args.observed / 'source' / Path(name).name
            if sha256(preserved) != expected:
                raise ValueError('Preserved observed numerical source differs: ' + name)
            evidence[str(preserved)] = expected
    # These ledgers may grow, but completed rows and episode artifacts are fixed.
    events = [json.loads(line) for line in (args.observed / 'events.jsonl').read_text().splitlines()]
    gradients = [json.loads(line) for line in (args.observed / 'raw_gradients.jsonl').read_text().splitlines()]
    count = len(observations)
    ends = [row['episode'] for row in events if row.get('event') == 'episode_end']
    if ends[:count] != list(range(count)) or [r['episode'] for r in gradients[:count]] != list(range(count)):
        raise ValueError('Observed prefix is incomplete or discontinuous')
    import torch
    torch.set_num_threads(2)
    keys = ['k', 'ld', 'lr', 'task_tokens', 'language_tokens', 'support_forwards',
            'keep_tokens', 'keep_forwards', 'exact_window']
    rows = []
    for episode in range(count):
        left = args.diagnostic / 'repeat_0' / f'gradient_episode_{episode}.pt'
        right = args.observed / f'gradient_episode_{episode:03d}.pt'
        evidence[str(left)], evidence[str(right)] = sha256(left), sha256(right)
        if evidence[str(left)] != reference['raw_evidence_sha256'].get(str(left)):
            raise ValueError('Diagnostic artifact differs from the prior reviewed comparison')
        row = gradients[episode]
        if (evidence[str(right)] != row['artifact_sha256'] or not row['raw_all_finite']
                or not row['phi_unchanged_during_rollout']
                or not row['update_authorized_by_guard'] or row.get('error')):
            raise ValueError('Observed gradient artifact or guard differs from its ledger')
        a, b = [torch.load(path, map_location='cpu', weights_only=False) for path in [left, right]]
        if (a['episode'] != episode or b['episode'] != episode
                or a['source_config_sha256'] != diagnostic['source_config_sha256']
                or b['config_sha256'] != pins[manifest['config']]):
            raise ValueError('Artifact has a different episode or configuration')
        inner = [x for x in events if x.get('event') == 'inner' and x['episode'] == episode]
        if [x['k'] for x in inner] != list(range(1, config['episode_K'][episode] + 1)):
            raise ValueError('Observed inner-step evidence is incomplete')
        forward_equal = len(inner) == len(reference_inner[episode]) and all(
            all(x.get(key) == y.get(key) for key in keys)
            for x, y in zip(reference_inner[episode], inner))
        starting = compare_tensors(torch, a['starting_phi'], b['starting_phi'])
        rows.append({'episode': episode, 'starting_phi': starting,
                     'raw_gradient': compare_tensors(torch, a['raw_gradient'], b['raw_gradient']),
                     'forward_records_equal': forward_equal,
                     'same_starting_phi_and_logged_forward_values': bool(
                         starting.get('bitwise_equal') and forward_equal)})
    result = {'status': 'CPU_COMPARISON_COMPLETE_REQUIRES_SCIENTIFIC_REVIEW',
              'compared_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'diagnostic': str(args.diagnostic), 'observed': str(args.observed),
              'episodes': rows, 'raw_evidence_sha256': evidence,
              'method_validated': False, 'downstream_promotion_allowed': False,
              'production_modified_or_restarted': False,
              'limitations': ['This compares completed prefix artifacts, not the complete training run.',
                             'Repeatability does not establish derivative correctness or the original failure cause.',
                             'Different starting phi prevents same-state interpretation of later gradients.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'episodes': count,
                      'same_start_and_forwards': all(r['same_starting_phi_and_logged_forward_values'] for r in rows),
                      'all_raw_gradients_bitwise_equal': all(r['raw_gradient'].get('bitwise_equal') for r in rows),
                      'output': str(args.out), 'method_validated': False}))


if __name__ == '__main__':
    main()
