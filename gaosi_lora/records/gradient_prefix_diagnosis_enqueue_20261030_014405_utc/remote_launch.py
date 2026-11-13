
import json,subprocess,datetime,hashlib,os,time
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora');os.chdir(root)
name='diagnose_core_prefix_v7_raw_gradient_r1'
config='configs/diagnose_core_prefix_v7_raw_gradient.json'
lock=Path('/tmp/gaosi_musa_device0.lock')
pins={'tools/diagnose_prefix_gradient.py': 'bde1f51a883987c37aee9b185f26890bfb31e38bb8844fee161db3771477540a', 'configs/diagnose_core_prefix_v7_raw_gradient.json': '826345d146610f066464ea5657c4ded113e2628d29311562f9144d2f7554c280'}
for file,expected in pins.items():
 if hashlib.sha256((root/file).read_bytes()).hexdigest()!=expected: raise RuntimeError('Remote diagnostic changed: '+file)
statefile=root/'records/jobs'/f'{name}.json'
if statefile.exists(): raise RuntimeError('Diagnostic job already recorded; inspect instead of duplicating')
for file in (root/'records/jobs').glob('*.json'):
 s=json.loads(file.read_text())
 if s.get('status')=='RUNNING' and any('diagnose_prefix_gradient.py' in x for x in s.get('command',[])):
  raise RuntimeError('Another prefix diagnostic is already queued/running: '+s['name'])
validation=subprocess.run(['/usr/bin/python','tools/diagnose_prefix_gradient.py','--config',config,'--validate-only'],capture_output=True,text=True,check=True,timeout=30)
validated=json.loads(validation.stdout)
if validated['inner_updates_max']!=64: raise RuntimeError('Unexpected diagnostic budget')
core=json.loads((root/'records/jobs/phi_mix_O1_L_H2_core_s17_recompute_v7.json').read_text())
proc=Path(f"/proc/{core.get('child_pid')}/stat")
f=proc.read_text().split() if proc.exists() else []
if core['status']!='RUNNING' or not f or f[2]=='Z' or f[21]!=core['child_proc_start_ticks']: raise RuntimeError('Existing core identity needs review')
st=lock.stat();key=f'{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}'
def holder():
 return [l for l in Path('/proc/locks').read_text().splitlines() if len(l.split())>=8 and l.split()[1:4]==['FLOCK','ADVISORY','WRITE'] and l.split()[5]==key]
before=holder()
if len(before)!=1 or before[0].split()[4]!=str(core['child_pid']): raise RuntimeError('Production does not hold expected existing lock')
out=root/'records'/'gradient_prefix_diagnosis_enqueue_20261030_014405_utc'
out.mkdir(exist_ok=False)
(root/'records/logs').mkdir(exist_ok=True)
command=['/usr/bin/python','-u','tools/job.py','--name',name,'--musa-lock',str(lock),'--','/usr/bin/python','-u','tools/diagnose_prefix_gradient.py','--config',config,'--execute']
logfile=root/'records/logs'/f'{name}.log'
with logfile.open('xb',buffering=0) as log:
 p=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
deadline=time.monotonic()+12
while True:
 if statefile.exists():
  state=json.loads(statefile.read_text())
  if state.get('child_pid') and logfile.exists() and 'waiting_device' in logfile.read_text(): break
 if p.poll() is not None or time.monotonic()>deadline: raise RuntimeError('Inspect the started diagnostic wrapper; do not duplicate it')
 time.sleep(.1)
child=Path(f"/proc/{state['child_pid']}/stat").read_text().split()
child_cmd=Path(f"/proc/{state['child_pid']}/cmdline").read_bytes().replace(b'\x00',b' ').decode()
after=holder()
log=logfile.read_text()
waiting=bool(state['status']=='RUNNING' and child[2]!='Z' and child[21]==state['child_proc_start_ticks'] and 'device_guard.py' in child_cmd and 'device_acquired' not in log and after and after[0].split()[4]==str(core['child_pid']))
evidence={'status':'QUEUED_WAITING_EXISTING_DEVICE_LOCK' if waiting else 'REQUIRES_REVIEW_NO_DUPLICATE_LAUNCH','observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'job':name,'state':state,'wrapper_proc_start_ticks':Path(f'/proc/{p.pid}/stat').read_text().split()[21],'child_identity_live':child[2]!='Z' and child[21]==state['child_proc_start_ticks'],'child_command':child_cmd,'launch_command':command,'validation':validated,'source_sha256':pins,'device_lock_inode':st.st_ino,'device_lock_holder_before':before,'device_lock_holder_after':after,'log':log,'diagnostic_gpu_started':not waiting,'production_training_started_stopped_resumed_or_modified':False,'diagnostic_inner_updates_max':64,'core_budget_credit':0,'formal_data_used':False,'cost_estimate':'About 30 minutes after production releases GPU; instrumentation can add overhead.','limitations':['New instrumented reconstruction, not exact replay of historical episode7.','CPU copies and synchronization can affect reproducibility.','Execution success does not establish numerical validity or efficacy.'],'recorder_schedule_changed':False}
with (out/'launch.json').open('x') as h:json.dump(evidence,h,ensure_ascii=False,indent=2);h.write('\n')
print(json.dumps(evidence,ensure_ascii=False))
if not waiting: raise RuntimeError('Inspect launch evidence')
