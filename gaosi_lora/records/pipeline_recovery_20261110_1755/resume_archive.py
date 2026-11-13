"""Recover the existing 17:45 capture after a terminal observer transport error."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = ROOT / 'records/pipeline_observer_20261110_1315'
RELATIVE = 'records/pipeline_monitor_20261110_174541_utc'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    state = json.loads((OLD / 'observer_state.json').read_text())
    assert state['status'] == 'OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART'
    assert state['pid'] == 2464224 and state['proc_start_ticks'] == '156222690'
    proc = Path('/proc') / str(state['pid'])
    if proc.exists():
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        assert fields[0] in ('Z', 'X') or fields[19] != state['proc_start_ticks']
    for name in ('observer_state.json', 'observer.stderr.txt', 'observer.stdout.txt', 'observations.jsonl'):
        target = HERE / ('predecessor_' + name)
        data = (OLD / name).read_bytes()
        if target.exists():
            assert target.read_bytes() == data
        else:
            with target.open('xb') as handle:
                handle.write(data)
    submission = json.loads((OLD / 'collector_submission_result_37.json').read_text())
    assert submission['returncode'] == 0 and submission['submitted_once']
    expected = json.loads((OLD / 'transfer_37/manifest.json').read_text())['sha256']
    assert expected == '6444d8666cbd14f2f202724d038eb63656bf5c53bae6f11f7d5ed91c3bd3a064'
    helper = ROOT / 'records/pipeline_recovery_20261110_0525/archive_transport.py'
    assert sha(helper) == 'e1515c622f24eae7a0f2bb798a3475612fb1e4b336a6764f45b5b7c3a16f1788'
    spec = importlib.util.spec_from_file_location('archive_transport', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = '''import datetime as dt,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);folder=r/p['observation'];archive=Path(str(folder)+'.tar.gz')
assert hashlib.sha256(archive.read_bytes()).hexdigest()==p['sha256']
result=json.loads((folder/'result.json').read_text());checked=[]
assert len([row for row in result['jobs'] if row['controller']])==15
for row in result['jobs']:
 job=row['job']
 live=json.loads((r/'records/jobs'/(job['name']+'.json')).read_text())
 if job['status']!='RUNNING':
  assert live==job,job['name']
  continue
 assert live['status']=='RUNNING' and live['child_pid']==job['child_pid'] and live['child_proc_start_ticks']==job['child_proc_start_ticks'],job['name']
 proc=Path('/proc')/str(job['child_pid']);fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
 assert fields[0] not in ('Z','X') and fields[19]==job['child_proc_start_ticks'],job['name']
 argv=[x.decode() for x in (proc/'cmdline').read_bytes().split(bytes([0])) if x]
 assert argv==row['identity']['argv'],job['name']
 checked.append({'name':job['name'],'controller':row['controller'],'pid':job['child_pid'],'start_ticks':fields[19],'state':fields[0],'argv_matches_capture':True})
assert len([x for x in checked if x['controller']])==sum(row['controller'] and row['job']['status']=='RUNNING' for row in result['jobs'])
assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino==31148027670
print(json.dumps({'status':'EXISTING_CAPTURE_AND_CURRENT_RUNNING_PROCESS_IDENTITIES_VERIFIED','utc':dt.datetime.now(dt.timezone.utc).isoformat(),'archive_sha256':p['sha256'],'result_sha256':hashlib.sha256((folder/'result.json').read_bytes()).hexdigest(),'running':checked,'unresolved_alerts_in_capture':result['unresolved_alerts'],'pending_reviews_in_capture':result['pending_scientific_reviews'],'training_modified_or_restarted':False}))
'''
    preflight = json.loads(module.transport.remote(code, {'observation': RELATIVE, 'sha256': expected}))
    preserve(HERE / 'preflight.json', preflight)
    result, count, = module.fetch(RELATIVE, expected, HERE / 'archive_transfer')
    assert sha(ROOT / RELATIVE / 'result.json') == preflight['result_sha256']
    preserve(HERE / 'capture_recovery.json', {
        'status': 'EXISTING_CAPTURE_RECOVERED_WITHOUT_RECOLLECTION',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'observation': RELATIVE, 'archive_sha256': expected,
        'source_files_rehashed': count, 'preflight_sha256': sha(HERE / 'preflight.json'),
        'transfer_receipt_sha256': sha(HERE / 'archive_transfer/receipt.json'),
        'predecessor_state_sha256': sha(HERE / 'predecessor_observer_state.json'),
        'predecessor_ledger_sha256': sha(HERE / 'predecessor_observations.jsonl'),
        'collector_resubmitted': False, 'training_or_controllers_modified_or_restarted': False,
        'method_validated': False, 'full_goal_complete': False})
    print(json.dumps({'status': 'EXISTING_CAPTURE_RECOVERED_WITHOUT_RECOLLECTION',
                      'source_files_rehashed': count, 'meta_runs': result['meta_runs'],
                      'unresolved_alerts': result['unresolved_alerts']}), flush=True)


if __name__ == '__main__':
    main()
