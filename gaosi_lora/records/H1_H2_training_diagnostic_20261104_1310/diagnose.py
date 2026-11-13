"""Describe archived training losses; no held-out efficacy test or promotion."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
LEDGER = ROOT / 'records/H1_partial_inner_review_20261104_1254.json'
H1 = ROOT / ('records/pipeline_monitor_20261104_125413_utc/snapshot/runs/'
             'phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8/events.jsonl')
H2 = ROOT / ('records/observed_blocking_core_monitor_20261101_070654_utc/snapshot/runs/'
             'phi_mix_O1_L_H2_core_s17_observed_blocking_v8/events.jsonl')
METRICS = ('objective', 'query_task', 'query_language', 'query_keep')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ledger = json.loads(LEDGER.read_text())
    assert ledger['status'] == 'RECORDED_COMMONWARM_H1_LEDGER_CHECKS_PASS_ONLY'
    assert ledger['recorded_task_noise_and_route_budgets_match_H2']
    rows, starts, ends = [], [], []
    for path in (H1, H2):
        assert sha(path) == ledger['input_sha256'][str(path.relative_to(ROOT))]
        values = [json.loads(s) for s in path.read_text().splitlines()]
        rows.append(values)
        starts.append({r['episode']: r for r in values if r['event'] == 'episode_start'})
        ends.append({r['episode']: r for r in values if r['event'] == 'episode_end'})
    ids = sorted(ends[0])
    assert ids == list(range(64, 156))
    assert len(ids) == ledger['completed_additional_episodes'] == 92
    for episode in ids:
        a, b = starts[0][episode], starts[1][episode]
        for key in ('mode', 'K', 'q_train', 'task_file', 'parent_file', 'retain_file', 'retain_route'):
            assert a[key] == b[key]
        noise_a = {k: v for k, v in a['noise'].items() if k != 'boundary_seconds'}
        noise_b = {k: v for k, v in b['noise'].items() if k != 'boundary_seconds'}
        assert noise_a == noise_b
        for metric in METRICS:
            assert all(math.isfinite(e[episode][metric]) for e in ends)

    def describe(group):
        result = {'n_training_episodes': len(group)}
        for metric in METRICS:
            a = [ends[0][i][metric] for i in group]
            b = [ends[1][i][metric] for i in group]
            differences = [x - y for x, y in zip(a, b)]
            result[metric] = {
                'H1_mean': statistics.mean(a), 'H2_mean': statistics.mean(b),
                'H1_median': statistics.median(a), 'H2_median': statistics.median(b),
                'mean_H1_minus_H2': statistics.mean(differences),
                'median_H1_minus_H2': statistics.median(differences),
                'H1_lower_count': sum(d < 0 for d in differences),
                'H1_higher_count': sum(d > 0 for d in differences),
                'equal_count': sum(d == 0 for d in differences),
            }
        return result

    mode_groups = {mode: describe([i for i in ids if starts[0][i]['mode'] == mode])
                   for mode in sorted({starts[0][i]['mode'] for i in ids})}
    dose_groups = []
    for mode, dose in sorted({(starts[0][i]['mode'], starts[0][i]['q_train']) for i in ids}):
        group = [i for i in ids if (starts[0][i]['mode'], starts[0][i]['q_train']) == (mode, dose)]
        dose_groups.append({'mode': mode, 'q_train': dose, **describe(group)})
    largest = sorted(ids, key=lambda i: abs(ends[0][i]['objective'] - ends[1][i]['objective']),
                     reverse=True)[:5]
    first_inner = [{r['k']: r for r in values if r['event'] == 'inner' and r['episode'] == ids[0]}
                   for values in rows]
    assert set(first_inner[0]) == set(first_inner[1]) == set(range(1, 17))
    first_differences = [{'k': k, **{metric + '_H1_minus_H2': first_inner[0][k][metric] -
                                  first_inner[1][k][metric] for metric in ('ld', 'lr')}}
                         for k in range(1, 17)]
    result = {
        'status': 'ARCHIVED_PARTIAL_TRAINING_DIAGNOSTIC_ONLY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source': str(Path(__file__).relative_to(ROOT)), 'source_sha256': sha(Path(__file__)),
        'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in (LEDGER, H1, H2)},
        'episode_start_inclusive': 64, 'episode_end_inclusive': 155,
        'paired_training_episodes': len(ids), 'mode_groups': mode_groups, 'dose_groups': dose_groups,
        'largest_absolute_objective_differences': [
            {'episode': i, 'mode': starts[0][i]['mode'], 'q_train': starts[0][i]['q_train'],
             'H1_objective': ends[0][i]['objective'], 'H2_objective': ends[1][i]['objective']}
            for i in largest],
        'first_compared_episode_support_loss_differences': first_differences,
        'heldout_results_used': False, 'statistical_significance_claimed': False,
        'method_validated': False, 'downstream_promotion_allowed': False, 'full_goal_complete': False,
        'training_modified_or_restarted': False,
        'limitations': [
            'Training objectives under changing phi are not held-out endpoint efficacy results.',
            'There is one phi seed, incomplete H1, heterogeneous tasks, and no Adam comparator here.',
            'Episode counts are not independent phi seeds; no significance interval is inferred.',
            'Means can be dominated by a few high-loss tasks; retain paired medians and individual outliers.',
            'The first common-warm episode has small numerical differences near the differentiable tail.',
            'Logs do not isolate BF16 execution differences from a pure gradient-horizon effect.',
            'The frozen endpoint comparison remains required; no development choice is changed.',
        ],
    }
    with (RECORD / 'diagnostic.json').open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': result['status'], 'paired_training_episodes': len(ids),
                      'mode_groups': mode_groups, 'method_validated': False}))


if __name__ == '__main__':
    main()
