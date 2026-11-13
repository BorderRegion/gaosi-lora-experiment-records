
from pathlib import Path
import json,hashlib,os,subprocess,datetime,time
root=Path('/root/autodl-tmp/gaosi_lora');os.chdir(root)
entries=[{'job': 'diagnose_L_episode7_same_state_async_r1', 'config': 'configs/diagnose_L_episode7_same_state_async_r1.json', 'config_sha256': '531c4a5830a7f6f8fea42db58378c0ce10f1c8f15b320770b12787e6a52ce366', 'MUSA_LAUNCH_BLOCKING': None}, {'job': 'diagnose_L_episode7_same_state_blocking_r1', 'config': 'configs/diagnose_L_episode7_same_state_blocking_r1.json', 'config_sha256': 'fbc5dd6022a978bb2d110376e052573964457de232e2b1987dae587271be5e27', 'MUSA_LAUNCH_BLOCKING': '1'}]
record=root/'records/episode7_same_state_pair_20261030_0600';record.mkdir(exist_ok=False)
lock=Path('/tmp/gaosi_musa_device0.lock');assert lock.stat().st_ino==31148027670
for name,want in {'tools/diagnose_repeat_gradient.py': 'd6e056e8d71183091c0a37b1af84aa5e56eef735a3b485eb95d3bf812463a35c', 'tools/job.py': '92ff3b837b26b968033ad851372e8b7a935d8bac83c1ccbf6d8ebe0735288951', 'tools/device_guard.py': 'b1068dc78b14c3e9dc55547d6d2050e3a0899a5d42407c82b8db56cdd4760bcf'}.items():assert hashlib.sha256((root/name).read_bytes()).hexdigest()==want,name
for p in (root/'records/jobs').glob('*.json'):
 s=json.loads(p.read_text())
 if s.get('status')=='RUNNING':
  raise RuntimeError('Existing job marked RUNNING; inspect before launching pair: '+str(p))
for entry in entries:
 p=root/entry['config'];assert hashlib.sha256(p.read_bytes()).hexdigest()==entry['config_sha256']
 assert not (root/'records/jobs'/(entry['job']+'.json')).exists()
 result=subprocess.run(['/usr/bin/python','tools/diagnose_traced_episode.py','--config',entry['config'],'--validate-only'],capture_output=True,text=True,check=True,timeout=30)
 entry['validation']=json.loads(result.stdout);assert entry['validation']['phi_updates']==0 and entry['validation']['episode']==7
runtime_source=Path('/usr/local/lib/python3.10/dist-packages/torch_musa/csrc/core/MUSAMiscFunctions.cpp')
if not runtime_source.exists() or 'MUSA_LAUNCH_BLOCKING' not in runtime_source.read_text():raise RuntimeError('Installed backend source no longer recognizes launch-blocking variable')
(record/'MUSAMiscFunctions.cpp').write_bytes(runtime_source.read_bytes())
ledger={'created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'LAUNCHING','experiment':'Four repeated no-update reconstructed episode7 analytic replays per process, async then blocking, same pinned starting phi/RNG/streams','production_modified_or_restarted':False,'formal_data_used':False,'core_budget_credit':0,'diagnostic_inner_updates_max':64,'diagnostic_meta_updates_max':0,'device_lock_inode':lock.stat().st_ino,'jobs':[],'limitations':['Same starting phi is restored from the same preserved prefix checkpoint; compare actual tensor bytes and all logged forward records before same-state conclusions.','No finite-gradient or exit-code result alone validates H2.','Environment setting is recognized by installed backend source; runtime efficacy still to be measured.']}
ledgerfile=record/'launch.json'
def save():
 temp=ledgerfile.with_suffix('.tmp');temp.write_text(json.dumps(ledger,indent=2)+'\n');temp.replace(ledgerfile)
save()
for entry in entries:
 envcmd=['/usr/bin/env','MUSA_LAUNCH_BLOCKING=1'] if entry['MUSA_LAUNCH_BLOCKING']=='1' else ['/usr/bin/env','-u','MUSA_LAUNCH_BLOCKING']
 command=['/usr/bin/python','-u','tools/job.py','--name',entry['job'],'--musa-lock',str(lock),'--',*envcmd,'/usr/bin/python','-u','tools/diagnose_repeat_gradient.py','--config',entry['config']]
 logfile=root/'records/logs'/(entry['job']+'.log');statefile=root/'records/jobs'/(entry['job']+'.json')
 with logfile.open('xb',buffering=0) as f:
  child=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
 row={**entry,'command':command,'wrapper_pid':child.pid,'wrapper_start_ticks':Path('/proc/'+str(child.pid)+'/stat').read_text().split()[21]};ledger['jobs'].append(row);save()
 deadline=time.monotonic()+12
 while True:
  if statefile.exists():
   state=json.loads(statefile.read_text())
   if state.get('child_pid'):break
  if child.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Inspect launched wrapper; never duplicate on observation timeout')
  time.sleep(.1)
 fields=Path('/proc/'+str(state['child_pid'])+'/stat').read_text().rsplit(')',1)[1].split()
 row.update(job_state=state,identity_live=fields[0]!='Z' and fields[19]==state['child_proc_start_ticks'])
 envfile=Path('/proc/'+str(state['child_pid'])+'/environ')
 row['observed_child_launch_blocking']=[v.decode(errors='replace') for v in envfile.read_bytes().split(bytes([0])) if v.startswith(b'MUSA_LAUNCH_BLOCKING=')]
 row['log_tail']=logfile.read_text()[-1200:];save()
ledger['status']='TWO_BOUNDED_DIAGNOSTICS_LAUNCHED_SAME_DEVICE_LOCK';save()
print(json.dumps(ledger,indent=2))
