"""Pause only the dispatcher, drain live training, profile, resume dispatcher."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def live(state):
    proc = Path(f"/proc/{state['child_pid']}/stat")
    if not proc.exists():
        return False
    fields = proc.read_text().split()
    return fields[21] == state['child_proc_start_ticks'] and fields[2] != 'Z'


def main():
    queuefile = Path('records/jobs/development_queue_v2.json')
    queue = json.loads(queuefile.read_text())
    if queue['status'] != 'RUNNING' or not live(queue):
        raise RuntimeError('Expected live development dispatcher')
    pid = queue['child_pid']
    os.kill(pid, signal.SIGSTOP)
    print('DISPATCHER_PAUSED', pid, flush=True)
    try:
        # Any already-running training is allowed to finish; no restart, kill,
        # or duplicate training. A stale RUNNING file does not count as live.
        for path in sorted(Path('records/jobs').glob('*.json')):
            state = json.loads(path.read_text())
            command = ' '.join(state['command'])
            if state['status'] != 'RUNNING' or '-m gaosi.train' not in command:
                continue
            print('DRAIN_TRAINING', state['name'], state['child_pid'], flush=True)
            while live(state):
                time.sleep(30)
                state = json.loads(path.read_text())
            while state['status'] == 'RUNNING':
                time.sleep(2)
                state = json.loads(path.read_text())
            print('DRAIN_RESULT', state['name'], state['status'], flush=True)
        for architecture in ['L', 'X']:
            name = f'profile_3b_{architecture}_H2_production_recomputed'
            with Path(f'records/logs/{name}.log').open('ab', buffering=0) as log:
                code = subprocess.call(['/usr/bin/python', '-u', 'tools/job.py', '--name', name,
                                        '--', '/usr/bin/python', '-u', 'tools/profile_transition.py',
                                        '--checkpoint', 'runs/capacity_3b_all7_r32_lr1e-4_s17_v2/latest.pt',
                                        '--out', f'checks/{name}.json', '--seq', '512',
                                        '--effective-tokens', '512', '--architecture', architecture,
                                        '--horizon', 'EXACT_H2_tail'], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            print('PROFILE_EXIT', name, code, flush=True)
    finally:
        current = json.loads(queuefile.read_text())
        if live(current):
            os.kill(pid, signal.SIGCONT)
            print('DISPATCHER_RESUMED', pid, flush=True)


if __name__ == '__main__':
    main()
