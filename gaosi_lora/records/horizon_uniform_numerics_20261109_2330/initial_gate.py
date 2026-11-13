"""Run only uniform-path H1/H2, then require agent endpoint/tensor review."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'tools'))


def main():
    directory = Path(__file__).resolve().parent
    plan = json.loads((directory / 'plan.json').read_text())
    probe = json.loads((directory / 'probe_tensor_audit.json').read_text())
    assert probe['status'] == 'SAVED_PROBE_WORST_GRADIENT_PAIRS_INDEPENDENTLY_AUDITED'
    validation = json.loads((directory / 'uniform_direct_chain_cpu.json').read_text())
    assert validation['status'] == 'PASS' and len(validation['tests']) == 22
    assert hashlib.sha256((directory / 'run.py').read_bytes()).hexdigest() == plan['source_sha256'][str((directory / 'run.py').relative_to(ROOT))]
    receipts = []
    for index in (0, 1):
        entry = plan['configs'][index]
        name = Path(entry['uniform']['out']).name
        command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', name,
                   '--musa-lock', '/tmp/gaosi_musa_device0.lock', '--',
                   '/usr/bin/env', 'MUSA_LAUNCH_BLOCKING=1', '/usr/bin/python', '-u',
                   str((directory / 'run.py').relative_to(ROOT)), '--index', str(index)]
        assert not (ROOT / 'records/jobs' / (name + '.json')).exists()
        log = ROOT / 'records/logs' / (name + '.log')
        with log.open('xb') as handle:
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, env=os.environ.copy())
        result = json.loads(Path(entry['uniform']['out'], 'result.json').read_text())
        assert completed.returncode == 0 and result['status'] == 'PASS'
        assert result['diagnostic_status'] == 'COMPLETE', 'A limitation is not endpoint equality'
        if index == 1:
            assert result['endpoint_matches_H1'] is not None
        receipts.append({'index': index, 'job': name, 'result_sha256': hashlib.sha256(Path(entry['uniform']['out'], 'result.json').read_bytes()).hexdigest()})
    output = directory / 'initial_gate_completed.json'
    with output.open('x') as handle:
        json.dump({'status': 'H1_H2_EXECUTED_PENDING_INDEPENDENT_SAVED_TENSOR_REVIEW',
                   'jobs': receipts, 'remaining_ten_launched': False,
                   'method_validated': False, 'full_goal_complete': False}, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'status': 'H1_H2_EXECUTED_PENDING_INDEPENDENT_SAVED_TENSOR_REVIEW',
                      'remaining_ten_launched': False}), flush=True)


if __name__ == '__main__':
    main()
