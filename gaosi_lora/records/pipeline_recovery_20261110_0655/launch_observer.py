"""Replace the absent local observer after preserving evidence and freeing space."""
import ast
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'records/pipeline_observer_20261110_0525'
NEW = ROOT / 'records/pipeline_observer_20261110_0655'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    assert not (NEW / 'launch.json').exists() and not (HERE / 'launch_intent.json').exists()
    state = read(OLD / 'observer_state.json')
    assert not (Path('/proc') / str(state['pid'])).exists()
    assert state == read(HERE / 'predecessor_observer_state.json')
    assert not (OLD / 'collector_submission_16.json').exists()
    for name in ['observer_state.json', 'observer_state.tmp', 'observer.stderr.txt',
                 'observer.stdout.txt', 'observations.jsonl']:
        assert (OLD / name).read_bytes() == (HERE / ('predecessor_' + name)).read_bytes()
    before = read(HERE / 'before.json')
    verification = read(HERE / 'deduplication_verification.json')
    dedupe = read(HERE / 'deduplication_receipt.json')
    preflight = read(HERE / 'remote_preflight.json')
    assert before['available_bytes'] == 0 and before['old_observer_proc_absent']
    assert verification['paths_rehashed'] == verification['paths_also_bound_to_existing_capture_manifest'] == 4268
    assert verification['evidence_paths_removed'] == 0 and not verification['evidence_bytes_modified']
    assert sha(HERE / 'deduplication.jsonl') == dedupe['journal_sha256'] == verification['journal_sha256']
    assert sha(HERE / 'deduplication_receipt.json') == verification['deduplication_receipt_sha256']
    assert dedupe['old_duplicate_allocated_bytes_reclaimed'] >= 2 * 1024**3
    assert len(preflight['active_processes']) == 4 and preflight['gpu_lock_inode'] == 31148027670
    assert not preflight['due_capture_directory_exists'] and not preflight['due_capture_archive_exists']
    stat = os.statvfs(ROOT)
    available = stat.f_bavail * stat.f_frsize
    assert available >= 512 * 1024**2
    raw_ledger = (OLD / 'observations.jsonl').read_bytes()
    rows = [json.loads(line) for line in raw_ledger.splitlines()]
    assert len(rows) == 16 and rows[-1]['observation'] == 'records/pipeline_monitor_20261110_062032_utc'
    assert raw_ledger.endswith(b'\n')
    with (NEW / 'observations.jsonl').open('xb') as handle:
        handle.write(raw_ledger)
    due = dt.datetime.fromisoformat(state['due_at_utc'])
    assert due.strftime('records/pipeline_monitor_%Y%m%d_%H%M%S_utc') == preflight['due_capture']
    with (HERE / 'deduplication.jsonl.gz').open('xb') as handle:
        handle.write(gzip.compress((HERE / 'deduplication.jsonl').read_bytes(), mtime=0))
    assert gzip.decompress((HERE / 'deduplication.jsonl.gz').read_bytes()) == (HERE / 'deduplication.jsonl').read_bytes()
    receipt = {
        'status': 'TERMINAL_LOCAL_OBSERVER_REPLACEMENT_PREPARED_AFTER_EVIDENCE_PRESERVING_DISK_RECOVERY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'old_observer_pid': state['pid'], 'old_observer_start_ticks': state['proc_start_ticks'],
        'old_observer_proc_authoritatively_absent': True,
        'local_disk_available_bytes_at_failure_inspection': before['available_bytes'],
        'local_disk_available_bytes_before_replacement': available,
        'failure_interpretation': 'Disk exhaustion confirmed; empty state temporary file and absent process are consistent with failure during the next local state write. No stderr exception bytes were captured.',
        'deduplicated_paths': verification['paths_rehashed'],
        'all_changed_inodes_rehashed_and_bound_to_prior_capture_manifests': True,
        'historical_evidence_paths_removed': 0, 'historical_evidence_file_bytes_modified': False,
        'deduplication_journal_sha256': sha(HERE / 'deduplication.jsonl'),
        'compressed_journal_retains_exact_original_bytes': True,
        'preflight_sha256': sha(HERE / 'remote_preflight.json'),
        'deduplication_verification_sha256': sha(HERE / 'deduplication_verification.json'),
        'predecessor_ledger_sha256': sha(OLD / 'observations.jsonl'),
        'new_ledger_seeded_with_existing_observations': 16,
        'due_capture_previously_not_submitted_or_present': True,
        'replacement_minimum_local_headroom_bytes': 128 * 1024**2,
        'fallback_error_receipt_directory_if_disk_write_fails': '/dev/shm',
        'training_or_original_controllers_modified_or_restarted': False,
        'method_validated': False, 'full_goal_complete': False}
    preserve(HERE / 'recovery_receipt.json', receipt)
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    sources = sorted(NEW.glob('*.py')) + [Path(__file__), HERE / 'remote_preflight.py',
              ROOT / 'records/pipeline_recovery_20261110_0525/archive_transport.py']
    for source in sources:
        ast.parse(source.read_text())
    assert sha(NEW / 'collect_snapshot.py') == '80329e6a2ec0cc86304c579a79e6a357d715dee542a9bf6e4c85fdd6eff05815'
    launch = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'first_due_at_utc': due.isoformat(), 'interval_seconds': 1800,
              'max_observations': 160, 'starting_observations': 16,
              'replaces_observer': str(OLD.relative_to(ROOT)),
              'replacement_reason': 'Authoritatively absent local observer and exhausted local disk; all evidence bytes preserved, duplicate snapshot storage reclaimed.',
              'recovery_receipt': str((HERE / 'recovery_receipt.json').relative_to(ROOT)),
              'recovery_receipt_sha256': sha(HERE / 'recovery_receipt.json'),
              'source_sha256': {str(path.relative_to(ROOT)): sha(path) for path in sources},
              'predecessor_ledger_sha256': sha(OLD / 'observations.jsonl'),
              'seeded_ledger_sha256': sha(NEW / 'observations.jsonl'),
              'collector_side_effect_submissions_retried': False,
              'training_started_or_approved_by_observer': False,
              'numerical_training_sources_modified': False}
    preserve(NEW / 'launch.json', launch)
    # The compressed journal has exactly the original journal bytes and avoids
    # sending a large uncompressed maintenance log over the fragile connection.
    recovery_files = [p for p in HERE.iterdir() if p.is_file() and p.name != 'deduplication.jsonl']
    for path in sources + recovery_files + [NEW / 'launch.json']:
        transport.sync(path)
    intent = {'status': 'SINGLE_LOCAL_OBSERVER_LAUNCH_INTENT',
              'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_sha256': sha(NEW / 'observe.py'), 'launch_sha256': sha(NEW / 'launch.json'),
              'predecessor_proc_absent': not (Path('/proc') / str(state['pid'])).exists()}
    assert intent['predecessor_proc_absent']
    preserve(HERE / 'launch_intent.json', intent)
    with (NEW / 'observer.stdout.txt').open('xb') as stdout, (NEW / 'observer.stderr.txt').open('xb') as stderr:
        process = subprocess.Popen([sys.executable, '-u', str(NEW / 'observe.py')], cwd=ROOT,
                                   stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
    fields = (Path('/proc') / str(process.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    preserve(HERE / 'child_identity.json', {'pid': process.pid, 'start_ticks': fields[19]})
    for _ in range(50):
        if (NEW / 'observer_state.json').exists():
            break
        assert process.poll() is None
        time.sleep(.2)
    observed = read(NEW / 'observer_state.json')
    assert process.poll() is None and observed['pid'] == process.pid
    assert observed['proc_start_ticks'] == fields[19]
    assert observed['status'] in ('WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', 'INSPECTING')
    assert observed['observations'] == 16
    start = {'status': 'REPLACEMENT_OBSERVER_VERIFIED_LIVE_AFTER_LOCAL_DISK_RECOVERY',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'pid': process.pid, 'start_ticks': fields[19], 'first_due_at_utc': due.isoformat(),
             'launch_sha256': sha(NEW / 'launch.json'), 'source_sha256': launch['source_sha256'],
             'recovery_receipt_sha256': sha(HERE / 'recovery_receipt.json'),
             'local_available_bytes_before_launch': available,
             'training_modified_or_restarted': False, 'full_goal_complete': False}
    preserve(NEW / 'start_receipt.json', start)
    for path in [HERE / 'launch_intent.json', HERE / 'child_identity.json', NEW / 'start_receipt.json']:
        transport.sync(path)
    print(json.dumps({'status': start['status'], 'pid': process.pid, 'start_ticks': fields[19],
                      'first_due_at_utc': due.isoformat(), 'start_receipt_sha256': sha(NEW / 'start_receipt.json')}))


if __name__ == '__main__':
    main()
