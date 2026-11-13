"""Explicit restart chain: validated boundary fix, saved recovery, original queue."""
import json
import os
from pathlib import Path
import subprocess


def main():
    check = json.loads(Path('checks/recovery_restart_boundary_cpu.json').read_text())
    assert check['status'] == 'PASS'
    assert check['tests']['resume_completes_pending_boundary_evaluation_before_next_update'] == 'PASS'
    name = 'recovery_dev_medium_lr0.0003_s17_resume_r1'
    with Path(f'records/logs/{name}.log').open('ab', buffering=0) as log:
        subprocess.run([
            '/usr/bin/python', '-u', 'tools/job.py', '--name', name, '--',
            '/usr/bin/python', '-u', '-m', 'gaosi.recover', '--config',
            'configs/recovery_dev_medium_lr0.0003_s17.json', '--resume',
        ], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=True)
    result = json.loads(Path('runs/recovery_dev_medium_lr0.0003_s17/result.json').read_text())
    assert result['status'] == 'PASS' and result['base_unchanged']
    os.execv('/usr/bin/python', [
        '/usr/bin/python', '-u', 'tools/calibration_queue.py', '--noise-plan',
        'records/noise_dose_extension_plan_v2.json', '--window-name',
        'noise_calibration_dev_extended_s17',
    ])


if __name__ == '__main__':
    main()
