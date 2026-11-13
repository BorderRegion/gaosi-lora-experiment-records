"""Serialize MUSA leaf processes, including the transition from old wrappers."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import time


DEFAULT_LOCK = '/tmp/gaosi_musa_device0.lock'
MODULES = {'gaosi.train', 'gaosi.recover', 'gaosi.meta', 'gaosi.continual', 'gaosi.references',
           'gaosi.reflow', 'gaosi.capacity_reference', 'gaosi.scale', 'gaosi.base_boundary',
           'gaosi.horizon', 'gaosi.confirmation'}
SCRIPTS = {'profile_transition.py', 'build_o4_history.py', 'calibrate_noise.py'}


def uses_musa(command):
    if '-m' in command:
        index = command.index('-m') + 1
        recognized = index < len(command) and command[index] in MODULES
    else:
        recognized = any(Path(part).name in SCRIPTS for part in command)
    if not recognized:
        return False
    if '--device' in command:
        return command[command.index('--device') + 1].startswith('musa')
    if '--config' in command:
        config = json.loads(Path(command[command.index('--config') + 1]).read_text())
        return config.get('device', 'musa').startswith('musa')
    return True


def live_identity(pid, start_ticks):
    if pid is None or start_ticks is None:
        return False
    try:
        tail = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        return tail[0] != 'Z' and tail[19] == str(start_ticks)
    except (FileNotFoundError, ProcessLookupError):
        return False


def legacy_jobs(jobs_dir, own_name):
    result = []
    for file in sorted(Path(jobs_dir).glob('*.json')):
        state = json.loads(file.read_text())
        if (state.get('name') == own_name or state.get('status') != 'RUNNING'
                or state.get('device_lock') or not uses_musa(state.get('command', []))):
            continue
        if live_identity(state.get('child_pid'), state.get('child_proc_start_ticks')):
            result.append(state['name'])
    return result


def event(kind, **data):
    print(json.dumps({'event': kind, 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                      **data}), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job-name', required=True)
    p.add_argument('--lock-path', default=DEFAULT_LOCK)
    p.add_argument('--jobs-dir', default='records/jobs')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not command:
        p.error('A leaf command is required')
    # Do not unlink the lock: all cooperating processes must use the same inode.
    with open(a.lock_path, 'a') as lock:
        event('waiting_device', job=a.job_name, lock=a.lock_path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        previous = None
        while True:
            running = legacy_jobs(a.jobs_dir, a.job_name)
            if not running:
                break
            if running != previous:
                event('waiting_legacy_device_job', job=a.job_name, legacy=running)
                previous = running
            time.sleep(1)
        event('device_acquired', job=a.job_name, lock=a.lock_path)
        # exec preserves the child PID/start ticks already recorded by job.py.
        # Keeping this descriptor across exec holds the lock for the full leaf.
        os.set_inheritable(lock.fileno(), True)
        os.execvpe(command[0], command, os.environ)


if __name__ == '__main__':
    main()
