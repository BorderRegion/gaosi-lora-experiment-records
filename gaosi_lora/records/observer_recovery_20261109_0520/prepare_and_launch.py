"""Replace an exited evidence observer after recovered SSH; never touch training."""
import ast
import base64
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
OLD = ROOT / 'records/pipeline_observer_20261108_0505'
NEW = ROOT / 'records/pipeline_observer_20261109_0520'
FOLLOWER = ROOT / 'records/scheduled_review_follower_20261109_0410'
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def dead(pid, ticks):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] in ('Z', 'X') or fields[19] != ticks
    except FileNotFoundError:
        return True


def remote(code, data):
    reply = subprocess.run(SSH + ['/usr/bin/python -c ' + shlex.quote(code)],
                           input=json.dumps(data), capture_output=True, text=True,
                           check=True, timeout=60)
    return json.loads(reply.stdout)


def sync(paths, create=False):
    data = {str(p.relative_to(ROOT)): {'sha256': sha(p),
            'data': base64.b64encode(p.read_bytes()).decode()} for p in paths}
    return remote('''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');d=json.load(sys.stdin)
if d['create']:
 for name in d['directories']:(r/name).mkdir(exist_ok=False)
for name,item in d['files'].items():
 p=r/name;b=base64.b64decode(item['data']);assert hashlib.sha256(b).hexdigest()==item['sha256']
 with p.open('xb') as h:h.write(b)
 assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256']
print(json.dumps({'status':'EXCLUSIVE_SYNC_INDEPENDENTLY_REHASHED','files_sha256':{n:i['sha256'] for n,i in d['files'].items()}}))
''', {'files': data, 'create': create,
      'directories': [str(NEW.relative_to(ROOT)), str(RECORD.relative_to(ROOT))]})


