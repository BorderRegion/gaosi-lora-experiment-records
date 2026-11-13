"""Estimate remaining declared cells from preserved process time, excluding lock wait."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics
import tarfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out = ROOT / 'records/development_eta_20261106_1430.json'
    assert not out.exists()
    evidence = [Path(__file__)]
    observed = []
    for path in sorted(HERE.glob('phi_dev_*.json')):
        match = re.fullmatch(r'phi_dev_([LX])_(2048|2560)_t([0-4])_(clean|medium)_s17', path.stem)
        if not match:
            continue
        audit = read(path)
        assert audit['job_status'] == 'PASS' and audit['completed_recovery_steps'] == 64
        receiptpath = HERE / ('fetch_receipt_' + path.name)
        receipt = read(receiptpath)
        assert receipt['audit_sha256'] == sha(path)
        saved = HERE / (path.stem + '_evidence')
        manifestpath = saved / 'manifest.json'
        manifest = read(manifestpath)
        archivepath = HERE / (path.stem + '_evidence.tar.gz')
        assert sha(archivepath) == receipt['archive']['sha256']
        with tarfile.open(archivepath, 'r:gz') as archive:
            assert archive.extractfile(str(manifestpath.relative_to(ROOT))).read() == manifestpath.read_bytes()
        if 'manifest_sha256' in receipt['archive']:
            assert sha(manifestpath) == receipt['archive']['manifest_sha256']
        eventpath = saved / 'snapshot/runs' / path.stem / 'events.jsonl'
        assert sha(eventpath) == manifest['files'][str(eventpath.relative_to(ROOT))]
        events = [json.loads(line) for line in eventpath.read_text().splitlines()]
        assert events[-1]['event'] == 'evaluation' and events[-1]['step'] == 64
        duration = events[-1]['process_wall_seconds']
        assert 0 < duration <= audit['job_wall_seconds']
        observed.append({'run': path.stem, 'task': int(match[3]), 'architecture': match[1],
                         'last_event_process_seconds': duration,
                         'wrapper_seconds_including_possible_lock_wait': audit['job_wall_seconds']})
        evidence.extend([path, receiptpath, manifestpath, eventpath])
    assert len(observed) == 22
    assert {a: sum(r['architecture'] == a for r in observed) for a in ['L', 'X']} == {'L': 20, 'X': 2}
    h1path = ROOT / 'records/horizon_effect_monitor_20261106_142546_utc/result.json'
    h1 = read(h1path)
    assert h1['pass_jobs'] == 1 and h1['running_jobs'] == 1 and not h1['alerts']
    finished_h1 = [row for row in h1['jobs'] if row['job']['status'] == 'PASS']
    assert len(finished_h1) == 1 and finished_h1[0]['name'] == 'phi_horizon_L_H1_2560_t0_clean_s17_v1'
    h1review = ROOT / 'records/horizon_noise_timing_repair_20261106_1230/first_h1_endpoint_assessment.json'
    evidence.extend([h1path, h1review])
    costs = {}
    for task in range(5):
        rows = [r for r in observed if r['task'] == task]
        mean = statistics.mean(row['last_event_process_seconds'] for row in rows)
        nbase = 12 - len(rows)
        nh1 = 1 if task == 0 else 2
        costs[str(task)] = {'completed_base_cells': len(rows), 'remaining_base_cells': nbase,
                            'remaining_H1_cells': nh1, 'mean_saved_process_seconds': mean,
                            'remaining_base_seconds_proxy': nbase * mean,
                            'remaining_H1_seconds_proxy': nh1 * mean}
    base = sum(r['remaining_base_seconds_proxy'] for r in costs.values()) / 86400
    extra = sum(r['remaining_H1_seconds_proxy'] for r in costs.values()) / 86400
    previous = ROOT / 'records/development_eta_20261106_1005.json'
    evidence.append(previous)
    report = {
        'status': 'CONDITIONAL_REMAINING_DECLARED_GRID_PROCESS_TIME_PROXY_EXCLUDING_LOCK_WAIT',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'completed_base_cells': 22, 'declared_base_cells': 60, 'remaining_base_cells': 38,
        'completed_H1_cells': 1, 'remaining_H1_cells_including_running': 9,
        'observed_architecture_cell_counts': {'L': 20, 'X': 2, 'standard_adam': 0, 'paired_adam': 0},
        'observed_base_process_costs': observed, 'task_cost_estimates': costs,
        'first_H1_last_event_process_seconds': read(h1review)['H1_last_event_process_seconds'],
        'base_remaining_serial_days_proxy': base,
        'additional_H1_remaining_serial_days_proxy': extra,
        'combined_shared_device_remaining_days_proxy': base + extra,
        'communicated_base_only_rough_days': [3, 4],
        'communicated_base_and_additional_H1_rough_days': [4, 5],
        'assumptions': ['Use the same-task mean saved process time for unmeasured X, Adam and H1 cells.',
                        'All incomplete cells, including currently active and queued cells, count at full estimated cost.',
                        'The existing two queues share one device; no interruption, retries or new competing work.'],
        'limitations': ['Last-event process time excludes lock wait and a short process shutdown tail; it is a cost proxy, not total billed device time.',
                        'Two X cells are both the known task; no Adam endpoint and only one H1 endpoint are measured.',
                        'No guarantee, upper bound or exact completion date; conditional extensions and full-protocol later stages are excluded.'],
        'full_protocol_eta': None,
        'full_protocol_remaining_scope': read(previous)['full_protocol_remaining_scope'],
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in evidence},
        'method_validated': False, 'full_goal_complete': False, 'training_modified_or_restarted': False}
    with out.open('x') as handle:
        handle.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for path in [Path(__file__), out]:
        print(json.dumps(sync.sync_preserved(path)))
    print(json.dumps({'base_days_proxy': base, 'H1_days_proxy': extra, 'combined_days_proxy': base + extra}))


if __name__ == '__main__':
    main()
