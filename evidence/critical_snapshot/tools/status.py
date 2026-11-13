"""Compact authoritative status: PID plus /proc start-time, never a stale lock."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--record', action='store_true')
    p.add_argument('--brief', action='store_true')
    a = p.parse_args()
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    jobs = []
    for file in sorted(Path('records/jobs').glob('*.json')):
        data = json.loads(file.read_text())
        pid = data.get('child_pid')
        proc = Path(f'/proc/{pid}/stat')
        live = False
        if proc.exists():
            fields = proc.read_text().split()
            live = fields[21] == data.get('child_proc_start_ticks') and fields[2] != 'Z'
        if data['status'] == 'RUNNING' or live:
            jobs.append({'job': data['name'], 'status': data['status'], 'live': live,
                         'pid': pid, 'process_state': fields[2] if live else None,
                         'command': data['command']})
    events = []
    latest_files = sorted(Path('runs').glob('*/events.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    for file in latest_files:
        with file.open() as f:
            lines = f.readlines()
        data = [json.loads(line) for line in lines]
        last = data[-1]
        evaluation = [x for x in data if x['event'] == 'evaluation']
        events.append({'run': str(file.parent), 'last': last,
                       'last_evaluation': evaluation[-1] if evaluation else None,
                       'checkpoint_mtime_utc': dt.datetime.fromtimestamp((file.parent / 'latest.pt').stat().st_mtime,
                                               dt.timezone.utc).isoformat() if (file.parent / 'latest.pt').exists() else None})
    queue = Path('records/development_queue.json')
    result = {'utc': timestamp, 'jobs': jobs, 'recent_runs': events,
              'queue': json.loads(queue.read_text()) if queue.exists() else None,
              'storage': {path: {'free_bytes': shutil.disk_usage(path).free,
                                 'total_bytes': shutil.disk_usage(path).total}
                          for path in ['/root/autodl-tmp', '/']}}
    if a.record:
        with Path('records/supervision.jsonl').open('a') as f:
            f.write(json.dumps(result) + '\n')
    if a.brief:
        summary = {'utc': timestamp,
                   'jobs': [{k: j[k] for k in ['job', 'status', 'live', 'process_state']} for j in jobs],
                   'recent': [], 'queue': result['queue'],
                   'free_GiB': {p: round(v['free_bytes'] / 2**30, 2) for p, v in result['storage'].items()}}
        for item in events:
            last, evaluation = item['last'], item['last_evaluation'] or {}
            task_metrics = evaluation.get('metrics', {}).get('task', evaluation)
            summary['recent'].append({'run': item['run'], 'last_event': last['event'],
                                      'step': last.get('step'), 'last_utc': last['utc'],
                                      'eval_step': evaluation.get('step'),
                                      'accuracy': task_metrics.get('candidate_accuracy'),
                                      'EM': task_metrics.get('strict_EM'),
                                      'peak_GB': round(last.get('peak_allocated_bytes', 0) / 1e9, 2)})
        print(json.dumps(summary))
    else:
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
