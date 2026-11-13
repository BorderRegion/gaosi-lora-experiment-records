"""Load corrected finite-queue code at a verified safe subprocess boundary."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def live(state):
    path = Path(f"/proc/{state['child_pid']}/stat")
    return path.exists() and path.read_text().split()[21] == state['child_proc_start_ticks']


def main():
    old = json.loads(Path('records/jobs/development_queue_v2.json').read_text())
    if live(old):
        # The production profiling controller has already stopped this waiting
        # dispatcher. Terminate only it, never its already-launched training.
        fields = Path(f"/proc/{old['child_pid']}/stat").read_text().split()
        if fields[2] != 'T':
            raise RuntimeError('Dispatcher is not at the paused profile boundary')
        os.kill(old['child_pid'], signal.SIGKILL)
        print('DISPATCHER_CODE_UPGRADE', old['child_pid'], flush=True)
    file = Path('records/jobs/profile_production_queue.json')
    while True:
        state = json.loads(file.read_text())
        if state['status'] != 'RUNNING':
            break
        if not live(state):
            time.sleep(2)
            continue
        time.sleep(30)
    if state['status'] != 'PASS':
        raise RuntimeError('Production profile controller failed; inspect before resuming queue')
    with open('records/logs/development_queue_v3.log', 'ab', buffering=0) as log:
        process = subprocess.Popen(['/usr/bin/python', '-u', 'tools/job.py', '--name', 'development_queue_v3',
                                     '--', '/usr/bin/python', '-u', 'tools/development_queue.py'],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
    print('DISPATCHER_V3_LAUNCHED', process.pid, flush=True)


if __name__ == '__main__':
    main()
