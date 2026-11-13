"""Recovery-specific finite factor-LR screen; all choices use dev data only."""
import copy
import json
from pathlib import Path

from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 1, 'reason': 'Acquisition-selected B/A=16 degrades ordinary recovery at development steps8/16; tune the recovery baseline before any formal comparison.',
    'ratios': [1., 4., 16.], 'lrs': [3e-5, .0001, .0003], 'K': 64,
    'eval_steps': [8, 16, 32, 64], 'data': 'same dev parent and q_medium candidate as original calibration',
    'selection_step': '3B recovery_K, once frozen by the original standard-Adam calibration',
    'score': 'candidate plus strict_EM minus language residual above .2 nats; ties prefer lower ratio then lower learning rate',
    'reuse': 'preserve original factor16/lr3e-4; factor1/lr3e-4 is exactly the existing paired-Adam update',
    'horizon': 'These fixed Adam-family updates do not use phi time features; recorded K64 intermediate states are valid fixed-budget endpoints.',
    'calibration_and_phi': 'do not change their q, primary K, phi LR or current training; override only formal factor-LR settings',
    'formal_data_used': False,
}


def main():
    preserve_json('records/factor_recovery_selection_policy_v1.json', POLICY)
    source = json.loads(Path('configs/recovery_dev_factor_medium_s17.json').read_text())
    if (source['phase'] != 'development' or source['method'] != 'factor_lr'
            or source['ba_ratio'] != 16. or source['inner_lr'] != .0003 or source['K'] != 64
            or source['data'] != 'data/synthetic/dev_parent.jsonl'):
        raise ValueError('Unexpected original factor-LR development configuration')
    planfile = 'records/factor_recovery_selection_plan_v1.json'
    plan = {'status': 'DEVELOPMENT_PLAN', 'policy': POLICY,
            'allowed_data': [source['data']], 'allowed_parents': [source['parent']],
            'lr_candidates': POLICY['lrs'], 'K_candidates': [64],
            'q_candidates': [source['q_apply']], 'noise_schemes': ['subspace'], 'formal_data_used': False}
    preserve_json(planfile, plan)
    original = wait_for('runs/recovery_dev_factor_medium_s17/result.json', 'recovery_dev_factor_medium_s17')
    paired = json.loads(Path('runs/recovery_dev_paired_medium_s17/result.json').read_text())
    if original['config'] != source or original['status'] != paired['status'] or original['status'] != 'PASS':
        raise ValueError('Unexpected preserved original baseline')
    equivalent = dict(source, method='paired_adam', out=paired['config']['out'])
    equivalent.pop('ba_ratio')
    if paired['config'] != equivalent:
        raise ValueError('Paired-Adam reuse differs from factor1 beyond its name/method')
    results = {(16., .0003): original, (1., .0003): paired}
    for ratio in POLICY['ratios']:
        for lr in POLICY['lrs']:
            if (ratio, lr) in results:
                continue
            c = copy.deepcopy(source)
            name = f'recovery_dev_factor_ratio{ratio:g}_lr{lr:g}_s17_v1'
            c.update(out='runs/' + name, freeze_manifest=planfile, ba_ratio=ratio, inner_lr=lr,
                     initial_adapter='runs/recovery_dev_paired_medium_s17/initial.pt')
            configfile = 'configs/' + name + '.json'
            preserve_json(configfile, c)
            result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.recover', '--config', configfile],
                             Path(c['out']) / 'result.json')
            if result['config'] != c:
                raise ValueError('Preserved factor recovery configuration differs')
            results[(ratio, lr)] = result
    calibration = wait_for('records/calibration_frozen.json', 'calibration_queue_v3')
    if calibration['q_medium'] != source['q_apply'] or calibration['development_parent'] != source['parent']:
        raise ValueError('Factor recovery screen differs from frozen parent/dose')
    K = calibration['recovery_K']
    trials = []
    for (ratio, lr), result in results.items():
        events = [json.loads(line) for line in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
        endpoint = next(event['metrics'] for event in events if event['event'] == 'evaluation' and event['step'] == K)
        residual = endpoint['language']['nll'] - result['clean']['language']['nll']
        score = endpoint['task']['candidate_accuracy'] + endpoint['task']['strict_EM'] - max(0., residual - .2)
        trials.append({'ratio': ratio, 'lr': lr, 'score': score, 'primary_K': K,
                       'metrics': endpoint, 'language_residual_nats': residual,
                       'run': result['config']['out'], 'reused': (ratio, lr) in [(16., .0003), (1., .0003)]})
    selected = max(trials, key=lambda trial: (trial['score'], -trial['ratio'], -trial['lr']))
    frozen = {'status': 'FACTOR_RECOVERY_FROZEN', 'selected_ratio': selected['ratio'],
              'selected_lr': selected['lr'], 'selection_K': K, 'q_medium': source['q_apply'],
              'parent': source['parent'], 'policy': POLICY, 'trials': trials, 'selected': selected,
              'formal_data_used': False, 'independent_phi_training_seeds': 0}
    preserve_json('records/factor_recovery_frozen_v1.json', frozen)
    print(json.dumps(frozen), flush=True)


if __name__ == '__main__':
    main()
