"""New pipeline leaf routing and evidence-bound review; no automatic promotion."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time

from device_guard import uses_musa
from storage import preserve_space
from train_guarded_meta import digest, prepare, LOCK

SNAPSHOT = Path('records/pipeline_observed_blocking_v8')
ADOPTED = 'phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
CONFIG_SHA = 'e09faf76b672452e3b3d3000bcff1956d04117bb5e66dab6a89059b77cf25685'


def meta_entry(command):
    if '-m' not in command or '--config' not in command:
        return None
    module = command[command.index('-m') + 1]
    config = json.loads(Path(command[command.index('--config') + 1]).read_text())
    if module == 'gaosi.meta':
        return 'meta'
    if module == 'gaosi.scale' and config.get('scale_action') == 'meta':
        return 'scale'
    if module == 'gaosi.confirmation' and config.get('confirmation_action') == 'meta':
        return 'confirmation'
    return None


def verify_review(run, receipt_path):
    receipt = json.loads(Path(receipt_path).read_text())
    if (receipt.get('status') != 'ALLOW_NEXT_PROTOCOL_STAGE_WITH_LIMITATIONS'
            or Path(receipt.get('run', '')).resolve() != Path(run).resolve()
            or not receipt.get('reviewer') or not receipt.get('reviewed_at_utc')
            or not receipt.get('rationale') or not receipt.get('scientific_evidence_sha256')
            or not isinstance(receipt.get('limitations'), list)):
        raise ValueError('Scientific review is absent, incomplete, or belongs to another run')
    audit_path = Path(receipt['artifact_audit'])
    if digest(audit_path) != receipt['artifact_audit_sha256']:
        raise ValueError('Completed artifact audit changed')
    audit = json.loads(audit_path.read_text())
    if (audit['status'] != 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
            or Path(audit['run']).resolve() != Path(run).resolve()):
        raise ValueError('Require a complete artifact audit of this run')
    for group in [audit['evidence_sha256'], receipt['scientific_evidence_sha256']]:
        for name, expected in group.items():
            if digest(name) != expected:
                raise ValueError('Reviewed evidence changed: ' + name)
    return receipt


def wait_for_review(run):
    receipt = SNAPSHOT / 'reviews' / (Path(run).name + '.json')
    announced = False
    while not receipt.exists():
        if not announced:
            print(json.dumps({'event': 'completed_meta_waiting_for_agent_scientific_review',
                              'run': str(run), 'receipt': str(receipt),
                              'training_restarted': False}), flush=True)
            announced = True
        time.sleep(1800)
    return verify_review(run, receipt)


def run_job(name, command, resultfile):
    resultfile = Path(resultfile)
    jobfile = Path('records/jobs') / (name + '.json')
    entry = meta_entry(command)
    if not resultfile.exists():
        if jobfile.exists():
            raise RuntimeError('Preserved incomplete job; inspect rather than resume: ' + name)
        for event in preserve_space():
            print(json.dumps({'storage': event}), flush=True)
        numerical = uses_musa(command) or entry is not None
        if entry:
            configfile = command[command.index('--config') + 1]
            manifest_path = SNAPSHOT / 'meta_entries' / (name + '.json')
            prepare(configfile, entry, manifest_path)
            command = ['/usr/bin/python', '-u', 'tools/train_guarded_meta.py',
                       'run', '--manifest', str(manifest_path)]
        if numerical:
            command = ['/usr/bin/env', 'MUSA_LAUNCH_BLOCKING=1', *command]
        wrapper = ['/usr/bin/python', '-u', 'tools/job.py', '--name', name]
        if numerical:
            wrapper += ['--musa-lock', LOCK]
        wrapper += ['--', *command]
        with Path('records/logs', name + '.log').open('xb', buffering=0) as log:
            code = subprocess.call(wrapper, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL)
        if code:
            raise RuntimeError(f'{name} failed {code}; preserve all evidence')
    result = json.loads(resultfile.read_text())
    if result['status'] != 'PASS':
        raise RuntimeError('Preserved unsuccessful result: ' + name)
    if entry:
        state = json.loads(jobfile.read_text())
        if state['status'] != 'PASS':
            raise RuntimeError('Meta wrapper has not completed successfully')
        wait_for_review(resultfile.parent)
    return result


def adopt_l():
    """Adopt the existing L exactly once after its completed evidence is reviewed."""
    configfile = Path('configs', ADOPTED + '.json')
    jobfile = Path('records/jobs', ADOPTED + '.json')
    while True:
        if digest(configfile) != CONFIG_SHA:
            raise RuntimeError('Adopted L configuration changed')
        state = json.loads(jobfile.read_text())
        if state['status'] == 'PASS':
            run = Path('runs') / ADOPTED
            result = json.loads((run / 'result.json').read_text())
            if (result['status'] != 'PASS' or result['episodes'] != 192
                    or result['inner_updates'] != 2560 or result['horizon'] != 'EXACT_H2_tail'
                    or Path(result['checkpoint']) != run / 'latest.pt'):
                raise RuntimeError('Adopted L differs from the full observed v8 schedule')
            wait_for_review(run)
            print(json.dumps({'event': 'adopted_reviewed_existing_L', 'job': ADOPTED,
                              'credited_inner_updates': 2560,
                              'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()}), flush=True)
            return result
        if state['status'] != 'RUNNING':
            raise RuntimeError('Adopted L failed; inspect preserved evidence')
        proc = Path('/proc') / str(state.get('child_pid'))
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            valid = fields[0] != 'Z' and fields[19] == state.get('child_proc_start_ticks')
        except FileNotFoundError:
            valid = False
        if not valid:
            raise RuntimeError('Adopted L process identity is absent')
        time.sleep(1800)
