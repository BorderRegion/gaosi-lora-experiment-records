"""Launch one distinct postprocessing continuation after a verified terminal predecessor."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = ROOT / 'records/horizon_effect_comparison_20261103_1005'
RECORD = Path(__file__).resolve().parent
NAME = 'phi_horizon_effect_comparison_s17_noise_timing_v2'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def identity(job):
    fields = Path('/proc', str(job['child_pid']), 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == job['child_proc_start_ticks']
    return {'pid': job['child_pid'], 'start_ticks': fields[19]}


def main():
    assert Path.cwd() == ROOT
    receiptpath = RECORD / 'launch_continuation_v2.json'
    jobfile = ROOT / 'records/jobs' / (NAME + '.json')
    logfile = ROOT / 'records/logs' / (NAME + '.log')
    assert not any(p.exists() for p in [receiptpath, jobfile, logfile, HERE / 'comparison_v2.json'])
    predecessor_path = ROOT / 'records/jobs/phi_horizon_effect_comparison_s17_v1.json'
    predecessor = read(predecessor_path)
    assert predecessor['status'] == 'FAIL' and predecessor['exit_code'] == 1
    try:
        identity(predecessor)
    except (FileNotFoundError, AssertionError):
        pass
    else:
        raise AssertionError('Predecessor is still live')
    validation_path = RECORD / 'remote_validation.json'
    assert sha(validation_path) == 'f1e973cb3e262d6dac9d38faa2ee12272438c87b83e0312c2ff431a6ef70f437'
    validation = read(validation_path)
    assert validation['status'] == 'REAL_CPU_H1_H2_PAIR_AND_STRICT_GATE_VALIDATION_PASS'
    assert not validation['numerical_training_executed']
    for name, expected in validation['evidence_sha256'].items():
        assert sha(ROOT / name) == expected, name
    assert sha(ROOT / validation['pair_review']) == validation['pair_review_sha256']
    oldsource = HERE / 'compare.py'
    assert sha(oldsource) == '0db6c3d8b589957f99d3ae21fb08c2504d199c5838f63124ffa2f56221b9d775'
    source = HERE / 'compare_noise_timing_v2.py'
    assert sha(source) == 'ef3208e1a8a173c864fd287dbd51a418037c096bc60b9c8391aff9bbc60bd37a'
    auditpath = HERE / 'phi_horizon_L_H1_2560_t0_clean_s17_v1.json'
    assert sha(auditpath) == '2b37bf680a34cdb6d764aacdfc467e243e75f580734ec43e4eab361c6e5f332d'
    audit = read(auditpath)
    for name, expected in audit['evidence_sha256'].items():
        assert sha(ROOT / name) == expected, name
    declared = read(HERE / 'declared_pairs.json')
    assert len(declared) == 10
    first = Path(declared[0]['H1']['out'])
    assert read(ROOT / 'records/jobs' / (first.name + '.json'))['status'] == 'PASS'
    completed_snapshot = {str(p): sha(p) for p in [ROOT / first / 'result.json', ROOT / first / 'latest.pt',
        ROOT / 'records/jobs' / (first.name + '.json'), ROOT / 'configs' / (first.name + '.json')]}
    for pair in declared[1:]:
        run = Path(pair['H1']['out'])
        assert not any(p.exists() for p in [ROOT / run, ROOT / 'records/jobs' / (run.name + '.json'),
            ROOT / 'records/logs' / (run.name + '.log'), ROOT / 'configs' / (run.name + '.json')])
    original = {}
    for item in read(ROOT / 'records/pipeline_observed_blocking_v8/launch.json')['controllers']:
        job = read(ROOT / 'records/jobs' / (item['name'] + '.json'))
        assert job['status'] in ['RUNNING', 'PASS']
        original[item['name']] = {'status': job['status'], **(identity(job) if job['status'] == 'RUNNING' else {})}
    assert len(original) == 15 and sum(v['status'] == 'RUNNING' for v in original.values()) == 14
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    instance = HERE / 'controller_instance.lock'
    with instance.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        import torch
        torch.set_num_threads(2)
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_file_location('comparison_v2', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ready = module.run(module.read(HERE / 'inputs.json'), check_only=True)
        assert ready['status'] == 'READY_NOT_STARTED' and ready['pairs'] == 10
        command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', NAME, '--',
                   '/usr/bin/python', '-u', str(source.relative_to(ROOT)), 'run']
        env = dict(os.environ, OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', MUSA_LAUNCH_BLOCKING='1')
        with logfile.open('xb', buffering=0) as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, env=env, pass_fds=(lock.fileno(),))
            deadline = time.monotonic() + 15
            job = None
            while time.monotonic() < deadline:
                if jobfile.exists():
                    job = read(jobfile)
                    if 'child_proc_start_ticks' in job:
                        break
                assert child.poll() is None, 'Inspect failed continuation; never repeat this launch'
                time.sleep(.1)
            assert job and job['status'] == 'RUNNING' and job['wrapper_pid'] == child.pid
            current_identity = identity(job)
        for name, expected in original.items():
            current = read(ROOT / 'records/jobs' / (name + '.json'))
            assert current['status'] == expected['status']
            if current['status'] == 'RUNNING':
                assert identity(current) == {k: expected[k] for k in ['pid', 'start_ticks']}
        assert read(predecessor_path) == predecessor
        assert all(sha(Path(p)) == h for p, h in completed_snapshot.items())
        receipt = {
            'status': 'ONE_DISTINCT_V2_POSTPROCESSING_CONTINUATION_STARTED_REUSING_COMPLETED_FIRST_H1',
            'launched_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'job': job, 'controller_identity': current_identity, 'command': command,
            'comparison_source_sha256': sha(source), 'launcher_sha256': sha(Path(__file__)),
            'predecessor_preserved': predecessor, 'predecessor_job_sha256': sha(predecessor_path),
            'validation_sha256': sha(validation_path), 'first_H1_audit_sha256': sha(auditpath),
            'completed_first_H1_files_unchanged': completed_snapshot,
            'original_15_controller_identities_unchanged': original,
            'controller_instance_lock_inode': instance.stat().st_ino,
            'device_lock_inode': Path('/tmp/gaosi_musa_device0.lock').stat().st_ino,
            'remaining_numerical_jobs': 9, 'completed_numerical_jobs_retried': False,
            'training_sources_or_configs_changed': False, 'original_controllers_restarted': False,
            'method_validated': False, 'full_goal_complete': False}
        with receiptpath.open('x') as handle:
            handle.write(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({'status': receipt['status'], 'receipt_sha256': sha(receiptpath),
                          'controller_identity': current_identity, 'job': job}))


if __name__ == '__main__':
    main()
