"""Local, bounded 30-minute evidence collection for the existing L job.

Adopts the already scheduled observation before making another SSH request.
Never starts, stops, resumes, or changes any training process or GPU lock.
An alert ends this recorder for agent review; it is not a training verdict.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import time


ROOT = Path(__file__).resolve().parents[1]
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
       'root@training.example.invalid']


def utc():
    return dt.datetime.now(dt.timezone.utc)


def identity(pid, ticks):
    path = Path(f'/proc/{pid}/stat')
    try:
        fields = path.read_text().split()
    except FileNotFoundError:
        return False
    return fields[2] != 'Z' and fields[21] == ticks


def write_json(path, data, exclusive=False):
    if exclusive:
        with path.open('x') as handle:
            handle.write(json.dumps(data, indent=2) + '\n')
    else:
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, indent=2) + '\n')
        temp.replace(path)


def verify_files(directory):
    directory = directory.resolve()
    manifest = json.loads((directory / 'archive_source_sha256.json').read_text())
    for name, expected in manifest.items():
        source = (ROOT / name).resolve()
        if not source.is_relative_to(directory.resolve()):
            raise ValueError('Source manifest escapes observation directory')
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Evidence checksum mismatch: ' + name)
    for required in ['result.json', 'config.json', 'events.jsonl', 'checkpoint.pt', 'job.json', 'job.log']:
        if str((directory / required).relative_to(ROOT)) not in manifest:
            raise ValueError('Required evidence absent from checksum manifest: ' + required)
    return len(manifest)


def assess(result, config, events, log, previous=None):
    alerts = []
    for key in ['config_sha_matches_manifest', 'checkpoint_config_matches',
                'checkpoint_phi_and_optimizer_finite']:
        if result.get(key) is not True:
            alerts.append(key)
    n, steps = result['completed_episodes'], result['inner_updates']
    if not 0 <= n <= len(config['episode_K']) or steps != sum(config['episode_K'][:n]):
        alerts.append('Checkpoint progress differs from declared curriculum')
    if not result['optimizer_steps'] or any(s != n for s in result['optimizer_steps']):
        alerts.append('Optimizer step differs from committed episode count')
    ends = [e for e in events if e.get('event') == 'episode_end']
    if [e['episode'] for e in ends] != list(range(len(ends))):
        alerts.append('Episode log is not contiguous from zero')
    # episode_end is logged just before atomic_save; allow that one-record race.
    if len(ends) not in [n, n + 1]:
        alerts.append('Checkpoint and event log need consistency review')
    if result.get('unparsed_event_lines'):
        alerts.append('Partial event line requires review')
    new_spikes = [s for s in result.get('large_preclip_norms', []) if s['episode'] != 7]
    if new_spikes:
        alerts.append('New large raw phi gradient: ' + json.dumps(new_spikes))
    if re.search(r'Traceback \(most recent call last\):|RuntimeError:|OutOfMemoryError:|Nonfinite phi gradient', log):
        alerts.append('Training log contains an error')
    running = result['job_status'] == 'RUNNING'
    if running and not result['identity_live']:
        alerts.append('Running job has no matching live process')
    if result['job_status'] not in ['RUNNING', 'PASS']:
        alerts.append('Training job is terminal without PASS')
    if result['job_status'] == 'PASS' and n != len(config['episode_K']):
        alerts.append('PASS job has not committed the full curriculum')
    if running:
        for name, state in result['controllers'].items():
            if state['status'] != 'RUNNING' or not state['identity_live']:
                alerts.append('Controller needs review: ' + name)
        event_time = dt.datetime.fromisoformat(result['last_event']['utc'])
        if (dt.datetime.fromisoformat(result['observed_at_utc']) - event_time).total_seconds() > 1200:
            alerts.append('No event recorded within 20 minutes; inspect same process')
        if previous and steps <= previous['inner_updates']:
            alerts.append('No committed update since previous observation')
    if result['storage_GiB']['/root/autodl-tmp']['free'] < 2 or result['storage_GiB']['/']['free'] < 1:
        alerts.append('Disk space needs review')
    return {'status': 'REQUIRES_AGENT_REVIEW' if alerts else
            ('L_COMPLETE_REQUIRES_PIPELINE_REVIEW' if not running else 'RUNNING_REVIEWED'),
            'alerts': alerts, 'inner_updates': steps, 'completed_episodes': n,
            'update_budget': sum(config['episode_K']), 'new_large_preclip_norms': new_spikes,
            'existing_numerical_risk': 'Episode 7 raw gradient spike is unresolved; finite state is not scientific validation.',
            'full_goal_complete': False, 'training_modified_or_restarted': False}


def review(directory, previous=None):
    count = verify_files(directory)
    result = json.loads((directory / 'result.json').read_text())
    config = json.loads((directory / 'config.json').read_text())
    events = []
    partial = 0
    for line in (directory / 'events.jsonl').read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            partial += 1
    result['unparsed_event_lines'] = max(partial, result.get('unparsed_event_lines', 0))
    assessment = assess(result, config, events, (directory / 'job.log').read_text(), previous)
    assessment.update(reviewed_at_utc=utc().isoformat(), source_files_verified=count,
                      observation_utc=result['observed_at_utc'], observation=str(directory.relative_to(ROOT)))
    write_json(directory / 'review_auto.json', assessment, exclusive=True)
    return assessment


def observe():
    label = utc().strftime('%Y%m%d_%H%M%S_utc')
    rel = 'records/training_monitor_auto_' + label
    archive_rel = 'records/supervision_auto_' + label + '.tar.gz'
    code = '''import hashlib,json,pathlib,shutil,subprocess,tarfile
root=pathlib.Path('/root/autodl-tmp/gaosi_lora')
out=root/REL
subprocess.run(['/usr/bin/python','tools/inspect_recompute_v7.py','--out',str(out)],cwd=root,check=True,stdout=subprocess.DEVNULL)
shutil.copyfile(root/'records/logs/phi_mix_O1_L_H2_core_s17_recompute_v7.log',out/'job.log')
sources=sorted(p for p in out.iterdir() if p.is_file())
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
(out/'archive_source_sha256.json').write_text(json.dumps(manifest,indent=2)+'\\n')
archive=root/ARCHIVE
with archive.open('xb') as stream:
 with tarfile.open(fileobj=stream,mode='w:gz') as handle:
  for p in sources+[out/'archive_source_sha256.json']:
   handle.add(p,arcname=str(p.relative_to(root)))
print(json.dumps({'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
'''.replace('REL', repr(rel)).replace('ARCHIVE', repr(archive_rel))
    reply = subprocess.run(SSH + ['/usr/bin/python -c ' + shlex.quote(code)],
                           capture_output=True, text=True, check=True, timeout=180)
    expected = json.loads(reply.stdout.splitlines()[-1])['sha256']
    archive = ROOT / archive_rel
    with archive.open('xb') as handle:
        subprocess.run(SSH + ['cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + archive_rel)],
                       stdout=handle, stderr=subprocess.PIPE, check=True, timeout=180)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('Archive checksum mismatch')
    directory = ROOT / rel
    with tarfile.open(archive, 'r:gz') as handle:
        members = handle.getmembers()
        for member in members:
            target = (ROOT / member.name).resolve()
            if not member.isfile() or not target.is_relative_to(directory) or target.exists():
                raise ValueError('Unexpected or existing evidence destination')
        for member in members:
            target = ROOT / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.extractfile(member) as source, target.open('xb') as destination:
                shutil.copyfileobj(source, destination)
    verify_files(directory)
    return directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prior-state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-checks', type=int, default=48)
    args = parser.parse_args()
    if not 1 <= args.max_checks <= 48:
        raise ValueError('Recorder is bounded to 1..48 observations')
    args.out.mkdir(parents=True, exist_ok=False)
    with (ROOT / 'records/supervise_recompute_v7.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def save(status, **extra):
            write_json(args.out / 'state.json', {'status': status, 'pid': os.getpid(),
                       'proc_start_ticks': Path('/proc/self/stat').read_text().split()[21],
                       'updated_at_utc': utc().isoformat(), 'training_changes': False, **extra})
        try:
            previous = None
            for index in range(args.max_checks):
                if index == 0:
                    state = json.loads(args.prior_state.read_text())
                    save('ADOPTING_EXISTING_SCHEDULE_NO_REMOTE_REQUEST', prior_state=str(args.prior_state),
                         next_observation_due_utc=state['due_at_utc'])
                    while state['status'] in ['WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', 'INSPECTING']:
                        if utc() > dt.datetime.fromisoformat(state['due_at_utc']) + dt.timedelta(minutes=15):
                            raise RuntimeError('Prior observation overdue; inspect same process without restarting training')
                        if not identity(state['pid'], state['proc_start_ticks']):
                            # Re-read after checking identity: the writer may have just completed.
                            state = json.loads(args.prior_state.read_text())
                            if state['status'] in ['WAITING_UNTIL_DUE_NO_REMOTE_CONNECTION_YET', 'INSPECTING']:
                                raise RuntimeError('Prior observation process no longer matches; inspect evidence')
                            break
                        time.sleep(30)
                        state = json.loads(args.prior_state.read_text())
                    if state['status'] != 'OBSERVATION_DOWNLOADED_REQUIRES_REVIEW':
                        raise RuntimeError('Prior observation needs manual recovery: ' + state['status'])
                    directory = ROOT / state['remote_observation']
                    if hashlib.sha256((ROOT / state['archive']).read_bytes()).hexdigest() != state['archive_sha256']:
                        raise ValueError('Adopted archive checksum mismatch')
                else:
                    directory = observe()
                previous = review(directory, previous)
                with (args.out / 'observations.jsonl').open('a') as handle:
                    handle.write(json.dumps(previous) + '\n')
                if previous['status'] != 'RUNNING_REVIEWED':
                    save(previous['status'], last_review=previous, observations=index + 1)
                    return
                due = dt.datetime.fromisoformat(previous['observation_utc']) + dt.timedelta(minutes=30)
                save('WAITING_30_MINUTES_NO_REMOTE_REQUEST', next_observation_due_utc=due.isoformat(),
                     last_review=previous, observations=index + 1)
                if index + 1 < args.max_checks:
                    while True:
                        remaining = (due - utc()).total_seconds()
                        if remaining <= 0:
                            break
                        time.sleep(min(30, remaining))
            save('OBSERVATION_LIMIT_REACHED_REQUIRES_REVIEW', last_review=previous,
                 observations=args.max_checks)
        except Exception as error:
            save('RECORDER_ERROR_REQUIRES_REVIEW_NO_TRAINING_RESTART',
                 error_type=type(error).__name__, error=str(error))
            raise


if __name__ == '__main__':
    main()
