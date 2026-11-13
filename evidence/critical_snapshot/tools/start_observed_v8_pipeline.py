"""Launch the validated continuation only after exact completed L evidence review."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from guarded_pipeline import SNAPSHOT, ADOPTED, CONFIG_SHA, verify_review
from train_guarded_meta import digest


def preflight():
    build = json.loads((SNAPSHOT / 'build_manifest.json').read_text())
    validation = json.loads((SNAPSHOT / 'validation.json').read_text())
    if validation['status'] != 'PASS' or validation['controller_imports'] != 15:
        raise RuntimeError('Independent CPU scheduler/audit/routing checks have not passed')
    for filename, expected in build['sources'].items():
        for file, key in [(Path('tools') / filename, 'original_sha256'),
                          (SNAPSHOT / 'tools' / filename, 'continuation_sha256')]:
            if digest(file) != expected[key]:
                raise RuntimeError('Scheduler source changed: ' + str(file))
    for group in ['helpers_sha256', 'numerical_source_sha256']:
        for name, expected in build[group].items():
            if digest(name) != expected:
                raise RuntimeError('Pinned helper/numerical source changed: ' + name)
    if digest(Path('configs', ADOPTED + '.json')) != CONFIG_SHA:
        raise RuntimeError('Adopted configuration changed')
    state = json.loads(Path('records/jobs', ADOPTED + '.json').read_text())
    if state['status'] != 'PASS':
        raise RuntimeError('The existing L must finish and be reviewed before launching downstream')
    verify_review(Path('runs') / ADOPTED, SNAPSHOT / 'reviews' / (ADOPTED + '.json'))
    for artifact in ['phi_core_ready.json', 'phi_architecture_selected.json', 'all_phi_frozen.json',
                     'o4_parents_ready.json', 'final_parents_ready.json', 'formal_recovery_ready_v2.json',
                     'reflow_ready_v1.json', 'continual_pilot_ready_v1.json', 'confirmation_ready_v1.json',
                     'seven_b_ready_v1.json', 'horizon_ready_v1.json']:
        if Path('records', artifact).exists():
            raise RuntimeError('Existing downstream result requires inspection: ' + artifact)
    if (SNAPSHOT / 'launch.json').exists():
        raise RuntimeError('Preserve the previous pipeline launch; never duplicate it')
    for job in build['jobs']:
        if Path('records/jobs', job['name'] + '.json').exists():
            raise RuntimeError('A continuation job already exists: ' + job['name'])
    old = json.loads(Path('records/pipeline_recompute_v7/build_manifest.json').read_text())
    for job in old['jobs']:
        state = json.loads(Path('records/jobs', job['name'] + '.json').read_text())
        if state['status'] == 'RUNNING':
            raise RuntimeError('An old controller is still marked running; inspect its identity')
    return build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    # A distinct local launch lock serializes controller creation. The original
    # GPU lock remains exclusively owned by device_guard and is never replaced.
    with (SNAPSHOT / 'launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        build = preflight()
        if args.validate_only:
            print(json.dumps({'status': 'READY_NOT_STARTED', 'controllers': len(build['jobs'])}))
            return
        environment = dict(os.environ, MUSA_LAUNCH_BLOCKING='1', PYTHONUNBUFFERED='1')
        environment['PYTHONPATH'] = ':'.join([str((SNAPSHOT / 'tools').resolve()),
                                             str(Path('tools').resolve()), str(Path.cwd())])
        launched = []
        report = {'status': 'STARTING', 'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'adopted_L_job': ADOPTED, 'controllers': launched,
                  'every_meta_leaf_requires_review': True}
        output = SNAPSHOT / 'launch.json'

        def save():
            temp = output.with_suffix('.tmp')
            temp.write_text(json.dumps(report, indent=2) + '\n')
            temp.replace(output)

        save()
        try:
            for job in build['jobs']:
                command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', job['name'], '--',
                           '/usr/bin/python', '-u', str(SNAPSHOT / 'tools' / (job['module'] + '.py'))]
                with Path('records/logs', job['name'] + '.log').open('xb', buffering=0) as log:
                    proc = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                record = Path('records/jobs', job['name'] + '.json')
                deadline = time.monotonic() + 20
                while not record.exists() or 'child_pid' not in json.loads(record.read_text()):
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Inspect failed wrapper startup: ' + job['name'])
                    time.sleep(.1)
                state = json.loads(record.read_text())
                fields = Path('/proc', str(state['child_pid']), 'stat').read_text().rsplit(')', 1)[1].split()
                if state['status'] != 'RUNNING' or fields[0] == 'Z' or fields[19] != state['child_proc_start_ticks']:
                    raise RuntimeError('Inspect failed controller startup: ' + job['name'])
                launched.append({'name': job['name'], 'wrapper_pid': proc.pid,
                                 'child_pid': state['child_pid'], 'child_proc_start_ticks': fields[19]})
                save()
            report['status'] = 'STARTED_AFTER_REVIEWED_L'
        except BaseException as error:
            report.update(status='PARTIAL_START_REQUIRES_INSPECTION', error=repr(error))
            raise
        finally:
            save()
        print(json.dumps(report))


if __name__ == '__main__':
    main()
