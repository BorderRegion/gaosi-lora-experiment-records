"""Inspect an immutable scheduled snapshot; never access training or approve it."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    folder = args.observation.resolve()
    assert folder.parent == root / 'records'
    manifest = json.loads((folder / 'archive_source_sha256.json').read_text())
    for name, expected in manifest.items():
        source = (root / name).resolve()
        assert source.is_relative_to(folder)
        assert sha(source) == expected, name
    run = folder / 'snapshot/runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
    config = json.loads((run / 'config.json').read_text())
    assert config['horizon'] == 'EXACT_H2_tail'
    assert config['episode_K'] == [8] * 64 + [16] * 128
    events = [json.loads(x) for x in (run / 'events.jsonl').read_text().splitlines()]
    gradients = [json.loads(x) for x in (run / 'raw_gradients.jsonl').read_text().splitlines()]
    starts = {r['episode']: r for r in events if r['event'] == 'episode_start'}
    ends = [r for r in events if r['event'] == 'episode_end']
    assert [r['episode'] for r in ends] == list(range(len(ends)))
    assert [r['episode'] for r in gradients] == list(range(len(ends)))
    inner = defaultdict(list)
    groups = defaultdict(list)
    checked = []
    for row in events:
        if row['event'] == 'inner':
            inner[row['episode']].append(row)
    for end, gradient in zip(ends, gradients):
        episode = end['episode']
        start = starts[episode]
        rows = inner[episode]
        K = config['episode_K'][episode]
        assert start['K'] == K
        assert [r['k'] for r in rows] == list(range(1, K + 1))
        assert [r['k'] for r in rows if r['exact_window']] == [K - 1, K]
        for row in rows:
            for key in ['task_tokens', 'language_tokens', 'keep_tokens']:
                assert row[key] >= config['effective_tokens'], (episode, row['k'], key)
            assert row['keep_forwards'] > 0
            assert all(math.isfinite(row[key]) for key in ['ld', 'lr', 'step_seconds'])
        assert all(math.isfinite(end[key]) for key in
                   ['query_task', 'query_language', 'query_keep', 'objective'])
        assert end['objective'] >= end['query_task']
        seconds = (dt.datetime.fromisoformat(end['utc']) -
                   dt.datetime.fromisoformat(start['utc'])).total_seconds()
        assert seconds > 0
        groups[(K, start['mode'], start['q_train'])].append({
            'seconds': seconds, 'raw_norm': gradient['raw_norm_cpu_fp64'],
            'objective': end['objective']})
        checked.extend(rows)
    assert checked
    result = {
        'status': 'RECORDED_INNER_LEDGER_CHECKS_PASS_ONLY',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source': str(Path(__file__).relative_to(root)), 'source_sha256': sha(Path(__file__)),
        'observation': str(folder.relative_to(root)),
        'input_sha256': {str(p.relative_to(root)): sha(p) for p in
                         [run / 'config.json', run / 'events.jsonl', run / 'raw_gradients.jsonl',
                          folder / 'archive_source_sha256.json']},
        'source_files_rehashed': len(manifest), 'completed_episodes_checked': len(ends),
        'completed_inner_steps_checked': len(checked),
        'exact_window_flag_matches_last_two_steps_in_every_completed_episode': True,
        'minimum_recorded_effective_tokens': {k: min(r[k] for r in checked) for k in
                                             ['task_tokens', 'language_tokens', 'keep_tokens']},
        'groups': [{'K': key[0], 'mode': key[1], 'q_train': key[2], 'count': len(rows),
                    'mean_episode_seconds': statistics.mean(r['seconds'] for r in rows),
                    'median_raw_norm': statistics.median(r['raw_norm'] for r in rows),
                    'min_raw_norm': min(r['raw_norm'] for r in rows),
                    'max_raw_norm': max(r['raw_norm'] for r in rows),
                    'median_objective': statistics.median(r['objective'] for r in rows)}
                   for key, rows in sorted(groups.items())],
        'method_validated': False, 'downstream_promotion_allowed': False,
        'limitations': ['Checks recorded flags and token counts, not actual graph connectivity or derivatives.',
                        'Task routes and noise levels differ; these summaries do not establish convergence.',
                        'Only completed episodes are included; this is not a final run audit.']}
    with args.out.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: result[k] for k in ['status', 'completed_episodes_checked',
                                           'completed_inner_steps_checked',
                                           'minimum_recorded_effective_tokens']}))


if __name__ == '__main__':
    main()
