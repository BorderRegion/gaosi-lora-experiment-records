"""Wait for production continuation evidence; summarize without GPU use."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from gaosi.reflow_statistics import POLICY
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


def main():
    if json.loads(Path('checks/reflow_statistics_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Continuation evidence/statistics checks must pass')
    preserve_json('records/reflow_statistics_policy_v1.json', POLICY)
    wait_for('records/reflow_ready_v1.json', 'reflow_queue_observed_blocking_v8')
    for stage, label in [('primary', 'primary'), ('full', 'three_seed')]:
        if stage == 'full':
            wait_for('records/confirmation_ready_v1.json', 'confirmation_queue_observed_blocking_v8')
        out = f'analysis/reflow_{label}_v1'
        run_job(f'reflow_statistics_{label}_v1',
                ['/usr/bin/python', '-u', 'tools/summarize_reflow.py', '--stage', stage, '--out', out],
                f'{out}/result.json')
    preserve_json('records/reflow_statistics_ready_v1.json',
                  {'status': 'PAIRED_REFLOW_STATISTICS_COMPLETE', 'policy': POLICY,
                   'primary': 'analysis/reflow_primary_v1', 'three_seed': 'analysis/reflow_three_seed_v1',
                   'overall_experiment_complete': False})


if __name__ == '__main__':
    main()
