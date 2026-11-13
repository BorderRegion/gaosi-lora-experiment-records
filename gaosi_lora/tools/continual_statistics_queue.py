"""Read-only eight-round/probe summaries, waiting for full runtime evidence."""
import json
from pathlib import Path

from summarize_continual import POLICY
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


def main():
    for file in ['checks/continual_statistics_cpu.json', 'checks/continual_cost_metadata_cpu.json']:
        if json.loads(Path(file).read_text())['status'] != 'PASS':
            raise RuntimeError('Continual summary and actual stage-cost checks must pass')
    preserve_json('records/continual_statistics_policy_v1.json', POLICY)
    wait_for('records/continual_pilot_ready_v1.json', 'continual_queue_v1')
    for stage, label in [('primary', 'primary'), ('full', 'three_seed')]:
        if stage == 'full':
            wait_for('records/confirmation_ready_v1.json', 'confirmation_queue_v1')
        out = f'analysis/continual_{label}_v1'
        run_job(f'continual_statistics_{label}_v1',
                ['/usr/bin/python', '-u', 'tools/summarize_continual.py', '--stage', stage, '--out', out],
                f'{out}/result.json')
    preserve_json('records/continual_statistics_ready_v1.json',
                  {'status': 'CONTINUAL_AND_COMMON_RANK16_STATISTICS_COMPLETE', 'policy': POLICY,
                   'primary': 'analysis/continual_primary_v1', 'three_seed': 'analysis/continual_three_seed_v1',
                   'overall_experiment_complete': False})


if __name__ == '__main__':
    main()
