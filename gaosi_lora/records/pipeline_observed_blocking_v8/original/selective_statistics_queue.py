"""Read-only selective summaries after complete production prerequisites."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.selective_statistics import POLICY
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


def main():
    if json.loads(Path('checks/selective_statistics_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Selective paired-evidence/statistics checks must pass first')
    preserve_json('records/selective_statistics_policy_v1.json', POLICY)
    wait_for('records/formal_recovery_ready_v2.json', 'formal_recovery_queue_v2')
    for stage, label in [('primary', 'primary'), ('full', 'three_seed')]:
        if stage == 'full':
            wait_for('records/confirmation_ready_v1.json', 'confirmation_queue_v1')
        run_job(f'selective_statistics_{label}_v1',
                ['/usr/bin/python', '-u', 'tools/summarize_selective.py', '--stage', stage,
                 '--out', f'analysis/selective_{label}_v1'], f'analysis/selective_{label}_v1/result.json')
    preserve_json('records/selective_statistics_ready_v1.json',
                  {'status': 'PAIRED_SELECTIVE_STATISTICS_COMPLETE', 'policy': POLICY,
                   'primary': 'analysis/selective_primary_v1', 'three_seed': 'analysis/selective_three_seed_v1',
                   'overall_experiment_complete': False})


if __name__ == '__main__':
    main()
