"""Bounded read-only observation every 30 minutes; never restart training."""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time

ROOT = Path('/home/ubuntu/diff/gaosi_lora')
RECORD = ROOT / 'records/episode7_fixed_tail_20261030_0730'
STATE = RECORD / 'observer_state.json'
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
       'root@training.example.invalid']
REMOTE = r'''
from pathlib import Path
import datetime as dt,hashlib,json,os,shutil,subprocess,tarfile
root=Path('/root/autodl-tmp/gaosi_lora');os.chdir(root)
out=Path(OBSERVATION);out.mkdir(exist_ok=False)
name='diagnose_L_episode7_fixed_tail_blocking_r1'
jobpath=Path('records/jobs',name+'.json');job=json.loads(jobpath.read_text())
pid=job['child_pid'];proc=Path('/proc',str(pid))
try:
 fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
 identity={'pid':pid,'state':fields[0],'start_ticks':fields[19],
           'matches':fields[19]==job['child_proc_start_ticks'],
           'cmdline':(proc/'cmdline').read_bytes().replace(bytes([0]),b' ').decode(errors='replace')}
except FileNotFoundError:
 identity={'pid':pid,'exists':False,'expected_ticks':job['child_proc_start_ticks']}
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
run=Path('runs/diagnostics/L_episode7_fixed_tail_blocking_r1')
files=[jobpath,Path('records/logs',name+'.log'),Path('configs',name+'.json'),
       Path('tools/diagnose_fixed_tail_gradient.py'),Path('tools/validate_fixed_tail_gradient.py')]
large=[]
if run.exists():
 for p in run.rglob('*'):
  if not p.is_file():continue
  if p.stat().st_size<10*1024*1024:files.append(p)
  else:
   row={'path':str(p),'bytes':p.stat().st_size,'mtime':p.stat().st_mtime}
   if job['status']!='RUNNING':row['sha256']=digest(p)
   large.append(row)
config=json.loads(Path('configs',name+'.json').read_text())
files.extend(Path(p) for p in config['source_code_sha256'])
for p in sorted(set(files)):
 if p.exists():
  target=out/'snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
result={'observed_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'job':job,'identity':identity,
        'production_modified_or_restarted':False,'method_validated':False,'core_budget_credit':0,
        'large_artifacts_kept_remote':large,
        'storage_GiB':{p:{'total':round(shutil.disk_usage(p).total/2**30,2),
                           'free':round(shutil.disk_usage(p).free/2**30,2)} for p in ['/root/autodl-tmp','/']}}
for n in ['tail_sensitivity.json','result.json']:
 p=run/n
 if p.exists():result[n]=json.loads(p.read_text())
p=run/'events.jsonl'
if p.exists():
 rows=[json.loads(l) for l in p.read_text().splitlines()]
 result['events']={'count':len(rows),'last':rows[-1] if rows else None}
telemetry=subprocess.run(['mthreads-gmi'],capture_output=True,text=True,timeout=30)
(out/'gpu.txt').write_text(telemetry.stdout+telemetry.stderr)
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
sources=sorted(p for p in out.rglob('*') if p.is_file())
manifest=out/'archive_source_sha256.json'
manifest.write_text(json.dumps({str(p):digest(p) for p in sources},indent=2)+'\n')
archive=Path(str(out)+'.tar.gz')
with archive.open('xb') as f:
 with tarfile.open(fileobj=f,mode='w:gz') as handle:
  for p in sources+[manifest]:handle.add(p,arcname=str(p))
print(json.dumps({'archive':str(archive),'sha256':digest(archive),'result':result}))
'''


def save(status, **extra):
    result = {'status': status, 'pid': os.getpid(),
              'proc_start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
              'updated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'production_modified_or_restarted': False, **extra}
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(result, indent=2) + '\n')
    temp.replace(STATE)
    print(json.dumps(result), flush=True)


def remote(command, **kwargs):
    return subprocess.run(SSH + [command], check=True, timeout=240, **kwargs)


def observe(due, index):
    rel = 'records/fixed_tail_monitor_' + due.strftime('%Y%m%d_%H%M%S_utc')
    save('WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', due_at_utc=due.isoformat(),
         observations=index, next_observation=rel)
    while True:
        remaining = (due - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 30))
    save('INSPECTING', due_at_utc=due.isoformat(), observations=index)
    code = REMOTE.replace('OBSERVATION', repr(rel))
    reply = remote('/usr/bin/python -c ' + shlex.quote(code), capture_output=True, text=True)
    (ROOT / (rel + '.remote_stdout.txt')).write_text(reply.stdout)
    (ROOT / (rel + '.remote_stderr.txt')).write_text(reply.stderr)
    info = json.loads(reply.stdout.splitlines()[-1])
    destination = ROOT / info['archive']
    with destination.open('xb') as handle:
        remote('cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + info['archive']),
               stdout=handle, stderr=subprocess.PIPE)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == info['sha256']
    with tarfile.open(destination, 'r:gz') as handle:
        for member in handle.getmembers():
            target = (ROOT / member.name).resolve()
            assert member.isfile() and target.is_relative_to(ROOT / rel)
            assert not target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.extractfile(member) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest)
    manifest = json.loads((ROOT / rel / 'archive_source_sha256.json').read_text())
    for name, expected in manifest.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    result = info['result']
    alerts = []
    if result['job']['status'] == 'RUNNING' and result['identity'].get('matches') is not True:
        alerts.append('Expected process identity absent')
    tail = result.get('tail_sensitivity.json', {})
    if tail.get('raw_gradient_norm_fp64', 0) > 1e6:
        alerts.append('Enormous raw gradient; inspect preserved artifacts')
    review = {'observation': rel, 'archive_sha256': info['sha256'],
              'verified_source_files': len(manifest), 'job_status': result['job']['status'],
              'alerts': alerts, 'production_modified_or_restarted': False}
    with (RECORD / 'observations.jsonl').open('a') as handle:
        handle.write(json.dumps(review) + '\n')
    return result, review


def main():
    launch = json.loads((RECORD / 'launch.json').read_text())
    due = dt.datetime.fromisoformat(launch['job_state']['started_at_utc']) + dt.timedelta(minutes=30)
    with (ROOT / 'records/supervise_recompute_v7.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index in range(8):
            result, review = observe(due, index)
            if result['job']['status'] != 'RUNNING' or review['alerts']:
                save('TERMINAL_OR_ALERT_REQUIRES_AGENT_REVIEW', observations=index + 1, **review)
                return
            due = dt.datetime.fromisoformat(result['observed_at_utc']) + dt.timedelta(minutes=30)
        save('OBSERVATION_LIMIT_REQUIRES_AGENT_REVIEW', observations=8, **review)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        save('OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART',
             error_type=type(error).__name__, error=str(error))
        raise
