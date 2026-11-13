"""Replace the confirmed terminal read-only observer after immutable recovery."""
import ast
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'records/pipeline_observer_20261110_1315'
NEW = ROOT / 'records/pipeline_observer_20261110_1755'
RELATIVE = 'records/pipeline_monitor_20261110_174541_utc'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    assert not (NEW / 'launch.json').exists() and not (NEW / 'observer_state.json').exists()
    state = read(OLD / 'observer_state.json')
    assert state['status'] == 'OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART'
    assert state['pid'] == 2464224 and state['proc_start_ticks'] == '156222690'
    proc = Path('/proc') / str(state['pid'])
    if proc.exists():
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        assert fields[0] in ('Z', 'X') or fields[19] != state['proc_start_ticks']
    for name in ('observer_state.json', 'observer.stderr.txt', 'observer.stdout.txt', 'observations.jsonl'):
        assert (HERE / ('predecessor_' + name)).read_bytes() == (OLD / name).read_bytes()
    old_ledger = (OLD / 'observations.jsonl').read_bytes()
    rows = [json.loads(line) for line in old_ledger.splitlines()]
    assert len(rows) == 37 and rows[-1]['observation'] == 'records/pipeline_monitor_20261110_171536_utc'
    result = read(ROOT / RELATIVE / 'result.json')
    capture = read(HERE / 'capture_recovery.json')
    preflight = read(HERE / 'preflight.json')
    transfer = read(HERE / 'archive_transfer/receipt.json')
    assert capture['status'] == 'EXISTING_CAPTURE_RECOVERED_WITHOUT_RECOLLECTION'
    assert sha(HERE / 'preflight.json') == capture['preflight_sha256']
    assert sha(HERE / 'archive_transfer/receipt.json') == capture['transfer_receipt_sha256']
    assert sha(ROOT / RELATIVE / 'result.json') == preflight['result_sha256']
    assert transfer['archive_sha256'] == capture['archive_sha256'] == sha(ROOT / (RELATIVE + '.tar.gz'))
    manifest = read(ROOT / RELATIVE / 'archive_source_sha256.json')
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT / RELATIVE) and sha(path) == expected
    assert len(manifest) == transfer['verified_source_files'] == capture['source_files_rehashed']
    assert not result['unresolved_alerts'] and not result['pending_scientific_reviews']
    assert result['gpu_lock_inode'] == 31148027670 and not result['all_controllers_terminal']
    due = dt.datetime.fromisoformat(result['observed_at_utc']) + dt.timedelta(seconds=1800)
    overdue_seconds = max(0.0, (dt.datetime.now(dt.timezone.utc) - due).total_seconds())
    row = {'observation': RELATIVE, 'archive_sha256': capture['archive_sha256'],
           'verified_source_files': len(manifest), 'alerts': result['alerts'],
           'acknowledged_alerts': result['acknowledged_alerts'],
           'unresolved_alerts': result['unresolved_alerts'],
           'pending_scientific_reviews': result['pending_scientific_reviews'],
           'meta_runs': result['meta_runs'], 'production_modified_or_restarted': False}
    assert old_ledger.endswith(b'\n')
    with (NEW / 'observations.jsonl').open('xb') as handle:
        handle.write(old_ledger + json.dumps(row).encode() + b'\n')
    preserve(HERE / 'recovery_receipt.json', {
        'status': 'TERMINAL_OBSERVER_TRANSPORT_FAILURE_CAPTURE_RECOVERED_WITHOUT_RECOLLECTION',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'old_observer_pid': state['pid'], 'old_observer_start_ticks': state['proc_start_ticks'],
        'old_observer_authoritatively_terminal': True, 'observation': RELATIVE,
        'archive_sha256': capture['archive_sha256'], 'source_files_rehashed': len(manifest),
        'original_partial_archive_preserved': str((HERE / 'archive_transfer/partial_before_resume.tar.gz').relative_to(ROOT)),
        'collector_resubmitted': False, 'predecessor_observations_modified': False,
        'seeded_ledger_preserves_previous_bytes_as_prefix': (NEW / 'observations.jsonl').read_bytes().startswith(old_ledger),
        'previous_observations': len(rows), 'recovered_observations': 1,
        'preflight': str((HERE / 'preflight.json').relative_to(ROOT)),
        'preflight_sha256': sha(HERE / 'preflight.json'),
        'transfer_receipt_sha256': sha(HERE / 'archive_transfer/receipt.json'),
        'capture_recovery_sha256': sha(HERE / 'capture_recovery.json'),
        'training_or_original_controllers_modified_or_restarted': False,
        'method_validated': False, 'full_goal_complete': False})
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shared = ROOT / 'records/pipeline_recovery_20261110_0525/archive_transport.py'
    sources = sorted(NEW.glob('*.py')) + [shared, HERE / 'resume_archive_v3.py', Path(__file__)]
    for path in sources:
        ast.parse(path.read_text())
    assert sha(NEW / 'collect_snapshot.py') == '80329e6a2ec0cc86304c579a79e6a357d715dee542a9bf6e4c85fdd6eff05815'
    launch = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'first_due_at_utc': due.isoformat(), 'interval_seconds': 1800, 'max_observations': 160,
              'scheduling_overdue_seconds_at_preparation': overdue_seconds,
              'overdue_policy': 'Collect the original overdue boundary once immediately; subsequent due uses actual capture time plus 1800 seconds. Do not replay missed intervals.',
              'starting_observations': len(rows) + 1, 'replaces_observer': str(OLD.relative_to(ROOT)),
              'replacement_reason': 'Confirmed terminal observer after interrupted immutable archive download; recovered existing capture without recollection. Training and original controllers unchanged.',
              'recovery_receipt': str((HERE / 'recovery_receipt.json').relative_to(ROOT)),
              'recovery_receipt_sha256': sha(HERE / 'recovery_receipt.json'),
              'source_sha256': {str(path.relative_to(ROOT)): sha(path) for path in sources},
              'predecessor_state_sha256': sha(OLD / 'observer_state.json'),
              'predecessor_ledger_sha256': sha(OLD / 'observations.jsonl'),
              'seeded_ledger_sha256': sha(NEW / 'observations.jsonl'),
              'collector_side_effect_submissions_retried': False,
              'training_started_or_approved_by_observer': False, 'numerical_training_sources_modified': False}
    preserve(NEW / 'launch.json', launch)
    files = sources + [NEW / 'launch.json', HERE / 'recovery_receipt.json', HERE / 'preflight.json',
                      HERE / 'capture_recovery.json', HERE / 'archive_transfer/manifest.json',
                      HERE / 'archive_transfer/receipt.json', HERE / 'attempt1_failure.json',
                      HERE / 'attempt2_transport_failure.json', HERE / 'resume_archive_attempt1.py',
                      HERE / 'resume_archive.py', HERE / 'launch_observer.py',
                      HERE / 'outage_local_note.json', HERE / 'partial_capture_result.json',
                      HERE / 'partial_capture_review.json', HERE / 'partial_capture_local_note.json']
    files += [HERE / ('predecessor_' + name) for name in
              ('observer_state.json', 'observer.stderr.txt', 'observer.stdout.txt', 'observations.jsonl')]
    for path in files:
        module.sync(path)
    with (NEW / 'observer.stdout.txt').open('xb') as stdout, (NEW / 'observer.stderr.txt').open('xb') as stderr:
        process = subprocess.Popen([sys.executable, '-u', str(NEW / 'observe.py')], cwd=ROOT,
                                   stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        if (NEW / 'observer_state.json').exists():
            break
        assert process.poll() is None
        time.sleep(0.2)
    state = read(NEW / 'observer_state.json')
    assert process.poll() is None and state['pid'] == process.pid
    assert state['status'] in ('WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', 'INSPECTING')
    fields = (Path('/proc') / str(process.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == state['proc_start_ticks']
    assert state['observations'] == len(rows) + 1
    if state['status'] == 'WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET':
        assert state['due_at_utc'] == due.isoformat()
    else:
        assert state['observation'] == 'records/pipeline_monitor_' + due.strftime('%Y%m%d_%H%M%S_utc')
    receipt = {'status': 'REPLACEMENT_OBSERVER_VERIFIED_LIVE_AFTER_ARCHIVE_RECOVERY',
               'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'pid': process.pid,
               'start_ticks': fields[19], 'next_due_at_utc': due.isoformat(),
               'initial_observer_state': state['status'],
               'original_boundary_preserved_if_overdue': True,
               'launch_sha256': sha(NEW / 'launch.json'), 'source_sha256': launch['source_sha256'],
               'recovery_receipt_sha256': launch['recovery_receipt_sha256'],
               'training_modified_or_restarted': False, 'full_goal_complete': False}
    preserve(NEW / 'start_receipt.json', receipt)
    module.sync(NEW / 'start_receipt.json')
    print(json.dumps({'status': receipt['status'], 'pid': process.pid, 'start_ticks': fields[19],
                      'next_due_at_utc': due.isoformat(), 'receipt_sha256': sha(NEW / 'start_receipt.json')}), flush=True)


if __name__ == '__main__':
    main()
