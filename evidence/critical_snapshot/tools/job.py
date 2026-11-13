"""Detached-job wrapper with process identity, exit code, and wall time ledger."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from device_guard import DEFAULT_LOCK, uses_musa


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', required=True)
    p.add_argument('--musa-lock', help='Explicit device lock for an additional MUSA leaf command')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    root = Path('records/jobs')
    root.mkdir(parents=True, exist_ok=True)
    statefile = root / (a.name + '.json')
    if statefile.exists():
        raise RuntimeError(f'Job name already exists: {a.name}; preserve its result')
    state = {'name': a.name, 'command': command, 'wrapper_pid': os.getpid(),
             'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'start_epoch': time.time(), 'status': 'RUNNING'}
    execution_command = command
    if a.musa_lock or uses_musa(command):
        lock_path = a.musa_lock or DEFAULT_LOCK
        state['device_lock'] = {'path': lock_path, 'version': 1}
        execution_command = [os.sys.executable, '-u', str(Path(__file__).with_name('device_guard.py').resolve()),
                             '--job-name', a.name, '--lock-path', lock_path, '--', *command]

    def save():
        temp = statefile.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2))
        temp.replace(statefile)

    save()
    try:
        child = subprocess.Popen(execution_command)
        state['child_pid'] = child.pid
        state['child_proc_start_ticks'] = Path(f'/proc/{child.pid}/stat').read_text().split()[21]
        save()
        code = child.wait()
        state.update(status='PASS' if code == 0 else 'FAIL', exit_code=code)
    except BaseException:
        state.update(status='FAIL', error=traceback.format_exc())
        raise
    finally:
        state['wall_seconds'] = time.time() - state['start_epoch']
        state['finished_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
        save()
    raise SystemExit(state.get('exit_code', 1))


if __name__ == '__main__':
    main()
