"""Preserve supplementary H1 comparison runtime at the existing scheduled observation cadence.

Writes a new evidence archive only. Does not import Torch, acquire the GPU lock,
start jobs, change policies, choose outcomes or grant scientific approval.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tarfile

ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path('records/horizon_effect_comparison_20261103_1005')
DECLARATIONS = [str(HERE.relative_to('records') / name) for name in
                ['inputs.json', 'policy.json', 'declared_pairs.json', 'plan.json', 'comparison.json',
                 'launch_20261106_0800.json', 'launch_verification_20261106_0803.json']]
CONTROLLER = 'phi_horizon_effect_comparison_s17_v1'



def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(job):
    proc = Path('/proc') / str(job.get('child_pid'))
    try:
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        return {'matches': fields[0] not in ('Z', 'X') and fields[19] == job['child_proc_start_ticks'],
                'pid': job['child_pid'], 'start_ticks': fields[19], 'state': fields[0],
                'cmdline': (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(),
                'blocking': [v.decode() for v in (proc / 'environ').read_bytes().split(b'\0')
                             if v.startswith(b'MUSA_LAUNCH_BLOCKING=')]}
    except (FileNotFoundError, ProcessLookupError):
        return {'matches': False, 'pid': job.get('child_pid'), 'state': 'ABSENT'}


def all_finite(value):
    if isinstance(value, dict):
        return all(all_finite(v) for v in value.values())
    if isinstance(value, list):
        return all(all_finite(v) for v in value)
    return not isinstance(value, float) or math.isfinite(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    anchor = Path(args.observation)
    assert anchor.parent == Path('records') and re.fullmatch(r'pipeline_monitor_\d{8}_\d{6}_utc', anchor.name)
    observed = json.loads((anchor / 'result.json').read_text())
    now = dt.datetime.now(dt.timezone.utc)
    age = (now - dt.datetime.fromisoformat(observed['observed_at_utc'])).total_seconds()
    assert 0 <= age <= 600, 'Only supplement a new scheduled sample, never poll on a faster independent cadence'
    assert not observed['alerts']
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    out = Path('records') / anchor.name.replace('pipeline_monitor_', 'horizon_effect_monitor_')
    out.mkdir(exist_ok=False)
    files = {Path(__file__).resolve().relative_to(ROOT), anchor / 'result.json'}
    files.update(Path('records') / name for name in DECLARATIONS if (Path('records') / name).exists())
    launch = json.loads((HERE / 'launch_20261106_0800.json').read_text())
    assert sha(HERE / 'compare.py') == launch['comparison_source_sha256']
    controllerpath = Path('records/jobs') / (CONTROLLER + '.json')
    controller = json.loads(controllerpath.read_text())
    controller_identity = identity(controller)
    files.update([controllerpath, Path('records/logs') / (CONTROLLER + '.log'),
                  HERE / 'compare.py', Path('records/pipeline_observed_blocking_v8/build_manifest.json')])
    for p in HERE.glob('phi_horizon_*_paired_review.json'):
        files.add(p)
    jobs = []
    alerts = []
    if controller['status'] == 'RUNNING':
        if (not controller_identity['matches'] or controller_identity['pid'] != launch['controller_identity']['pid']
                or controller_identity['start_ticks'] != launch['controller_identity']['start_ticks']):
            alerts.append(CONTROLLER + ': controller identity differs')
    elif controller['status'] != 'PASS' or not (HERE / 'comparison.json').exists():
        alerts.append(CONTROLLER + ': unsuccessful or incomplete endpoint')
    declared = {Path(p['H1']['out']).name: p['H1']
                for p in json.loads((HERE / 'declared_pairs.json').read_text())}
    assert len(declared) == 10
    paths = [Path('records/jobs') / (name + '.json') for name in declared]
    for jobpath in paths:
        if not jobpath.exists():
            continue
        job = json.loads(jobpath.read_text())
        assert re.fullmatch(r'phi_horizon_L_H1_2560_t[0-4]_(?:clean|medium)_s17_v1', job['name'])
        run = Path('runs') / job['name']
        configpath = Path('configs') / (job['name'] + '.json')
        config = json.loads(configpath.read_text())
        assert config == declared[job['name']]
        assert config['phase'] == 'phi_development' and config['out'] == str(run)
        assert config['K'] == 64 and config['data_seed'] == 17 and config['noise_seed'] == 1729
        files.update([jobpath, configpath])
        paths = [run / n for n in ['config.json', 'events.jsonl', 'result.json', 'source_sha256.json']]
        paths += sorted((run / 'source').glob('*.py'))
        paths += [Path('records/logs') / (job['name'] + '.log')]
        files.update(p for p in paths if p.exists())
        row = {'name': job['name'], 'job': job, 'identity': identity(job),
               'config': str(configpath), 'run': str(run)}
        if job['status'] == 'RUNNING':
            if not row['identity']['matches']:
                alerts.append(job['name'] + ': RUNNING identity absent')
            elif row['identity']['blocking'] != ['MUSA_LAUNCH_BLOCKING=1']:
                alerts.append(job['name'] + ': blocking environment absent')
        elif job['status'] != 'PASS':
            alerts.append(job['name'] + ': job ended ' + job['status'])
        jobs.append(row)
    # Copy first, then parse the preserved event bytes. A trailing partial record is disclosed.
    for source in sorted(files):
        dest = out / 'snapshot' / source
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    for row in jobs:
        run = out / 'snapshot' / row['run']
        saved_config = run / 'config.json'
        if saved_config.exists():
            assert json.loads(saved_config.read_text()) == declared[row['name']]
        source_manifest = run / 'source_sha256.json'
        if source_manifest.exists():
            build = json.loads((out / 'snapshot/records/pipeline_observed_blocking_v8/build_manifest.json').read_text())
            expected_sources = {Path(k).name: v for k, v in build['numerical_source_sha256'].items()}
            assert json.loads(source_manifest.read_text()) == expected_sources
            for filename, expected in expected_sources.items():
                assert sha(run / 'source' / filename) == expected
        events_path = run / 'events.jsonl'
        raw = events_path.read_bytes() if events_path.exists() else b''
        lines = raw.splitlines()
        row['trailing_partial_event_line_excluded'] = bool(raw and not raw.endswith(b'\n'))
        if row['trailing_partial_event_line_excluded']:
            lines = lines[:-1]
        events = [json.loads(line) for line in lines]
        assert all(all_finite(event) for event in events)
        updates = [e for e in events if e['event'] == 'recover']
        assert [e['step'] for e in updates] == list(range(1, len(updates) + 1))
        assert all(set(e['route_tokens']) == {'task', 'language', 'keep'} for e in updates)
        assert all(v >= declared[row['name']]['effective_tokens'] for e in updates for v in e['route_tokens'].values())
        row['minimum_effective_tokens'] = {part: min(e['route_tokens'][part] for e in updates)
                for part in ['task', 'language', 'keep']} if updates else {}
        row['recorded_recovery_steps'] = len(updates)
        row['evaluation_steps'] = [e['step'] for e in events if e['event'] == 'evaluation']
        row['last_event'] = {k: events[-1][k] for k in ['event', 'utc', 'step'] if k in events[-1]} if events else None
        result_path = run / 'result.json'
        if result_path.exists():
            result = json.loads(result_path.read_text())
            assert all_finite(result) and result['status'] == 'PASS'
            assert result['config'] == json.loads((out / 'snapshot' / row['config']).read_text())
            assert result['base_unchanged'] and result['phi_frozen']
            assert result['coverage'] == result['pool']
            assert result['keep_coverage']['covered'] == result['keep_coverage']['pool']
            assert row['recorded_recovery_steps'] == 64 and row['evaluation_steps'] == [0, 8, 16, 32, 64]
            row['result_present_and_text_checks_pass'] = True
        else:
            row['result_present_and_text_checks_pass'] = False
            if row['job']['status'] == 'PASS':
                alerts.append(row['name'] + ': PASS job has no preserved result')
    result = {'status': 'HORIZON_COMPARISON_RUNTIME_TEXT_EVIDENCE_ONLY',
        'observed_at_utc': now.isoformat(), 'anchor': str(anchor),
        'anchor_result_sha256': sha(anchor / 'result.json'), 'anchor_age_seconds': age,
        'collector_sha256': sha(Path(__file__)), 'jobs': jobs,
        'comparison_controller': controller, 'comparison_controller_identity': controller_identity,
        'pass_jobs': sum(j['job']['status'] == 'PASS' for j in jobs),
        'running_jobs': sum(j['job']['status'] == 'RUNNING' for j in jobs),
        'declarations_present': [n for n in DECLARATIONS if (out / 'snapshot/records' / n).exists()],
        'alerts': alerts, 'gpu_lock_inode': 31148027670,
        'training_modified_or_restarted': False, 'method_validated': False, 'full_goal_complete': False,
        'limitations': ['Text logs can precede checkpoint commits; no committed tensor step is inferred.',
                        'Files can reflect adjacent instants; no atomic snapshot is claimed.',
                        'No tensor audit, checkpoint finiteness, derivative or efficacy claim.']}
    (out / 'result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    sources = sorted(p for p in out.rglob('*') if p.is_file())
    manifest = out / 'archive_source_sha256.json'
    manifest.write_text(json.dumps({str(p): sha(p) for p in sources}, indent=2) + '\n')
    archive = Path(str(out) + '.tar.gz')
    with archive.open('xb') as handle:
        with tarfile.open(fileobj=handle, mode='w:gz') as tar:
            for path in sources + [manifest]:
                tar.add(path, arcname=str(path))
    print(json.dumps({'archive': str(archive), 'sha256': sha(archive),
                     'files': len(sources), 'result': result}))


if __name__ == '__main__':
    main()
