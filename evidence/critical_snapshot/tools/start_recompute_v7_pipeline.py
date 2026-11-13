"""Start verified v7 controllers once, preserving all prior queue identities."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def identity(state):
    proc = Path(f"/proc/{state.get('child_pid')}/stat")
    fields = proc.read_text().split() if proc.exists() else []
    return bool(fields and fields[2] != 'Z' and fields[21] == state.get('child_proc_start_ticks'))


def main():
    root = Path.cwd()
    snapshot = root / 'records/pipeline_recompute_v7'
    manifest = json.loads((snapshot / 'build_manifest.json').read_text())
    validation = json.loads((snapshot / 'validation.json').read_text())
    if validation['status'] != 'PASS' or validation['controller_imports'] != 15:
        raise RuntimeError('CPU scheduler checks have not passed')
    for filename, hashes in manifest['sources'].items():
        for file, key in [(root / 'tools' / filename, 'original_sha256'),
                          (snapshot / 'tools' / filename, 'continuation_sha256')]:
            if hashlib.sha256(file.read_bytes()).hexdigest() != hashes[key]:
                raise RuntimeError(f'Controller source changed: {file}')
    config = root / 'configs' / (manifest['adopted_L_job'] + '.json')
    if hashlib.sha256(config.read_bytes()).hexdigest() != manifest['adopted_L_config_sha256']:
        raise RuntimeError('Adopted L configuration changed')
    for artifact in ['phi_core_ready.json', 'phi_architecture_selected.json', 'all_phi_frozen.json',
                     'o4_parents_ready.json', 'final_parents_ready.json', 'formal_recovery_ready_v2.json',
                     'reflow_ready_v1.json', 'continual_pilot_ready_v1.json', 'confirmation_ready_v1.json']:
        if (root / 'records' / artifact).exists():
            raise RuntimeError(f'Existing downstream artifact requires inspection: {artifact}')
    for job in manifest['jobs']:
        if (root / 'records/jobs' / (job['name'] + '.json')).exists():
            raise RuntimeError('Continuation already started; inspect rather than duplicate')
    adopted = json.loads((root / 'records/jobs' / (manifest['adopted_L_job'] + '.json')).read_text())
    if adopted['status'] == 'RUNNING' and not identity(adopted):
        raise RuntimeError('Adopted L process identity is stale')
    if adopted['status'] not in ['RUNNING', 'PASS']:
        raise RuntimeError('Adopted L has failed')
    environment = dict(os.environ)
    environment['PYTHONPATH'] = ':'.join([str(snapshot / 'tools'), str(root / 'tools'), str(root)])
    environment['PYTHONUNBUFFERED'] = '1'
    started = []
    for job in manifest['jobs']:
        command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', job['name'], '--',
                   '/usr/bin/python', '-u', str(snapshot / 'tools' / (job['module'] + '.py'))]
        with (root / 'records/logs' / (job['name'] + '.log')).open('xb', buffering=0) as log:
            process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 20
        record = root / 'records/jobs' / (job['name'] + '.json')
        while not record.exists() or 'child_pid' not in json.loads(record.read_text()):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f'Controller wrapper failed to start: {job["name"]}')
            time.sleep(.1)
        started.append(job['name'])
    time.sleep(5)
    states = {name: json.loads((root / 'records/jobs' / (name + '.json')).read_text()) for name in started}
    observations = {name: {'status': state['status'], 'child_pid': state['child_pid'],
                           'child_proc_start_ticks': state['child_proc_start_ticks'],
                           'identity_live': identity(state)} for name, state in states.items()}
    valid = all(v['status'] == 'RUNNING' and v['identity_live'] for v in observations.values())
    report = {'status': 'WAITING_ON_EXISTING_L' if valid else 'STARTUP_FAILURE_REQUIRES_INSPECTION',
              'observed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'adopted_L_job': manifest['adopted_L_job'], 'controller_processes': observations,
              'numerical_source_sha256': {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                                          for f in sorted((root / 'gaosi').glob('*.py'))},
              'device_lock': '/tmp/gaosi_musa_device0.lock',
              'training_observation_unchanged': 'Last checkpoint/GPU observation remains 06:41 UTC+8',
              'next_training_check_utc': '2026-10-29T23:09:00+00:00'}
    (snapshot / 'launch.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if not valid:
        raise RuntimeError('Inspect startup failures before proceeding')


if __name__ == '__main__':
    main()
