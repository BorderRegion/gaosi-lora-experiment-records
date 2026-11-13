"""One scheduled observation; leave training and its queues untouched."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time
import os
import fcntl

ROOT = Path('/home/ubuntu/diff/gaosi_lora')
DUE = dt.datetime(2026, 10, 30, 6, 25, 17, tzinfo=dt.timezone.utc)
STATE = ROOT / 'records/scheduled_check_20261030_1155_diagnostics_pair.json'
REL = 'records/episode7_same_state_monitor_20261030_035517_utc'
ARCHIVE = 'records/episode7_same_state_monitor_20261030_035517_utc.tar.gz'
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
       'root@training.example.invalid']


def save(status, **extra):
    record = {'status': status, 'pid': os.getpid(),
              'proc_start_ticks': Path('/proc/self/stat').read_text().split()[21],
              'updated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'due_at_utc': DUE.isoformat(), 'remote_observation': REL, **extra}
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(record, indent=2) + '\n')
    temp.replace(STATE)
    print(json.dumps(record), flush=True)


def remote(command, **kwargs):
    return subprocess.run(SSH + [command], check=True, timeout=180, **kwargs)


def observe():
    save('WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET')
    while True:
        remaining = (DUE - dt.datetime.now(dt.timezone.utc)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 30))
    save('INSPECTING')
    code = "import hashlib,json,pathlib,shutil,subprocess,tarfile,datetime,os\nroot=pathlib.Path('/root/autodl-tmp/gaosi_lora');out=root/__OBSERVATION__;out.mkdir(exist_ok=False)\ndef identity(pid,ticks):\n try:\n  p=pathlib.Path('/proc')/str(pid); fields=(p/'stat').read_text().rsplit(')',1)[1].split()\n  return {'pid':pid,'start_ticks':fields[19],'state':fields[0],'matches':fields[19]==ticks,'cmdline':(p/'cmdline').read_bytes().replace(bytes([0]),b' ').decode(errors='replace')}\n except FileNotFoundError:return {'pid':pid,'exists':False,'expected_ticks':ticks}\ndef digest(p):\n h=hashlib.sha256()\n with p.open('rb') as f:\n  for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)\n return h.hexdigest()\nobs={'observed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Bounded read-only diagnostic observation, not a training controller or scientific validation.','jobs':{},'training_modified_or_restarted':False,'full_goal_complete':False,'storage_GiB':{p:{'total':round(shutil.disk_usage(p).total/2**30,2),'free':round(shutil.disk_usage(p).free/2**30,2)} for p in ['/root/autodl-tmp','/']}}\nfor suffix,pid,ticks in [('L_episode7_same_state_async_r1',585692,'61402582'),('L_episode7_same_state_blocking_r1',585694,'61402592')]:\n job='diagnose_'+suffix\n jobfile=root/'records/jobs'/(job+'.json');jobdata=json.loads(jobfile.read_text());run=root/'runs/diagnostics'/suffix\n row={'job':jobdata,'identity':identity(pid,ticks),'captures_kept_remote':[]}\n files=[jobfile,root/'records/logs'/(job+'.log'),root/'configs'/(job+'.json')]\n for p in run.rglob('*'):\n  if not p.is_file():continue\n  if p.name.startswith('hvp_capture_'):\n   info={'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'mtime':p.stat().st_mtime}\n   if jobdata['status']!='RUNNING':info['sha256']=digest(p)\n   row['captures_kept_remote'].append(info)\n  elif p.stat().st_size<10*1024*1024:files.append(p)\n for name in ['result.json','repeat_summary.json','diagnostic_result.json']:\n  p=run/name\n  if p.exists():row[name]=json.loads(p.read_text())\n sub=run\n for name in ['gradient_observations.jsonl','events.jsonl']:\n  p=sub/name\n  if p.exists():\n   rows=[json.loads(line) for line in p.read_text().splitlines()]\n   row[name]={'records':len(rows),'last':rows[-1] if rows else None}\n p=run/'hvp_trace.jsonl'\n if p.exists():row['hvp_trace']=[json.loads(l) for l in p.read_text().splitlines()]\n for p in files:\n  if p.exists():\n   dest=out/'snapshot'/p.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)\n obs['jobs'][suffix]=row\ntelemetry=subprocess.run(['mthreads-gmi'],capture_output=True,text=True,timeout=30)\n(out/'gpu.txt').write_text(telemetry.stdout+telemetry.stderr)\n(out/'result.json').write_text(json.dumps(obs,indent=2))\nsources=sorted(p for p in out.rglob('*') if p.is_file())\nmanifest={str(p.relative_to(root)):digest(p) for p in sources}\n(out/'archive_source_sha256.json').write_text(json.dumps(manifest,indent=2))\narchive=root/__ARCHIVE__\nwith archive.open('xb') as f:\n with tarfile.open(fileobj=f,mode='w:gz') as handle:\n  for p in sources+[out/'archive_source_sha256.json']:handle.add(p,arcname=str(p.relative_to(root)))\nprint(json.dumps({'archive':str(archive.relative_to(root)),'sha256':digest(archive),'result':obs}))\n"
    code = code.replace('__OBSERVATION__', repr(REL)).replace('__ARCHIVE__', repr(ARCHIVE))
    reply = remote('/usr/bin/python -c ' + shlex.quote(code), capture_output=True, text=True)
    (ROOT / (REL + '.remote_stdout.txt')).write_text(reply.stdout)
    (ROOT / (REL + '.remote_stderr.txt')).write_text(reply.stderr)
    record = json.loads(reply.stdout.splitlines()[-1])
    destination = ROOT / ARCHIVE
    if destination.exists():
        raise RuntimeError('Refuse to overwrite an existing local observation archive')
    with destination.open('xb') as handle:
        remote('cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + ARCHIVE), stdout=handle, stderr=subprocess.PIPE)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest != record['sha256']:
        raise RuntimeError('Observation archive checksum mismatch')
    with tarfile.open(destination, 'r:gz') as handle:
        members = handle.getmembers()
        for member in members:
            target = (ROOT / member.name).resolve()
            if not member.isfile() or not target.is_relative_to(ROOT / REL):
                raise RuntimeError('Unexpected archive member')
            if target.exists():
                raise RuntimeError('Refuse to overwrite an existing local evidence file')
        for member in members:
            target = ROOT / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.extractfile(member) as source, target.open('xb') as dest:
                shutil.copyfileobj(source, dest)
    manifest = json.loads((ROOT / REL / 'archive_source_sha256.json').read_text())
    for name, expected in manifest.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Observation source checksum mismatch: ' + name)
    save('OBSERVATION_DOWNLOADED_REQUIRES_REVIEW', archive=ARCHIVE,
         archive_sha256=digest, source_files_verified=len(manifest), result=record['result'])



def main():
    global DUE, STATE, REL, ARCHIVE
    ledger = ROOT / 'records/episode7_same_state_pair_20261030_0600/observations.jsonl'
    supervisor_state = ROOT / 'records/episode7_same_state_pair_20261030_0600/observer_state.json'
    for index in range(8):
        label = DUE.strftime('%Y%m%d_%H%M%S_utc')
        STATE = ROOT / ('records/scheduled_episode7_same_state_' + label + '.json')
        REL = 'records/episode7_same_state_monitor_' + label
        ARCHIVE = REL + '.tar.gz'
        snapshot = {'status':'WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', 'pid':os.getpid(),
                    'proc_start_ticks':Path('/proc/self/stat').read_text().split()[21],
                    'due_at_utc':DUE.isoformat(), 'observation_state':str(STATE.relative_to(ROOT)),
                    'observations':index, 'production_modified_or_restarted':False}
        supervisor_state.write_text(json.dumps(snapshot,indent=2)+'\n')
        observe()
        result=json.loads((ROOT/REL/'result.json').read_text())
        with ledger.open('a') as f:f.write(json.dumps({'observation':REL,'result':result})+'\n')
        states=[result['jobs'][name]['job']['status'] for name in
                ['L_episode7_same_state_async_r1','L_episode7_same_state_blocking_r1']]
        alerts=[]
        for name in ['L_episode7_same_state_async_r1','L_episode7_same_state_blocking_r1']:
            job=result['jobs'][name]
            if job['job']['status']=='RUNNING' and job['identity'].get('matches') is not True:
                alerts.append(name+': matching process absent')
            summaries=job.get('repeat_summary.json',[])
            if any(row.get('all_finite') is False or (row.get('norm') or 0)>1e6 for row in summaries):
                alerts.append(name+': nonfinite or enormous raw gradient; inspect captured evidence')
        if alerts or any(state=='FAIL' for state in states) or all(state=='PASS' for state in states):
            snapshot.update(status='DIAGNOSTICS_TERMINAL_REQUIRES_AGENT_REVIEW',
                            last_observation=REL,observations=index+1,alerts=alerts)
            supervisor_state.write_text(json.dumps(snapshot,indent=2)+'\n')
            return
        DUE=dt.datetime.fromisoformat(result['observed_at_utc'])+dt.timedelta(minutes=30)
    snapshot.update(status='OBSERVATION_LIMIT_REQUIRES_AGENT_REVIEW',observations=8)
    supervisor_state.write_text(json.dumps(snapshot,indent=2)+'\n')

if __name__ == '__main__':
    try:
        with (ROOT / 'records/supervise_recompute_v7.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except Exception as error:
        failure = {'status':'OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART', 'error_type':type(error).__name__, 'error':str(error), 'pid':os.getpid(), 'proc_start_ticks':Path('/proc/self/stat').read_text().split()[21]}
        (ROOT / 'records/episode7_same_state_pair_20261030_0600/observer_state.json').write_text(json.dumps(failure,indent=2)+'\n')
        save('OBSERVATION_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART',
             error_type=type(error).__name__, error=str(error))
        raise
