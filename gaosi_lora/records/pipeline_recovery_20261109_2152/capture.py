"""Preserve current alert evidence with read-only remote access, once."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HELPER = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'

CODE = r'''
import base64,datetime as dt,hashlib,json
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora')
files=set(r.glob('gaosi/*.py'))
files.update(r.glob('records/pipeline_observed_blocking_v8/tools/*.py'))
for name in ['tools/job.py','tools/device_guard.py','tools/guarded_pipeline.py',
 'records/phi_selection_policy.json','records/phi_recovery_development_plan.json',
 'records/phi_development_assessment.json','records/phi_architecture_selected.json',
 'records/horizon_policy_v1.json','records/horizon_plan_v1.json',
 'records/organization_training_policy.json','records/organization_training_plan.json',
 'records/o4_parents_ready.json','checks/horizon_cpu.json','checks/horizon_upstream_cpu.json',
 'records/scheduled_review_follower_20261109_1955/state.json',
 'records/parents_ready.json','records/calibration_frozen.json']:
 p=r/name
 if p.is_file():files.add(p)
jobs=[]
for p in sorted((r/'records/jobs').glob('*.json')):
 j=json.loads(p.read_text());name=j['name']
 if 'observed_blocking_v8' not in name and name!='phi_dev_paired_adam_t4_medium_s17' and not name.startswith('o4_'):continue
 files.add(p)
 status=j['status'];identity=None
 if status=='RUNNING':
  proc=Path('/proc')/str(j['child_pid'])
  try:
   fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
   identity={'pid':j['child_pid'],'start_ticks':fields[19],'state':fields[0],
    'matches':fields[19]==j['child_proc_start_ticks'] and fields[0] not in ('Z','X'),
    'cmdline':(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode()}
  except FileNotFoundError:identity={'pid':j['child_pid'],'matches':False,'state':'ABSENT'}
 jobs.append({'job':j,'identity':identity})
 for relative in [f'configs/{name}.json',f'records/logs/{name}.log']:
  item=r/relative
  if item.is_file() and (status in ['RUNNING','FAIL'] or name.startswith(('horizon_','o4_'))):files.add(item)
 if status in ['RUNNING','FAIL'] or name.startswith(('horizon_','o4_')) or name=='phi_dev_paired_adam_t4_medium_s17':
  for suffix in ['config.json','events.jsonl','result.json','source_sha256.json']:
   item=r/'runs'/name/suffix
   if item.is_file():files.add(item)
output={}
for p in sorted(files):
 raw=p.read_bytes();output[str(p.relative_to(r))]={'sha256':hashlib.sha256(raw).hexdigest(),
  'base64':base64.b64encode(raw).decode()}
print(json.dumps({'observed_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
 'files':output,'jobs':jobs,'device_lock_inode':Path('/tmp/gaosi_musa_device0.lock').stat().st_ino}))
'''


def main():
    assert not (HERE / 'capture_receipt.json').exists()
    assert hashlib.sha256(HELPER.read_bytes()).hexdigest() == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = json.loads(module.remote(CODE, {}))
    assert value['device_lock_inode'] == 31148027670
    manifest = {}
    for relative, item in value.pop('files').items():
        destination = HERE / 'snapshot' / relative
        assert destination.resolve().is_relative_to(HERE / 'snapshot')
        raw = base64.b64decode(item['base64'], validate=True)
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as handle:
            handle.write(raw)
        manifest[str(destination.relative_to(ROOT))] = item['sha256']
    for filename, data in [('capture_runtime.json', value), ('manifest.json', manifest)]:
        with (HERE / filename).open('x') as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write('\n')
    receipt = {'status': 'READ_ONLY_ALERT_AND_CURRENT_RUNTIME_EVIDENCE_PRESERVED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'observed_at_utc': value['observed_at_utc'], 'files': len(manifest),
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'manifest_sha256': hashlib.sha256((HERE / 'manifest.json').read_bytes()).hexdigest(),
        'runtime_sha256': hashlib.sha256((HERE / 'capture_runtime.json').read_bytes()).hexdigest(),
        'training_modified_or_restarted': False, 'method_validated': False, 'full_goal_complete': False}
    with (HERE / 'capture_receipt.json').open('x') as handle:
        json.dump(receipt, handle, indent=2)
        handle.write('\n')
    for path in [Path(__file__), HERE / 'manifest.json', HERE / 'capture_runtime.json', HERE / 'capture_receipt.json']:
        module.sync(path)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
