"""Read an archived L/X core ledger; never contact training or grant approval."""
import argparse
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def review(folder, architecture, require_complete=False):
    root = Path(__file__).resolve().parents[2]
    folder = folder.resolve()
    require(folder.parent == root / 'records', 'Expected a local scheduled archive')
    manifest_path = folder / 'archive_source_sha256.json'
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest.items():
        path = (root / name).resolve()
        require(path.is_relative_to(folder), 'Source escapes archive: ' + name)
        require(sha(path) == expected, 'Source hash differs: ' + name)
    run_name = f'runs/phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8'
    run = folder / 'snapshot' / run_name
    inputs = [run / name for name in ['config.json', 'events.jsonl', 'raw_gradients.jsonl']]
    for path in inputs:
        require(str(path.relative_to(root)) in manifest, 'Unmanifested ledger input')
    config = json.loads(inputs[0].read_text())
    require(config['architecture'] == architecture, 'Architecture differs')
    require(config['horizon'] == 'EXACT_H2_tail', 'Expected H2 tail')
    require(config['episode_K'] == [8] * 64 + [16] * 128, 'Core schedule differs')
    require(config.get('start_episode', 0) == 0, 'Expected fresh full core')
    require(not config.get('initial_phi'), 'Expected fresh phi')
    require(config['effective_tokens'] >= 512, 'Insufficient configured route tokens')
    events = [json.loads(line) for line in inputs[1].read_text().splitlines()]
    gradients = [json.loads(line) for line in inputs[2].read_text().splitlines()]
    starts = [row for row in events if row['event'] == 'episode_start']
    ends = [row for row in events if row['event'] == 'episode_end']
    require(0 < len(ends) <= 192, 'No committed core episodes or excess episodes')
    require([row['episode'] for row in ends] == list(range(len(ends))),
            'Committed episodes are not contiguous')
    require([row['episode'] for row in gradients] == list(range(len(ends))),
            'Gradient ledger does not match committed episodes')
    require([row['episode'] for row in starts] == list(range(len(starts))),
            'Episode starts are not unique and contiguous')
    require(len(starts) in (len(ends), min(len(ends) + 1, 192)),
            'Unexpected uncommitted episode count')
    complete = len(ends) == 192
    if require_complete:
        require(complete, 'Incomplete core: completion review requires 192 committed episodes')
        for name in ['result.json', 'observed_training_result.json']:
            path = run / name
            require(str(path.relative_to(root)) in manifest, 'Missing terminal evidence: ' + name)
            inputs.append(path)
        result = json.loads(inputs[-2].read_text())
        sidecar = json.loads(inputs[-1].read_text())
        require(result['status'] == 'PASS' and result['episodes'] == 192
                and result['inner_updates'] == 2560 and result['horizon'] == config['horizon'],
                'Terminal result differs from the core budget')
        require(sidecar['status'] == 'COMPLETE_REQUIRES_REVIEW'
                and sidecar['method_validated'] is False
                and sidecar['downstream_promotion_allowed'] is False
                and sidecar['formal_data_used'] is False, 'Terminal sidecar differs')
    inner = defaultdict(list)
    for row in events:
        if row['event'] == 'inner':
            require(row['episode'] in range(len(starts)), 'Inner step lacks episode start')
            inner[row['episode']].append(row)
    checked = []
    groups = defaultdict(list)
    for end, gradient in zip(ends, gradients):
        episode = end['episode']
        start = starts[episode]
        rows = inner[episode]
        K = config['episode_K'][episode]
        require(start['K'] == K, 'Episode K differs')
        require([row['k'] for row in rows] == list(range(1, K + 1)), 'Inner steps differ')
        require([row['k'] for row in rows if row['exact_window']] == [K - 1, K],
                'Recorded exact window differs from last two steps')
        for row in rows:
            require(all(row[key] >= config['effective_tokens'] for key in
                        ['task_tokens', 'language_tokens', 'keep_tokens']),
                    'Insufficient recorded route tokens')
            require(row['keep_forwards'] > 0 and all(math.isfinite(row[key]) for key in
                    ['ld', 'lr', 'step_seconds']), 'Invalid recorded inner values')
        require(all(math.isfinite(end[key]) for key in
                    ['query_task', 'query_language', 'query_keep', 'objective']),
                'Nonfinite recorded terminal objective')
        require(end['objective'] >= end['query_task'], 'Objective smaller than task loss')
        require(end['inner_updates'] == sum(config['episode_K'][:episode + 1]),
                'Committed update count differs')
        require(math.isfinite(gradient['raw_norm_cpu_fp64']) and gradient['raw_all_finite']
                and gradient['clipped_all_finite'] and gradient['phi_unchanged_during_rollout']
                and gradient['update_authorized_by_guard'] and not gradient.get('error'),
                'Invalid recorded gradient guard')
        require(end['phi_grad_norm'] == gradient['raw_norm_cpu_fp64'], 'Gradient norm differs')
        seconds = (dt.datetime.fromisoformat(end['utc']) -
                   dt.datetime.fromisoformat(start['utc'])).total_seconds()
        require(seconds > 0, 'Invalid recorded episode duration')
        groups[(K, start['mode'], start['q_train'])].append({
            'seconds': seconds, 'raw_norm': gradient['raw_norm_cpu_fp64'],
            'objective': end['objective']})
        checked.extend(rows)
    return {
        'status': 'RECORDED_CORE_INNER_LEDGER_CHECKS_PASS_ONLY',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source': str(Path(__file__).relative_to(root)), 'source_sha256': sha(Path(__file__)),
        'observation': str(folder.relative_to(root)), 'run': run_name,
        'architecture': architecture, 'require_complete': require_complete,
        'full_core_episode_ledger_present': complete,
        'input_sha256': {str(path.relative_to(root)): sha(path) for path in inputs + [manifest_path]},
        'source_files_rehashed': len(manifest), 'completed_episodes_checked': len(ends),
        'completed_inner_steps_checked': len(checked),
        'exact_window_flag_matches_last_two_steps_in_every_completed_episode': True,
        'minimum_recorded_effective_tokens': {key: min(row[key] for row in checked) for key in
                                             ['task_tokens', 'language_tokens', 'keep_tokens']},
        'groups': [{'K': key[0], 'mode': key[1], 'q_train': key[2], 'count': len(rows),
                    'mean_episode_seconds': statistics.mean(row['seconds'] for row in rows),
                    'median_raw_norm': statistics.median(row['raw_norm'] for row in rows),
                    'min_raw_norm': min(row['raw_norm'] for row in rows),
                    'max_raw_norm': max(row['raw_norm'] for row in rows),
                    'median_objective': statistics.median(row['objective'] for row in rows)}
                   for key, rows in sorted(groups.items())],
        'method_validated': False, 'downstream_promotion_allowed': False,
        'limitations': ['Checks recorded flags and tokens, not graph connectivity or derivatives.',
                        'Task routes and noise levels differ; these summaries do not establish convergence.',
                        'Only committed episodes are summarized; live files may reflect adjacent instants.',
                        'Even a full ledger needs job identity verification, tensor artifact audit, Adam replay and a separate scientific review.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', required=True, type=Path)
    parser.add_argument('--architecture', required=True, choices=['L', 'X'])
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    require(args.out.resolve().is_relative_to(root / 'records'), 'Keep review in records')
    require(not args.out.resolve().is_relative_to(args.observation.resolve()),
            'Do not write inside the preserved observation')
    result = review(args.observation, args.architecture, args.require_complete)
    with args.out.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({key: result[key] for key in
                      ['status', 'architecture', 'completed_episodes_checked',
                       'completed_inner_steps_checked', 'full_core_episode_ledger_present']}))


if __name__ == '__main__':
    main()
