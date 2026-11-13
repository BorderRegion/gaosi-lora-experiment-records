"""Verify the first replacement capture, preserved evidence, and live processes."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
OBSERVER = ROOT / 'records/pipeline_observer_20261110_0655'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def identity(expected, source=None):
    proc = Path('/proc') / str(expected['pid'])
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == expected['start_ticks']
    if source is not None:
        assert str(source).encode() in (proc / 'cmdline').read_bytes()
    return {'pid': expected['pid'], 'start_ticks': fields[19], 'state': fields[0]}


def main():
    output = HERE / 'recovery_verification.json'
    assert not output.exists()
    before = read(HERE / 'before.json')
    assert not (Path('/proc') / str(before['old_observer_state']['pid'])).exists()
    for name, expected in before['evidence'].items():
        assert sha(ROOT / name) == expected['sha256']
        assert (ROOT / name).stat().st_size == expected['bytes']
    start = read(OBSERVER / 'start_receipt.json')
    observed = identity(start, OBSERVER / 'observe.py')
    state = read(OBSERVER / 'observer_state.json')
    assert state['pid'] == observed['pid'] and state['proc_start_ticks'] == observed['start_ticks']
    assert state['observations'] >= 17 and state['status'] == 'WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET'
    maintainer = read(ROOT / 'records/scheduled_review_recovery_20261109_0855/ssh_maintainer_state.json')
    ssh = {key: identity(maintainer[key]) for key in ['identity', 'master_identity']}
    review = read(OBSERVER / 'observation_verification_20261110_0650.json')
    progress = read(OBSERVER / 'meta_progress_20261110_0650.json')
    assert review['status'] == 'SCHEDULED_SNAPSHOT_AND_MONITOR_REFERENCES_REHASHED_NO_NEW_ALERT'
    assert review['observation'] == progress['observation'] == 'records/pipeline_monitor_20261110_065036_utc'
    assert not review['new_PASS_jobs_requiring_separate_endpoint_review'] and not review['pending_scientific_reviews']
    launch = read(OBSERVER / 'launch.json')
    checks = dict(launch['source_sha256'])
    files = [HERE / name for name in ['before.json', 'deduplication_receipt.json',
             'deduplication_verification.json', 'deduplication.jsonl.gz', 'recovery_receipt.json',
             'remote_preflight.json', 'launch_intent.json', 'child_identity.json']]
    files += [OBSERVER / name for name in ['launch.json', 'start_receipt.json',
              'observation_verification_20261110_0650.json', 'meta_progress_20261110_0650.json']]
    files += [Path(__file__)]
    for path in files:
        checks[str(path.relative_to(ROOT))] = sha(path)
    for name, expected in checks.items():
        assert sha(ROOT / name) == expected
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    transport.sync(Path(__file__))
    code = '''import datetime as dt,gzip,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p['checks'].items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
journal=gzip.decompress((r/p['journal']).read_bytes())
assert hashlib.sha256(journal).hexdigest()==p['journal_sha256']
rows=[json.loads(line) for line in journal.splitlines()]
assert len(rows)==8536 and sum('completed' in row for row in rows)==4268
active=[]
for expected in p['processes']:
 name=expected['name'];job=json.loads((r/'records/jobs'/(name+'.json')).read_text())
 assert job['status']=='RUNNING' and job['child_pid']==expected['pid'] and job['child_proc_start_ticks']==expected['start_ticks']
 proc=Path('/proc')/str(expected['pid']);f=(proc/'stat').read_text().rsplit(')',1)[1].split()
 assert f[0] not in ('Z','X') and f[19]==expected['start_ticks']
 assert b'MUSA_LAUNCH_BLOCKING=1' in (proc/'environ').read_bytes().split(b'\\0')
 active.append({'name':name,'pid':expected['pid'],'start_ticks':f[19],'state':f[0],'wchan':(proc/'wchan').read_text()})
assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino==31148027670
print(json.dumps({'checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'reference_files_rehashed':len(p['checks']),'compressed_journal_restores_original_bytes':True,'active_processes':active,'device_lock_inode':31148027670}))
'''
    remote = json.loads(transport.remote(code, {'checks': checks,
             'journal': str((HERE / 'deduplication.jsonl.gz').relative_to(ROOT)),
             'journal_sha256': sha(HERE / 'deduplication.jsonl'),
             'processes': read(HERE / 'remote_preflight.json')['active_processes']}))
    stat = os.statvfs(ROOT)
    value = {'status': 'REPLACEMENT_OBSERVER_FIRST_CAPTURE_REVIEWED_AFTER_EVIDENCE_PRESERVING_LOCAL_DISK_RECOVERY',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'observer': observed, 'ssh_processes': ssh, 'remote': remote,
             'evidence_sha256': checks, 'local_available_bytes': stat.f_bavail * stat.f_frsize,
             'preserved_old_observer_state_and_logs_rehashed': True,
             'snapshot_source_files_independently_rehashed': review['snapshot_source_files_rehashed'],
             'committed_inner_steps': progress['runs'][0]['committed_executed_inner_updates'],
             'new_observer_headroom_guard_minimum_bytes': 128 * 1024**2,
             'training_or_original_controllers_modified_or_restarted': False,
             'method_validated': False, 'full_goal_complete': False}
    preserve = json.dumps(value, indent=2, allow_nan=False) + '\n'
    with output.open('x') as handle:
        handle.write(preserve)
    transport.sync(output)
    print(json.dumps({'status': value['status'], 'receipt_sha256': sha(output),
                      'committed_inner_steps': value['committed_inner_steps'],
                      'local_available_bytes': value['local_available_bytes']}))


if __name__ == '__main__':
    main()
