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
RECORD = ROOT / 'records/observed_blocking_core_20261030_0810'
STATE = RECORD / 'observer_state.json'
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
       'root@training.example.invalid']
REMOTE = "\nfrom pathlib import Path\nimport datetime as dt,hashlib,json,os,shutil,subprocess,tarfile\nroot=Path('/root/autodl-tmp/gaosi_lora');os.chdir(root)\nout=Path(OBSERVATION);out.mkdir(exist_ok=False)\nname='phi_mix_O1_L_H2_core_s17_observed_blocking_v8'\njobpath=Path('records/jobs',name+'.json');job=json.loads(jobpath.read_text())\nproc=Path('/proc',str(job['child_pid']))\ntry:\n fields=(proc/'stat').read_text().rsplit(')',1)[1].split()\n identity={'pid':job['child_pid'],'state':fields[0],'start_ticks':fields[19],\n           'matches':fields[19]==job['child_proc_start_ticks'],\n           'cmdline':(proc/'cmdline').read_bytes().replace(bytes([0]),b' ').decode(errors='replace'),\n           'launch_blocking_environment':[x.decode() for x in (proc/'environ').read_bytes().split(bytes([0])) if x.startswith(b'MUSA_LAUNCH_BLOCKING=')]}\nexcept FileNotFoundError:\n identity={'pid':job['child_pid'],'exists':False,'expected_ticks':job['child_proc_start_ticks']}\ndef digest(p):\n h=hashlib.sha256()\n with p.open('rb') as f:\n  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)\n return h.hexdigest()\nrun=Path('runs')/name\nfiles=[jobpath,Path('records/logs',name+'.log'),Path('configs',name+'.json'),\n       Path('tools/train_observed_meta.py'),Path('tools/validate_observed_meta.py'),\n       Path('records/observed_blocking_core_20261030_0810/training_manifest.json')]\nresult={'observed_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'job':job,'identity':identity,\n        'method_validated':False,'training_restarted_or_modified_by_observer':False,\n        'storage_GiB':{p:{'total':round(shutil.disk_usage(p).total/2**30,2),\n                           'free':round(shutil.disk_usage(p).free/2**30,2)} for p in ['/root/autodl-tmp','/']}}\nfor n in ['result.json','observed_training_result.json']:\n p=run/n\n if p.exists():result[n]=json.loads(p.read_text())\nfor n in ['events.jsonl','raw_gradients.jsonl']:\n p=run/n\n if p.exists():\n  rows=[json.loads(l) for l in p.read_text().splitlines()]\n  result[n]={'count':len(rows),'last':rows[-1] if rows else None}\n  if n=='events.jsonl':\n   ends=[x for x in rows if x.get('event')=='episode_end']\n   result['committed_episodes']=len(ends)\n   result['committed_inner_updates']=ends[-1]['inner_updates'] if ends else 0\n   result['last_episode_end']=ends[-1] if ends else None\n   result['last_inner']=next((x for x in reversed(rows) if x.get('event')=='inner'),None)\n  else:\n   result['all_observed_raw_gradients_finite']=all(x['raw_all_finite'] for x in rows)\n   result['maximum_observed_raw_norm']=max((x['raw_norm_cpu_fp64'] or 0 for x in rows),default=0)\n   result['gradient_guard_errors']=[x for x in rows if x.get('error')]\ncheckpoint=run/'latest.pt'\nfiles.extend(run/n for n in ['config.json','events.jsonl','raw_gradients.jsonl','latest.pt',\n                            'observed_training_manifest.json','observed_training_result.json',\n                            'train_observed_meta.py','source_sha256.json','result.json'])\nfiles.extend(sorted(run.glob('gradient_episode_*.pt'))[-2:])\nfiles.extend(sorted(run.glob('before_episode_*.pt'))[-2:])\nfiles.extend(run.glob('phi_*.pt'))\nfiles.extend((run/'source').glob('*.py'))\nfor p in sorted(set(files)):\n if p.exists():\n  target=out/'snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)\n# Training may advance after the first read. Digest the copied checkpoint as\n# additional evidence; never claim an atomic snapshot of the live directory.\np=out/'snapshot'/checkpoint\nif p.exists():\n import torch\n torch.set_num_threads(2)\n saved=torch.load(p,map_location='cpu',weights_only=False)\n def finite(x):\n  if torch.is_tensor(x):return bool(torch.isfinite(x).all())\n  if isinstance(x,dict):return all(finite(v) for v in x.values())\n  if isinstance(x,(list,tuple)):return all(finite(v) for v in x)\n  return True\n result['checkpoint']={'copied_artifact_sha256':digest(p),'next_episode':saved['next_episode'],\n                       'inner_updates':saved['inner_updates'],'phi_finite':finite(saved['phi']),\n                       'optimizer_finite':finite(saved['meta_optimizer']),\n                       'config_equal':saved['config']==json.loads((out/'snapshot'/run/'config.json').read_text())}\nresult['snapshot_limitation']='Individual live files may reflect adjacent moments; copied checkpoint is inspected immutably.'\ntelemetry=subprocess.run(['mthreads-gmi'],capture_output=True,text=True,timeout=30)\n(out/'gpu.txt').write_text(telemetry.stdout+telemetry.stderr)\n(out/'result.json').write_text(json.dumps(result,indent=2)+'\\n')\nsources=sorted(p for p in out.rglob('*') if p.is_file())\nmanifest=out/'archive_source_sha256.json'\nmanifest.write_text(json.dumps({str(p):digest(p) for p in sources},indent=2)+'\\n')\narchive=Path(str(out)+'.tar.gz')\nwith archive.open('xb') as f:\n with tarfile.open(fileobj=f,mode='w:gz') as handle:\n  for p in sources+[manifest]:handle.add(p,arcname=str(p))\nprint(json.dumps({'archive':str(archive),'sha256':digest(archive),'result':result}))\n"


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
    rel = 'records/observed_blocking_core_monitor_' + due.strftime('%Y%m%d_%H%M%S_utc')
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
    if result.get('maximum_observed_raw_norm', 0) > 1e6:
        alerts.append('Enormous raw gradient; inspect preserved artifacts')
    if result.get('all_observed_raw_gradients_finite') is False or result.get('gradient_guard_errors'):
        alerts.append('Gradient guard reported invalid values; inspect saved evidence')
    checkpoint = result.get('checkpoint')
    if checkpoint and not all(checkpoint[k] for k in ['phi_finite', 'optimizer_finite', 'config_equal']):
        alerts.append('Checkpoint is nonfinite or mismatches the full schedule')
    if result['job']['status'] == 'RUNNING' and result['identity'].get('launch_blocking_environment') != ['MUSA_LAUNCH_BLOCKING=1']:
        alerts.append('Process-local launch blocking environment absent')
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
        for index in range(160):
            result, review = observe(due, index)
            if result['job']['status'] != 'RUNNING' or review['alerts']:
                save('TERMINAL_OR_ALERT_REQUIRES_AGENT_REVIEW', observations=index + 1, **review)
                return
            due = dt.datetime.fromisoformat(result['observed_at_utc']) + dt.timedelta(minutes=30)
        save('OBSERVATION_LIMIT_REQUIRES_AGENT_REVIEW', observations=160, **review)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        save('OBSERVER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART',
             error_type=type(error).__name__, error=str(error))
        raise
