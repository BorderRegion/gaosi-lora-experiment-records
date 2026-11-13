"""Read-only training inspection; save immutable observation evidence separately."""
import argparse
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

import torch


def identity(state):
    proc = Path(f"/proc/{state.get('child_pid')}/stat")
    fields = proc.read_text().split() if proc.exists() else []
    return bool(fields and fields[2] != 'Z' and
                fields[21] == state.get('child_proc_start_ticks'))


def finite(value):
    if torch.is_tensor(value):
        return bool(torch.isfinite(value).all())
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(v) for v in value)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(Path('records/pipeline_recompute_v7/build_manifest.json').read_text())
    name = manifest['adopted_L_job']
    run = Path('runs') / name
    config_raw = (Path('configs') / (name + '.json')).read_bytes()
    state_raw = (Path('records/jobs') / (name + '.json')).read_bytes()
    events_raw = (run / 'events.jsonl').read_bytes()
    checkpoint_raw = (run / 'latest.pt').read_bytes()
    for file, raw in [('job.json', state_raw), ('config.json', config_raw),
                      ('events.jsonl', events_raw), ('checkpoint.pt', checkpoint_raw)]:
        (out / file).write_bytes(raw)
    state = json.loads(state_raw)
    events, partial = [], 0
    for line in events_raw.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            partial += 1
    checkpoint = torch.load(io.BytesIO(checkpoint_raw), map_location='cpu', weights_only=False)
    ends = [e for e in events if e.get('event') == 'episode_end']
    controllers = {}
    for job in manifest['jobs']:
        file = Path('records/jobs') / (job['name'] + '.json')
        s = json.loads(file.read_text())
        controllers[job['name']] = {'status': s['status'], 'identity_live': identity(s),
                                     'child_pid': s.get('child_pid')}
    gpu_command = next((shutil.which(n) for n in ['mthreads-gmi', 'mthreads-smi', 'musa-smi']
                        if shutil.which(n)), None)
    gpu = subprocess.run([gpu_command], capture_output=True, text=True, timeout=20) if gpu_command else None
    report = {
        'observed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'job': name, 'job_status': state['status'], 'identity_live': identity(state),
        'pid': state.get('child_pid'), 'pid_start_ticks': state.get('child_proc_start_ticks'),
        'config_sha256': hashlib.sha256(config_raw).hexdigest(),
        'config_sha_matches_manifest': hashlib.sha256(config_raw).hexdigest() == manifest['adopted_L_config_sha256'],
        'checkpoint_sha256': hashlib.sha256(checkpoint_raw).hexdigest(),
        'checkpoint_config_matches': checkpoint['config'] == json.loads(config_raw),
        'checkpoint_phi_and_optimizer_finite': finite(checkpoint['phi']) and finite(checkpoint['meta_optimizer']),
        'completed_episodes': checkpoint['next_episode'], 'inner_updates': checkpoint['inner_updates'],
        'optimizer_steps': [float(v['step']) for v in checkpoint['meta_optimizer']['state'].values() if 'step' in v],
        'latest_episode_end': ends[-1] if ends else None, 'last_event': events[-1] if events else None,
        'large_preclip_norms': [{'episode': e['episode'], 'norm': e['phi_grad_norm']}
                                for e in ends if e.get('phi_grad_norm', 0) > 1e6],
        'unparsed_event_lines': partial, 'controllers': controllers,
        'storage_GiB': {p: {'free': round(shutil.disk_usage(p).free/2**30, 2),
                              'total': round(shutil.disk_usage(p).total/2**30, 2)}
                        for p in ['/root/autodl-tmp', '/']},
        'mount': subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE,TARGET,FSTYPE', '/root/autodl-tmp'], text=True).strip(),
        'pid1_start': subprocess.check_output(['ps', '-p', '1', '-o', 'lstart='], text=True).strip(),
        'gpu_command': gpu_command, 'gpu_returncode': gpu.returncode if gpu else None,
        'gpu_output': gpu.stdout if gpu else None,
        'numerical_scope': 'Finite state and progress only; earlier gradient spike remains unresolved.'}
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
