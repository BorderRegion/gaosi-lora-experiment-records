"""Run saved-artifact CPU audits only, after the O1 specialization has completed."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path(__file__).resolve().parent
RUN = 'runs/phi_mix_O1_X_specialize512_s17_observed_blocking_v8'
PINS = {
    'tools/audit_observed_meta.py': '6583634debbdf7c77e1078d987e3ec37bf9c1f4e14cf07babc6fe59e9e8ca99b',
    'records/pipeline_observer_20261101_1143/replay_saved_adam.py': '9eedbe61dc0f18e26462ed5ebe658cc491600079afd1a64e6a35b486d8df95a2',
    'records/organization_completion_preparation_20261110_0603/inspect_completed_specialization.py': '120223d80e431505656cc51bb759ce77b63422c88e6f3ef13ce29db50eb4732e',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    with (HERE / name).open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    os.chdir(ROOT)
    for name, expected in PINS.items():
        assert sha(ROOT / name) == expected, name
    job = json.loads((ROOT / 'records/jobs' / (Path(RUN).name + '.json')).read_text())
    result = json.loads((ROOT / RUN / 'result.json').read_text())
    assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
    assert result['episodes'] == 64 and result['inner_updates'] == 512
    assert sha(ROOT / RUN / 'latest.pt') == '96d44f33169c6e28fc8dafcf906df175a287ee12da309a73edfaee3979b5ed88'
    prefix = str(HERE.relative_to(ROOT))
    audit = prefix + '/artifact_audit.json'
    replay = prefix + '/adam_replay.json'
    tensor = prefix + '/tensor_review.json'
    commands = [
        [sys.executable, '-u', 'tools/audit_observed_meta.py', '--run', RUN, '--out', audit],
        [sys.executable, '-u', 'records/pipeline_observer_20261101_1143/replay_saved_adam.py', '--audit', audit, '--out', replay],
        [sys.executable, '-u', 'records/organization_completion_preparation_20261110_0603/inspect_completed_specialization.py', '--audit', audit, '--replay', replay, '--out', tensor],
    ]
    write('execution_started.json', {
        'pid': os.getpid(),
        'proc_start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'commands': commands, 'source_sha256': PINS,
        'driver_sha256': sha(Path(__file__)), 'run': RUN,
        'training_modified_or_restarted': False,
    })
    for index, command in enumerate(commands):
        with (HERE / f'command_{index}.log').open('xb', buffering=0) as log:
            code = subprocess.call(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        write(f'command_{index}_result.json', {'returncode': code, 'command': command,
                                              'log_sha256': sha(HERE / f'command_{index}.log')})
        if code:
            raise RuntimeError('Saved-evidence review failed at stage ' + str(index))
    values = {name: json.loads((ROOT / name).read_text()) for name in (audit, replay, tensor)}
    assert values[replay]['all_finite'] and values[replay]['allclose']
    assert values[tensor]['episodes_checked'] == 64 and values[tensor]['inner_steps_checked'] == 512
    write('execution_completed.json', {
        'status': 'CPU_ARTIFACT_REPLAY_AND_ORGANIZATION_TENSOR_CHECKS_COMPLETED_REQUIRES_SCIENTIFIC_REVIEW',
        'completed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'run': RUN,
        'outputs_sha256': {name: sha(ROOT / name) for name in values},
        'training_modified_or_restarted': False, 'scientific_promotion_generated': False,
        'method_validated': False, 'full_goal_complete': False,
    })


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        write('execution_failed.json', {'error': repr(error),
                                      'failed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                                      'training_modified_or_restarted': False})
        raise
