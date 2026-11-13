"""Audit recorded job costs without treating queue wait as device occupancy.

The acquired-to-wrapper-finish interval is a proxy: wrapper cleanup can add a
small delay after the lock is released. This is neither utilization time nor
a provider billing receipt. Incomplete jobs contribute only a recorded lower
bound, never an extrapolation from a stale RUNNING flag to the current time.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def union_seconds(intervals):
    end = None
    total = 0.
    for left, right in sorted(intervals):
        if end is None or left > end:
            total += right - left
        elif right > end:
            total += right - end
        end = right if end is None else max(end, right)
    return total


def job_cost(state, events):
    acquired = [e for e in events if e.get('event') == 'device_acquired'
                and e.get('job') == state['name']]
    row = {'job': state['name'], 'recorded_status': state['status'],
           'wrapper_wall_seconds': state.get('wall_seconds'),
           'device_guard_declared': bool(state.get('device_lock')),
           'timing_status': 'unknown', 'device_seconds': None,
           'queue_wait_seconds': None, 'acquired_at_utc': None,
           'interval_end_utc': None, 'interval_end_source': None}
    if len(acquired) != 1:
        row['timing_status'] = ('ambiguous_multiple_acquisitions' if acquired
                                else 'no_acquisition_evidence')
        return row, None
    acquired_at = acquired[0]['utc']
    left = timestamp(acquired_at)
    start = timestamp(state['started_at_utc'])
    if left < start:
        row['timing_status'] = 'invalid_acquisition_before_wrapper'
        return row, None
    row.update(acquired_at_utc=acquired_at, queue_wait_seconds=left-start)
    if state['status'] in ('PASS', 'FAIL') and state.get('finished_at_utc'):
        end_value = state['finished_at_utc']
        source = 'wrapper_finished_at_utc'
        status = 'completed_occupation_proxy'
    else:
        dated = [e for e in events if e.get('utc') and timestamp(e['utc']) >= left]
        end_value = max(dated, key=lambda e: timestamp(e['utc']))['utc']
        source = 'last_recorded_log_event_not_current_time'
        status = 'recorded_partial_lower_bound'
    right = timestamp(end_value)
    if right < left:
        row['timing_status'] = 'invalid_end_before_acquisition'
        return row, None
    row.update(timing_status=status, device_seconds=right-left,
               interval_end_utc=end_value, interval_end_source=source)
    return row, (left, right)


def build(root):
    rows, sources, complete, partial = [], {}, [], []
    for file in sorted((root / 'records/jobs').glob('*.json')):
        raw = file.read_bytes()
        sources[str(file.relative_to(root))] = hashlib.sha256(raw).hexdigest()
        state = json.loads(raw)
        events = []
        log = root / 'records/logs' / (state['name'] + '.log')
        if log.exists():
            raw = log.read_bytes()
            sources[str(log.relative_to(root))] = hashlib.sha256(raw).hexdigest()
            for line in raw.splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.append(event)
                except (ValueError, UnicodeDecodeError):
                    pass  # Tracebacks and ordinary non-JSON process output.
        row, interval = job_cost(state, events)
        rows.append(row)
        if interval:
            (complete if row['timing_status'] == 'completed_occupation_proxy'
             else partial).append(interval)
    return {
        'schema_version': 1,
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'Only locally available job records/log bytes; not all rental history.',
        'method': 'Guard acquisition to wrapper finish proxy; queue wait separate. '
                  'Incomplete jobs end at last recorded log event, never now. '
                  'Union avoids overlapping/nested intervals being counted twice. '
                  'Failed/diagnostic jobs remain included. CPU/controller wrapper '
                  'time is not summed into device time. No price or utilization inferred.',
        'job_count': len(rows),
        'completed_interval_union_seconds': union_seconds(complete),
        'completed_plus_partial_union_seconds': union_seconds(complete + partial),
        'jobs_without_usable_device_interval': sum(r['device_seconds'] is None for r in rows),
        'jobs': rows, 'source_sha256': sources}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f'Preserve earlier cost evidence: {args.out}')
    report = build(args.root.resolve())
    args.out.mkdir(parents=True)
    (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    with (args.out / 'jobs.csv').open('w', newline='') as handle:
        fields = list(report['jobs'][0]) if report['jobs'] else ['job']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report['jobs'])
    print(json.dumps({k: v for k, v in report.items() if k not in ('jobs', 'source_sha256')}))


if __name__ == '__main__':
    main()
