"""Continue the declared ten replays only after independent H1/H2 tensor review.

This file does not launch itself. A launch must name the exact saved review SHA.
Recognized backend/memory limitations remain limitations, never full derivatives.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess


ROOT = Path.cwd()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review-sha256', required=True)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    review_path = directory / 'initial_saved_tensor_review.json'
    assert sha(review_path) == args.review_sha256
    review = read(review_path)
    assert review['status'] == 'UNIFORM_H1_H2_SAVED_ENDPOINT_GATE_INDEPENDENTLY_PASSED'
    assert review['remaining_ten_permitted_by_this_gate']
    assert not review['production_training_modified']
    for name, expected in review['evidence_sha256'].items():
        assert sha(ROOT / name) == expected, name
    plan_path = directory / 'plan.json'
    plan = read(plan_path)
    assert len(plan['configs']) == 12
    for name, expected in plan['source_sha256'].items():
        assert sha(ROOT / name) == expected, name
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    assert os.environ.get('MUSA_LAUNCH_BLOCKING') == '1'
    assert not (directory / 'remaining_queue_started.json').exists()
    # Refuse any partial or duplicate submission, including jobs not in this plan.
    for entry in plan['configs'][2:]:
        out = ROOT / entry['uniform']['out']
        assert not out.exists()
        assert not (ROOT / 'records/jobs' / (out.name + '.json')).exists()
        assert not (ROOT / 'records/logs' / (out.name + '.log')).exists()
    preserve(directory / 'remaining_queue_started.json', {
        'status': 'TEN_UNIFORM_REPLAYS_STARTED_AFTER_INDEPENDENT_SAVED_TENSOR_GATE',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'initial_review_sha256': sha(review_path), 'plan_sha256': sha(plan_path),
        'queue_source_sha256': sha(Path(__file__)), 'indices': list(range(2, 12)),
        'production_training_modified': False, 'full_goal_complete': False,
    })
    receipts = []
    for index in range(2, 12):
        entry = plan['configs'][index]
        config = entry['uniform']
        out = ROOT / config['out']
        name = out.name
        command = ['/usr/bin/python', '-u', 'tools/job.py', '--name', name,
                   '--musa-lock', '/tmp/gaosi_musa_device0.lock', '--',
                   '/usr/bin/env', 'MUSA_LAUNCH_BLOCKING=1', '/usr/bin/python', '-u',
                   str((directory / 'run.py').relative_to(ROOT)), '--index', str(index)]
        assert not (ROOT / 'records/jobs' / (name + '.json')).exists()
        with (ROOT / 'records/logs' / (name + '.log')).open('xb') as handle:
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT,
                                       env=os.environ.copy())
        result_path = out / 'result.json'
        result = read(result_path)
        job = read(ROOT / 'records/jobs' / (name + '.json'))
        assert completed.returncode == job['exit_code'] == 0
        assert job['status'] == result['status'] == 'PASS'
        assert result['config'] == config
        assert result['original_frozen_config'] == entry['original']
        assert result['diagnostic_variant'] == 'uniform_recomputed_support_forward_v1'
        status = result['diagnostic_status']
        assert status in ('COMPLETE', 'NOT_AVAILABLE_BACKEND', 'NOT_AVAILABLE_MEMORY'), status
        if config['tail'] == 1:
            assert status == 'COMPLETE', 'A missing H1 cannot provide a reference endpoint'
        if status == 'COMPLETE':
            assert result['base_unchanged'] and result['phi_unchanged']
            assert result['optimizer_updates'] == 0
            assert (out / 'endpoint.pt').is_file() and (out / 'gradient.pt').is_file()
            if config['tail'] != 1:
                assert result['endpoint_matches_H1'] is not None
        else:
            assert result['derivative_obtained'] is False
        receipt = {'index': index, 'job': name, 'diagnostic_status': status,
                   'result_sha256': sha(result_path), 'job_sha256': sha(ROOT / 'records/jobs' / (name + '.json')),
                   'saved_tensor_independently_reviewed': False}
        preserve(directory / ('remaining_index_' + str(index) + '.json'), receipt)
        receipts.append(receipt)
    preserve(directory / 'remaining_queue_completed.json', {
        'status': 'TEN_UNIFORM_REPLAYS_EXECUTED_PENDING_INDEPENDENT_REVIEW',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'initial_review_sha256': sha(review_path), 'jobs': receipts,
        'all_derivatives_obtained': all(r['diagnostic_status'] == 'COMPLETE' for r in receipts),
        'original_mixed_path_diagnostic_revalidated': False,
        'production_training_modified': False, 'method_validated': False,
        'full_goal_complete': False,
    })


if __name__ == '__main__':
    main()
