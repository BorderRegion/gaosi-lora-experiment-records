"""Hand-calculated cost accounting checks; no training or device reads."""
import copy
import json
from pathlib import Path
import tempfile

from summarize_job_costs import build, job_cost, union_seconds


def main():
    state = {'name': 'sample', 'status': 'PASS',
             'started_at_utc': '2026-10-29T00:00:00+00:00',
             'finished_at_utc': '2026-10-29T00:02:00+00:00',
             'wall_seconds': 120, 'device_lock': {'path': '/example'}}
    events = [{'event': 'waiting_device', 'utc': '2026-10-29T00:00:00+00:00'},
              {'event': 'device_acquired', 'job': 'sample',
               'utc': '2026-10-29T00:00:30+00:00'},
              {'event': 'inner', 'utc': '2026-10-29T00:01:00+00:00'}]
    tests = []
    row, _ = job_cost(state, events)
    assert row['queue_wait_seconds'] == 30 and row['device_seconds'] == 90
    tests.append('thirty_seconds_queue_excluded_from_two_minute_wrapper')
    row, _ = job_cost(dict(state, status='FAIL'), events)
    assert row['device_seconds'] == 90
    tests.append('failed_run_cost_retained')
    running = {k: v for k, v in state.items() if k not in ('finished_at_utc', 'wall_seconds')}
    running['status'] = 'RUNNING'
    row, _ = job_cost(running, events)
    assert row['device_seconds'] == 30
    assert row['timing_status'] == 'recorded_partial_lower_bound'
    tests.append('stale_running_status_never_extended_to_now')
    row, interval = job_cost(running, events[:1])
    assert row['device_seconds'] is None and interval is None
    tests.append('waiting_only_and_missing_acquisition_not_device_time')
    row, interval = job_cost(state, events + [events[1]])
    assert row['timing_status'] == 'ambiguous_multiple_acquisitions' and interval is None
    tests.append('ambiguous_repeated_acquisition_rejected')
    assert union_seconds([(0, 90), (30, 60), (80, 120), (130, 140)]) == 130
    tests.append('overlap_nested_and_separate_intervals_counted_once')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / 'records/jobs').mkdir(parents=True)
        (root / 'records/logs').mkdir(parents=True)
        (root / 'records/jobs/sample.json').write_text(json.dumps(state))
        cpu = copy.deepcopy(state)
        cpu.update(name='cpu', device_lock=None)
        (root / 'records/jobs/cpu.json').write_text(json.dumps(cpu))
        (root / 'records/logs/sample.log').write_text(
            'ordinary output\n' + '\n'.join(json.dumps(e) for e in events) + '\n{"partial":')
        report = build(root)
        assert report['job_count'] == 2
        assert report['completed_interval_union_seconds'] == 90
        assert report['completed_plus_partial_union_seconds'] == 90
        assert report['jobs_without_usable_device_interval'] == 1
        assert len(report['source_sha256']) == 3
        tests.append('file_aggregation_ignores_cpu_time_preserves_hashes_and_partial_log')
    print(json.dumps({'status': 'PASS', 'scope': 'CPU fixtures only; no actual cost measurement',
                      'tests': tests}))


if __name__ == '__main__':
    main()
