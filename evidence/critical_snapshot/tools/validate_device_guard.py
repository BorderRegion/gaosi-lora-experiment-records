"""Real subprocess checks: mutual exclusion, legacy drain, PID and exit behavior."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from device_guard import legacy_jobs, uses_musa


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / 'tools/device_guard.py'
JOB = ROOT / 'tools/job.py'


def ticks(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def wait_file(path, timeout=10):
    end = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= end:
            raise AssertionError(f'Timed out waiting for {path}')
        time.sleep(.02)


def main():
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_guard_') as d:
        root = Path(d)
        (root / 'records/jobs').mkdir(parents=True)
        config = root / 'cpu.json'
        config.write_text(json.dumps({'device': 'cpu'}))
        assert uses_musa([sys.executable, '-m', 'gaosi.train'])
        assert uses_musa([sys.executable, '-u', 'tools/profile_transition.py'])
        assert not uses_musa([sys.executable, '-m', 'gaosi.train', '--config', str(config)])
        for script in ['calibration_queue.py', 'phi_core_queue.py', 'organization_queue.py',
                       'final_parent_queue.py', 'seven_b_early_queue.py', 'validate_continual.py']:
            assert not uses_musa([sys.executable, 'tools/' + script])
        tests['MUSA_leaves_only_no_dispatcher_or_CPU_locks'] = 'PASS'
        lock = str(root / 'musa.lock')
        writer = root / 'writer.py'
        writer.write_text('import json,os,pathlib,sys,time\n'
                          'p=pathlib.Path(sys.argv[1]);p.write_text(json.dumps({"pid":os.getpid(),"start":time.monotonic()}))\n'
                          'time.sleep(float(sys.argv[2]))\n'
                          'v=json.loads(p.read_text());v["end"]=time.monotonic();p.write_text(json.dumps(v))\n')

        def start(name, duration='.2', through_job=False):
            out = root / (name + '.json')
            target = [sys.executable, str(writer), str(out), duration]
            cmd = ([sys.executable, str(JOB), '--name', name, '--musa-lock', lock, '--', *target]
                   if through_job else
                   [sys.executable, str(GUARD), '--job-name', name, '--lock-path', lock, '--', *target])
            log = (root / (name + '.log')).open('w')
            process = subprocess.Popen(cmd, cwd=root, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            return process, out

        first, f = start('first', '.5')
        wait_file(f)
        second, s = start('second')
        first_ticks = ticks(first.pid)
        assert first.wait(timeout=10) == second.wait(timeout=10) == 0
        fv, sv = json.loads(f.read_text()), json.loads(s.read_text())
        assert fv['end'] <= sv['start'] and fv['pid'] == first.pid
        assert int(first_ticks) > 0
        tests['two_real_processes_never_overlap_and_exec_preserves_PID'] = 'PASS'

        holder, h = start('killed', '60')
        wait_file(h)
        waiter, w = start('after_kill')
        holder.terminate()
        holder.wait(timeout=10)
        assert waiter.wait(timeout=10) == 0 and w.exists()
        tests['process_exit_releases_device_without_stale_lock_deadlock'] = 'PASS'

        legacy = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], cwd=root)
        lf = root / 'records/jobs/legacy.json'
        state = {'name': 'legacy', 'command': [sys.executable, '-m', 'gaosi.train'],
                 'status': 'RUNNING', 'child_pid': legacy.pid, 'child_proc_start_ticks': ticks(legacy.pid)}
        lf.write_text(json.dumps(state))
        try:
            blocked, b = start('legacy_wait')
            time.sleep(.3)
            assert not b.exists() and blocked.poll() is None
            legacy.terminate()
            legacy.wait(timeout=10)
            assert blocked.wait(timeout=10) == 0
            assert 'waiting_legacy_device_job' in (root / 'legacy_wait.log').read_text()
        finally:
            if legacy.poll() is None:
                legacy.kill(); legacy.wait()
        tests['live_predeployment_leaf_drains_before_new_training'] = 'PASS'

        state.update(child_pid=os.getpid(), child_proc_start_ticks='0')
        lf.write_text(json.dumps(state))
        assert legacy_jobs(root / 'records/jobs', 'other') == []
        state.update(child_proc_start_ticks=ticks(os.getpid()), status='PASS')
        lf.write_text(json.dumps(state))
        assert legacy_jobs(root / 'records/jobs', 'other') == []
        tests['reused_PID_and_terminal_job_do_not_block'] = 'PASS'

        wrapper, output = start('wrapped', through_job=True)
        assert wrapper.wait(timeout=10) == 0
        state = json.loads((root / 'records/jobs/wrapped.json').read_text())
        assert state['status'] == 'PASS' and state['child_pid'] == json.loads(output.read_text())['pid']
        failed = subprocess.run([sys.executable, str(JOB), '--name', 'failure', '--musa-lock', lock,
                                 '--', sys.executable, '-c', 'raise SystemExit(7)'], cwd=root, capture_output=True)
        state = json.loads((root / 'records/jobs/failure.json').read_text())
        assert failed.returncode == state['exit_code'] == 7 and state['status'] == 'FAIL'
        tests['job_ledger_records_real_child_identity_and_failure_code'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'Linux subprocess locking; no model or MUSA training performed',
              'tests': tests}
    path = ROOT / 'checks/device_guard.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
