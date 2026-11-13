"""Compute paired recovery statistics after complete production prerequisites."""
import json
from pathlib import Path

from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


def main():
    if json.loads(Path('checks/recovery_statistics_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Paired cluster/statistics integration checks must pass first')
    from gaosi.recovery_statistics import POLICY
    preserve_json('records/recovery_statistics_policy_v1.json', POLICY)
    wait_for('records/formal_main_grid_ready_v2.json', 'formal_recovery_queue_recompute_v7')
    primary = run_job('recovery_statistics_primary_v1',
                      ['/usr/bin/python', '-u', 'tools/summarize_recovery.py', '--stage', 'primary',
                       '--out', 'analysis/recovery_primary_v1'], 'analysis/recovery_primary_v1/result.json')
    wait_for('records/confirmation_ready_v1.json', 'confirmation_queue_recompute_v7')
    full = run_job('recovery_statistics_three_seed_v1',
                   ['/usr/bin/python', '-u', 'tools/summarize_recovery.py', '--stage', 'full',
                    '--out', 'analysis/recovery_three_seed_v1'], 'analysis/recovery_three_seed_v1/result.json')
    preserve_json('records/recovery_statistics_ready_v1.json',
                  {'status': 'PAIRED_MAIN_RECOVERY_STATISTICS_COMPLETE', 'policy': POLICY,
                   'primary': 'analysis/recovery_primary_v1', 'three_seed': 'analysis/recovery_three_seed_v1',
                   'primary_run_count': primary['raw_run_count'], 'three_seed_run_count': full['raw_run_count'],
                   'overall_experiment_complete': False, 'remaining_report_blocks': full['remaining_report_blocks']})


if __name__ == '__main__':
    import sys
    sys.path.insert(0, str(Path.cwd()))
    main()
