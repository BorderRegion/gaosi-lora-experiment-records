"""Launch the frozen supplementary comparison once; never restart any job."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path('records/horizon_effect_comparison_20261103_1005')
NAME = 'phi_horizon_effect_comparison_s17_v1'


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def identity(job):
    proc = Path('/proc') / str(job['child_pid'])
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == job['child_proc_start_ticks']
    return {'pid': job['child_pid'], 'start_ticks': job['child_proc_start_ticks']}


def main():
    assert Path.cwd().resolve() == ROOT
    out = HERE / 'launch_20261106_0800.json'
    assert not out.exists()
    with (HERE / 'controller_instance.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
        assert sha(HERE / 'compare.py') == '0db6c3d8b589957f99d3ae21fb08c2504d199c5838f63124ffa2f56221b9d775'
        jobfile = Path('records/jobs') / (NAME + '.json')
        logfile = Path('records/logs') / (NAME + '.log')
        assert not jobfile.exists() and not logfile.exists()
        assert not (HERE / 'plan.json').exists() and not (HERE / 'comparison.json').exists()
        for pair in read(HERE / 'declared_pairs.json'):
            leaf = Path(pair['H1']['out'])
            assert not leaf.exists()
            assert not Path('records/jobs', leaf.name + '.json').exists()
            assert not Path('records/logs', leaf.name + '.log').exists()
            assert not Path('configs', leaf.name + '.json').exists()
        original = {}
        for item in read('records/pipeline_observed_blocking_v8/launch.json')['controllers']:
            job = read(Path('records/jobs', item['name'] + '.json'))
            assert job['status'] in ('RUNNING', 'PASS')
            original[item['name']] = {'status': job['status'],
                **(identity(job) if job['status'] == 'RUNNING' else {})}
        assert len(original) == 15 and sum(j['status'] == 'RUNNING' for j in original.values()) == 14
        source = Path('records/development_completion_20261104_2358/phi_dev_L_2560_t4_medium_s17.json')
        assert sha(source) == '7a6c073389ac0ba3bffdecf472c3350a1316f4066a3b460993b452b652d195b1'
        audit = read(source)
        assert audit['status'] == 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
        for path, expected in audit['evidence_sha256'].items():
            assert sha(path) == expected, path
        import torch
        torch.set_num_threads(2)
        spec = importlib.util.spec_from_file_location('frozen_horizon_comparison', HERE / 'compare.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ready = mod.run(mod.read(HERE / 'inputs.json'), check_only=True)
        assert ready['status'] == 'READY_NOT_STARTED' and ready['pairs'] == 10
        command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', NAME, '--',
                   '/usr/bin/python', '-u', str(HERE / 'compare.py'), 'run']
        env = dict(os.environ, OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', MUSA_LAUNCH_BLOCKING='1')
        with logfile.open('xb', buffering=0) as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True, env=env, pass_fds=(lock.fileno(),))
            deadline = time.monotonic() + 15
            job = None
            while time.monotonic() < deadline:
                if jobfile.exists():
                    job = read(jobfile)
                    if 'child_proc_start_ticks' in job:
                        break
                assert child.poll() is None, 'New controller exited; inspect preserved log, never relaunch'
                time.sleep(.1)
            assert job and job['status'] == 'RUNNING' and job['wrapper_pid'] == child.pid
            child_identity = identity(job)
            fields = Path('/proc', str(child.pid), 'stat').read_text().rsplit(')', 1)[1].split()
            wrapper_identity = {'pid': child.pid, 'start_ticks': fields[19]}
        for name, expected in original.items():
            current = read(Path('records/jobs', name + '.json'))
            assert current['status'] == expected['status']
            if current['status'] == 'RUNNING':
                assert identity(current) == {k: expected[k] for k in ['pid', 'start_ticks']}
        receipt = {
            'status': 'SINGLE_DETACHED_FROZEN_HORIZON_COMPARISON_CONTROLLER_STARTED',
            'launched_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'job': job, 'wrapper_identity': wrapper_identity, 'controller_identity': child_identity,
            'controller_instance_lock': str(HERE / 'controller_instance.lock'),
            'instance_lock_inode': (HERE / 'controller_instance.lock').stat().st_ino,
            'device_lock_inode': Path('/tmp/gaosi_musa_device0.lock').stat().st_ino,
            'command': command, 'readiness': ready, 'last_H2_cell_audit': str(source),
            'last_H2_cell_audit_sha256': sha(source), 'frozen_inputs_sha256': sha(HERE / 'inputs.json'),
            'comparison_source_sha256': sha(HERE / 'compare.py'),
            'launcher_sha256': sha(Path(__file__)), 'original_15_controllers_unchanged': original,
            'env_selected': {k: env[k] for k in ['OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'MUSA_LAUNCH_BLOCKING']},
            'numerical_leaves_use_existing_guarded_pipeline_and_GPU_lock': True,
            'controller_holds_GPU_lock': False, 'original_controllers_restarted': False,
            'frozen_numerical_sources_modified': False, 'scientific_promotion_created': False,
            'method_validated': False, 'full_goal_complete': False}
        with out.open('x') as handle:
            handle.write(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({'receipt': str(out), 'sha256': sha(out), 'status': receipt['status'],
            'controller_identity': child_identity, 'wrapper_identity': wrapper_identity, 'job': job}))


if __name__ == '__main__':
    main()
