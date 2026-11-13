"""Replace only the confirmed terminal observer after recovering its saved capture."""
import ast
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'records/pipeline_observer_20261109_2225'
NEW = ROOT / 'records/pipeline_observer_20261110_0525'
RELATIVE = 'records/pipeline_monitor_20261110_052022_utc'


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
    proc = Path('/proc') / str(state['pid'])
    if proc.exists():
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        assert fields[0] in ('Z', 'X') or fields[19] != state['proc_start_ticks']
    for name in ('observer_state.json', 'observer.stderr.txt', 'observer.stdout.txt', 'observations.jsonl'):
        with (HERE / ('predecessor_' + name)).open('xb') as handle:
            handle.write((OLD / name).read_bytes())
    original_rows = [json.loads(line) for line in (OLD / 'observations.jsonl').read_text().splitlines()]
    assert len(original_rows) == 13 and original_rows[-1]['observation'] == 'records/pipeline_monitor_20261110_045018_utc'
    result = read(ROOT / RELATIVE / 'result.json')
    info = read(ROOT / (RELATIVE + '.remote_stdout.txt'))
    assert {k: v for k, v in result.items() if k != 'jobs'} == info['result']
    transfer = read(HERE / 'archive_transfer/receipt.json')
    assert transfer['archive_sha256'] == info['sha256'] == sha(ROOT / (RELATIVE + '.tar.gz'))
    manifest = read(ROOT / RELATIVE / 'archive_source_sha256.json')
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT / RELATIVE) and sha(path) == expected
    assert len(manifest) == transfer['verified_source_files']
    assert not result['unresolved_alerts'] and not result['pending_scientific_reviews']
    assert result['gpu_lock_inode'] == 31148027670
    assert not result['all_controllers_terminal']
    row = {'observation': RELATIVE, 'archive_sha256': info['sha256'],
           'verified_source_files': len(manifest), 'alerts': result['alerts'],
           'acknowledged_alerts': result['acknowledged_alerts'],
           'unresolved_alerts': result['unresolved_alerts'],
           'pending_scientific_reviews': result['pending_scientific_reviews'],
           'meta_runs': result['meta_runs'], 'production_modified_or_restarted': False}
    with (NEW / 'observations.jsonl').open('x') as handle:
        for item in original_rows + [row]:
            handle.write(json.dumps(item) + '\n')
    preserve(HERE / 'recovery_receipt.json', {
        'status': 'TERMINAL_OBSERVER_TRANSPORT_FAILURE_CAPTURE_RECOVERED_WITHOUT_RECOLLECTION',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'previous_goal_turn': 'progress_scheduled_capture_rehashed_and_verified_wait',
        'current_goal_turn': 'progress_resume_immutable_capture_and_replace_terminal_observer',
        'old_observer_pid': state['pid'], 'old_observer_start_ticks': state['proc_start_ticks'],
        'old_observer_authoritatively_terminal': True, 'observation': RELATIVE,
        'archive_sha256': info['sha256'], 'source_files_rehashed': len(manifest),
        'original_partial_archive_preserved': str((HERE / 'archive_transfer/partial_before_resume.tar.gz').relative_to(ROOT)),
        'collector_resubmitted': False, 'predecessor_observations_modified': False,
        'new_ledger_contains_13_existing_and_one_recovered_capture': True,
        'preflight': str((HERE / 'preflight.json').relative_to(ROOT)),
        'preflight_sha256': sha(HERE / 'preflight.json'),
        'transfer_receipt_sha256': sha(HERE / 'archive_transfer/receipt.json'),
        'training_or_original_controllers_modified_or_restarted': False,
        'method_validated': False, 'full_goal_complete': False})
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    sources = sorted(NEW.glob('*.py')) + [HERE / 'archive_transport.py', Path(__file__)]
    for path in sources:
        ast.parse(path.read_text())
    assert sha(NEW / 'collect_snapshot.py') == '80329e6a2ec0cc86304c579a79e6a357d715dee542a9bf6e4c85fdd6eff05815'
    due = dt.datetime.fromisoformat(result['observed_at_utc']) + dt.timedelta(minutes=30)
    launch = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'first_due_at_utc': due.isoformat(), 'interval_seconds': 1800, 'max_observations': 160,
              'starting_observations': 14, 'replaces_observer': str(OLD.relative_to(ROOT)),
              'replacement_reason': 'Confirmed terminal observer after interrupted immutable archive transfer; recover the original capture and use verified chunk downloads with read-only retries.',
              'recovery_receipt': str((HERE / 'recovery_receipt.json').relative_to(ROOT)),
              'recovery_receipt_sha256': sha(HERE / 'recovery_receipt.json'),
              'source_sha256': {str(path.relative_to(ROOT)): sha(path) for path in sources},
              'predecessor_ledger_sha256': sha(OLD / 'observations.jsonl'),
              'seeded_ledger_sha256': sha(NEW / 'observations.jsonl'),
              'collector_side_effect_submissions_retried': False,
              'training_started_or_approved_by_observer': False, 'numerical_training_sources_modified': False}
    preserve(NEW / 'launch.json', launch)
    files = sources + [NEW / 'launch.json', HERE / 'recovery_receipt.json', HERE / 'preflight.json',
                      HERE / 'archive_transfer/manifest.json', HERE / 'archive_transfer/receipt.json']
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
    assert state['status'] == 'WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET'
    fields = (Path('/proc') / str(process.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == state['proc_start_ticks']
    assert state['observations'] == 14 and state['due_at_utc'] == due.isoformat()
    receipt = {'status': 'REPLACEMENT_OBSERVER_VERIFIED_LIVE_AFTER_ARCHIVE_RECOVERY',
               'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'pid': process.pid,
               'start_ticks': fields[19], 'next_due_at_utc': due.isoformat(),
               'launch_sha256': sha(NEW / 'launch.json'), 'source_sha256': launch['source_sha256'],
               'recovery_receipt_sha256': launch['recovery_receipt_sha256'],
               'training_modified_or_restarted': False, 'full_goal_complete': False}
    preserve(NEW / 'start_receipt.json', receipt);module.sync(NEW / 'start_receipt.json')
    print(json.dumps({'status': receipt['status'], 'pid': process.pid, 'start_ticks': fields[19],
                      'next_due_at_utc': due.isoformat(), 'receipt_sha256': sha(NEW / 'start_receipt.json')}))


if __name__ == '__main__':
    main()
