"""Finish early 7B acquisition and ordinary recovery regardless of 3B phi scores."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from device_guard import live_identity
from phi_core_queue import run_job


OLD = 'runs/capacity_7b_early_all7_r32_lr1e-4_s17_v2'
PARENT_NAME = 'parent_7b_dev_lr1e-4_s17_continue512_v1'
PLAN = Path('records/seven_b_early_plan_v1.json')
RESULT = Path('records/seven_b_early_ready_v1.json')


def immutable_json(path, data):
    path = Path(path)
    if path.exists():
        if json.loads(path.read_text()) != data:
            raise ValueError(f'Preserved configuration differs: {path}')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + '\n')


def execute(name, config, module):
    config = copy.deepcopy(config)
    config['out'] = 'runs/' + name
    file = Path('configs') / (name + '.json')
    immutable_json(file, config)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', module, '--config', str(file)],
                     Path(config['out']) / 'result.json')
    if result['config'] != config:
        raise ValueError(f'Result configuration differs: {name}')
    return result


def main():
    old = json.loads((Path(OLD) / 'result.json').read_text())
    if old['config']['base_revision'] != 'd149729398750b98c0af14eb82c78cfe92750796':
        raise ValueError('Unexpected 7B revision')
    if old['config']['data'] != 'data/synthetic/dev_parent.jsonl' or old['steps'] != 128:
        raise ValueError('Unexpected early development checkpoint')
    # Continuing the cursor is only meaningful with the same binding sampler.
    before = (Path(OLD) / 'source/data.py').read_bytes()
    current = Path('gaosi/data.py').read_bytes()
    if before != current:
        raise ValueError('Early 7B sampler changed; declare a new development branch')
    plan = {'status': 'EARLY_7B_PLAN', 'source': OLD, 'data_source_sha256': hashlib.sha256(current).hexdigest(),
            'depends_on_3b_phi_success': False, 'ordinary_acquisition': {
                'continue_optimizer_streams_and_rng': True, 'lr': old['config']['lr'],
                'start_step': 128, 'maximum_total_steps': 512, 'full_query_steps': [256, 512],
                'stop_at_full_acquisition_gate': True, 'coverage': 512,
                'if_failed': 'Record ACQUISITION_INCOMPLETE; do not perturb an unlearned parent. A new LR branch needs its own recorded plan.'},
            'ordinary_recovery': {'dose_source': 'records/calibration_frozen.json',
                'q_apply': ['zero', '3b_light', '3b_medium'], 'method': 'standard_adam',
                'K': 64, 'eval_steps': [8, 16, 32, 64],
                'primary_budget': '3B development-frozen recovery_K; 64 is also reported as saturation diagnostic',
                'claim_scope': '7B acquisition and recovery under transferred doses, not matched functional damage or phi effectiveness'},
            'final_data_used': False, 'single_device': 'MUSA leaf lock and verified legacy-process drain'}
    immutable_json(PLAN, plan)
    config = copy.deepcopy(old['config'])
    config.update(continue_from=old['checkpoint'], steps=512, eval_steps=[256, 512],
                  eval_limit=0, stop_on_acquisition=True,
                  purpose='early_7b_ordinary_acquisition_full512_queries_no_phi_dependency')
    parent = execute(PARENT_NAME, config, 'gaosi.train')
    events = [json.loads(line) for line in (Path(parent['config']['out']) / 'events.jsonl').read_text().splitlines()]
    evaluations = [event for event in events if event['event'] == 'evaluation']
    final = evaluations[-1]
    if final['n'] != 512 or len(parent['fact_stream']['covered']) != 512 or not parent['base_unchanged']:
        raise ValueError('Early 7B parent lacks complete query/support validation')
    if not parent['acquisition_gate_passed']:
        RESULT.write_text(json.dumps({'status': 'ACQUISITION_INCOMPLETE', 'parent': parent['checkpoint'],
                                     'evaluation': final, 'recovery_started': False,
                                     'next': plan['ordinary_acquisition']['if_failed']}, indent=2))
        return
    print(json.dumps({'event': 'seven_b_parent_acquired', 'checkpoint': parent['checkpoint'],
                      'evaluation': final}), flush=True)
    calibration_file = Path('records/calibration_frozen.json')
    while not calibration_file.exists():
        job = json.loads(Path('records/jobs/calibration_queue_v3.json').read_text())
        if job['status'] != 'RUNNING':
            raise RuntimeError('Calibration ended without frozen settings')
        if not live_identity(job.get('child_pid'), job.get('child_proc_start_ticks')):
            raise RuntimeError('Calibration process is absent; inspect without restarting it')
        time.sleep(30)
    cal = json.loads(calibration_file.read_text())
    if cal['status'] != 'CALIBRATION_FROZEN' or not cal['ordinary_recovery_validated'] or cal['formal_data_used']:
        raise ValueError('Invalid 3B development calibration')
    recovery_plan = {'status': 'DEVELOPMENT_PLAN', 'allowed_data': [parent['config']['data']],
                     'allowed_parents': [parent['checkpoint']], 'lr_candidates': [cal['recovery_lr']],
                     'K_candidates': [64], 'q_candidates': [0., cal['q_light'], cal['q_medium']],
                     'noise_schemes': ['subspace'], 'no_formal_data': True,
                     'dose_transfer_from': str(calibration_file), 'functional_damage_matched': False,
                     'primary_K': cal['recovery_K'], 'early_plan': str(PLAN)}
    manifest = Path('records/seven_b_early_recovery_plan_v1.json')
    immutable_json(manifest, recovery_plan)
    template = {'out': '', 'freeze_manifest': str(manifest), 'phase': 'development',
                'data_seed': 17, 'noise_seed': 1729, 'parent': parent['checkpoint'], 'device': 'musa',
                'q_apply': 0., 'noise_scheme': 'subspace', 'architecture': 'L', 'method': 'standard_adam',
                'data': parent['config']['data'], 'target_ids': [], 'task_layout': 'all',
                'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
                'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': cal['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': 64, 'eval_steps': [8, 16, 32, 64]}
    results = {}
    for label, q in [('clean', 0.), ('light', cal['q_light']), ('medium', cal['q_medium'])]:
        c = dict(template, q_apply=q)
        result = execute(f'recovery_7b_early_{label}_standard_s17_v1', c, 'gaosi.recover')
        recovery_events = [json.loads(line) for line in
                           (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
        primary = next(event['metrics'] for event in recovery_events
                       if event['event'] == 'evaluation' and event['step'] == cal['recovery_K'])
        results[label] = {'run': result['config']['out'], 'clean': result['clean'],
                          'initial': result['initial'], 'final': result['final'],
                          'primary': primary, 'loss_changes': result['loss_changes'],
                          'primary_K': cal['recovery_K']}
    RESULT.write_text(json.dumps({'status': 'EARLY_7B_ORDINARY_RECOVERY_COMPLETE',
                                 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                                 'parent': parent['checkpoint'], 'parent_evaluation': final,
                                 'results': results, 'functional_damage_matched': False,
                                 'phi_scale_validation_complete': False,
                                 'next': 'Frozen phi 8-world transfer and 512-step local meta adaptation remain required.'}, indent=2))


if __name__ == '__main__':
    main()