def main():
    assert not NEW.exists() and not (RECORD / 'incident_review.json').exists()
    failed = ROOT / 'records/pipeline_observer_20261109_0510'
    failedstate = read(failed / 'observer_state.json')
    assert failedstate['status'] == 'OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART'
    assert dead(failedstate['pid'], failedstate['proc_start_ticks'])
    assert not (failed / 'observations.jsonl').read_text()
    state = read(OLD / 'observer_state.json')
    assert state['status'] == 'OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART'
    assert state['pid'] == 3541706 and state['proc_start_ticks'] == '135999189'
    assert dead(state['pid'], state['proc_start_ticks'])
    follower = read(FOLLOWER / 'state.json')
    assert follower['status'] == 'OBSERVER_IDENTITY_OR_TERMINATION_REQUIRES_AGENT'
    assert follower['observation_count'] == 48
    assert dead(follower['identity']['pid'], follower['identity']['start_ticks'])
    rows = [json.loads(line) for line in (OLD / 'observations.jsonl').read_text().splitlines()]
    assert len(rows) == 48
    for name, expected in read(OLD / 'launch.json')['source_sha256'].items():
        assert sha(ROOT / name) == expected
    previous = OLD / 'followup_20261109_0435.json'
    receipt = OLD / 'completion_receipt_20261109_0435.json'
    for name, expected in read(receipt)['evidence_sha256'].items():
        assert sha(ROOT / name) == expected
    last = ROOT / rows[-1]['observation']
    assert sha(Path(str(last) + '.tar.gz')) == rows[-1]['archive_sha256']
    manifest = read(last / 'archive_source_sha256.json')
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(last) and sha(path) == expected
    assert not rows[-1]['alerts'] and not read(last / 'result.json')['alerts']
    check = remote('''import datetime,json
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');missing='records/pipeline_monitor_20261109_050539_utc'
assert not (r/missing).exists() and not (r/(missing+'.tar.gz')).exists()
assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino==31148027670
p=r/'runs/phi_dev_paired_adam_t0_clean_s17/events.jsonl';event=json.loads(p.read_text().splitlines()[-1])
print(json.dumps({'status':'SSH_RESTORED_NO_FAILED_SLOT_OUTPUT_OR_DEVICE_LOCK_REPLACEMENT','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'device_lock_inode':31148027670,'last_training_event':event}))
''', {})
    lock = ROOT / 'records/supervise_recompute_v7.lock'
    inode = lock.stat().st_ino
    with lock.open('r+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    NEW.mkdir(exist_ok=False)
    copied = {}
    for name in ['observe.py', 'collect_snapshot.py', 'wait_next_observation.py',
                 'review_snapshot.py', 'record_runtime_review.py', 'complete_scheduled_followup_v1.py']:
        ast.parse((OLD / name).read_text())
        with (NEW / name).open('xb') as handle:
            handle.write((OLD / name).read_bytes())
        copied[name] = {'source': str((OLD / name).relative_to(ROOT)), 'sha256': sha(NEW / name)}
        assert sha(OLD / name) == copied[name]['sha256']
    bridge = NEW / 'previous_observer_followup_20261109_0435.json'
    with bridge.open('xb') as handle:
        handle.write(previous.read_bytes())
    assert sha(bridge) == sha(previous)
    evidence = [OLD / 'observer_state.json', OLD / 'observations.jsonl', OLD / 'stderr.log',
                FOLLOWER / 'state.json', FOLLOWER / 'completed.jsonl', receipt, previous,
                failed / 'observer_state.json', failed / 'stderr.log',
                ROOT / 'records/observer_recovery_20261109_0510/connection_recurrence.json',
                ROOT / 'records/observer_recovery_20261109_0510/ssh_banner_probe.jsonl',
                ROOT / 'records/observer_recovery_20261109_0510/ssh_master_retry.jsonl',
                ROOT / 'records/observer_recovery_20261109_0510/ssh_restored_preflight.json']
    write(RECORD / 'incident_review.json', {
        'status': 'EXITED_OBSERVER_SSH_FAILURE_REVIEWED_CONNECTION_RESTORED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'old_observer_identity_verified_dead': True,
        'old_follower_identity_verified_dead': True,
        'old_observation_count': len(rows), 'last_committed_observation': rows[-1],
        'last_snapshot_files_independently_rehashed': len(manifest),
        'last_completion_receipt_evidence_independently_rehashed': len(read(receipt)['evidence_sha256']),
        'failure': 'SSH exited 255 before the scheduled 05:05 UTC snapshot was created; original control socket was absent.',
        'recovery': 'Initial restored master and zero-observation successor exited; subsequent SSH attempts returned preauthentication Exceeded MaxStartups. Low-frequency retry restored a detached foreground SSH master with keepalives; all 15 original controllers and active training identity verified. Initial master exit cause remains unproven.',
        'failed_successor': str(failed.relative_to(ROOT)),
        'failed_successor_identity_verified_dead': True,
        'failed_successor_observations': 0,
        'remote_preflight': check, 'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in evidence},
        'copied_sources_unchanged': copied,
        'bridge_followup': str(bridge.relative_to(ROOT)), 'bridge_sha256': sha(bridge),
        'bridge_original': str(previous.relative_to(ROOT)),
        'training_modified_or_restarted': False, 'old_observer_modified_or_restarted': False,
        'method_validated': False, 'full_goal_complete': False,
    })
    write(NEW / 'preparation.json', {
        'status': 'FRESH_SUCCESSOR_AFTER_REVIEWED_SSH_EXIT', 'copied_sources': copied,
        'incident_review': str((RECORD / 'incident_review.json').relative_to(ROOT)),
        'incident_review_sha256': sha(RECORD / 'incident_review.json'),
        'training_modified_or_restarted': False, 'full_goal_complete': False,
    })
    deployment = sync([*sorted(NEW.glob('*.py')), bridge, NEW / 'preparation.json',
                       RECORD / 'incident_review.json', Path(__file__)], create=True)
    write(RECORD / 'deployment_receipt.json', deployment)
    launch = {
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'first_due_at_utc': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)).isoformat(),
        'interval_seconds': 1800, 'max_observations': 160,
        'replaces_observer': str(OLD.relative_to(ROOT)),
        'replacement_reason': 'Predecessor exited on SSH 255 after 48 committed snapshots; connection restored and incident reviewed.',
        'previous_observer_pid': state['pid'], 'previous_observer_start_ticks': state['proc_start_ticks'],
        'previous_observation_count': 48,
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sorted(NEW.glob('*.py'))},
        'incident_review_sha256': sha(RECORD / 'incident_review.json'),
        'training_started_or_approved_by_observer': False,
    }
    write(NEW / 'launch.json', launch)
    with (NEW / 'observations.jsonl').open('x'):
        pass
    with (NEW / 'stdout.log').open('xb') as stdout, (NEW / 'stderr.log').open('xb') as stderr:
        process = subprocess.Popen([sys.executable, str(NEW / 'observe.py')], cwd=ROOT,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
    deadline = time.monotonic() + 10
    while not (NEW / 'observer_state.json').exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(.1)
    assert process.poll() is None, 'Inspect failed launch; never retry this launcher.'
    newstate = read(NEW / 'observer_state.json')
    fields = (Path('/proc') / str(process.pid) / 'stat').read_text().rsplit(')', 1)[1].split()
    assert newstate['pid'] == process.pid and newstate['proc_start_ticks'] == fields[19]
    assert fields[0] not in ('Z', 'X')
    assert newstate['status'] == 'WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET'
    assert newstate['due_at_utc'] == launch['first_due_at_utc']
    assert lock.stat().st_ino == inode
    with lock.open('r+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError('Successor does not hold original observer lock')
    write(NEW / 'launch_process.json', {
        'status': 'FRESH_OBSERVER_STARTED_ONCE_AFTER_REVIEWED_SSH_FAILURE',
        'pid': process.pid, 'proc_start_ticks': fields[19],
        'original_observer_lock_inode': inode, 'original_observer_lock_held': True,
        'first_due_at_utc': newstate['due_at_utc'],
        'training_modified_or_restarted': False, 'full_goal_complete': False,
    })
    synced = sync([NEW / 'launch.json', NEW / 'launch_process.json', NEW / 'observer_state.json',
                   NEW / 'observations.jsonl', RECORD / 'deployment_receipt.json'])
    write(RECORD / 'launch_sync_receipt.json', synced)
    print(json.dumps({'status': 'NEW_OBSERVER_LIVE_OLD_EVIDENCE_UNCHANGED',
                      'observer': str(NEW.relative_to(ROOT)), 'pid': process.pid,
                      'proc_start_ticks': fields[19], 'first_due_at_utc': newstate['due_at_utc'],
                      'full_goal_complete': False}))


if __name__ == '__main__':
    main()
