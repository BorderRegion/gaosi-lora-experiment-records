"""Finite P04 queue after valid learned parents; dev-only choice of q and endpoint."""
import copy
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import time

from storage import preserve_space


def execute(name, config=None, command=None):
    for event in preserve_space():
        print(json.dumps({'storage': event}), flush=True)
    jobfile = Path('records/jobs') / (name + '.json')
    if config is not None:
        config = copy.deepcopy(config)
        config['out'] = 'runs/' + name
        configfile = Path('configs') / (name + '.json')
        configfile.write_text(json.dumps(config, indent=2))
        resultfile = Path(config['out']) / 'result.json'
        if resultfile.exists():
            result = json.loads(resultfile.read_text())
            assert result['config'] == config
            return result
        command = ['/usr/bin/python', '-u', '-m', 'gaosi.recover', '--config', str(configfile)]
    else:
        resultfile = Path('runs') / name / 'result.json'
        if resultfile.exists():
            return json.loads(resultfile.read_text())
    if jobfile.exists():
        raise RuntimeError(f'Incomplete prior calibration job {name}; inspect before continuing')
    with Path(f'records/logs/{name}.log').open('ab', buffering=0) as log:
        code = subprocess.call(['/usr/bin/python', '-u', 'tools/job.py', '--name', name,
                                '--', *command], stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL)
    if code:
        raise RuntimeError(f'Calibration job {name} failed {code}; no silent retry')
    return json.loads(resultfile.read_text())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--noise-plan')
    p.add_argument('--window-name', default='noise_calibration_dev_s17')
    args = p.parse_args()
    parentfile = Path('records/parents_ready.json')
    while not parentfile.exists():
        dispatcher = json.loads(Path('records/jobs/development_queue_v4.json').read_text())
        if dispatcher['status'] != 'RUNNING':
            raise RuntimeError('Development ended without valid parents; inspect before noise/phi')
        time.sleep(30)
    parents = json.loads(parentfile.read_text())
    dev = next(p for p in parents if p['config']['data'] == 'data/synthetic/dev_parent.jsonl')
    assert all(p['acquisition_gate_passed'] for p in parents)
    parent = dev['checkpoint']
    name = args.window_name
    command = ['/usr/bin/python', '-u', 'tools/calibrate_noise.py',
               '--parent', parent, '--out', 'runs/' + name]
    if args.noise_plan:
        command += ['--plan', args.noise_plan]
    window = execute(name, command=command)
    if window['status'] != 'CANDIDATE_WINDOW':
        raise RuntimeError('No measurable noise window; inspect dose diagnostics before formal phi')
    qlight, qmedium = window['light_q'], window['medium_q']
    selection = json.loads(Path('records/development_frozen.json').read_text())
    plan = {'status': 'DEVELOPMENT_PLAN', 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'allowed_data': [dev['config']['data']], 'allowed_parents': [parent],
            'lr_candidates': [3e-5, .0001, .0003], 'K_candidates': [64],
            'q_candidates': [0., qlight, qmedium], 'noise_schemes': ['subspace', 'shrink', 'uniform', 'factor'],
            'choice_rule': 'medium dose final candidate accuracy plus strict EM; choose earliest declared step within 1pp of final under language residual <= .2 nats. Light and clean validate selected settings.',
            'no_formal_data': True, 'window': 'runs/' + name + '/result.json'}
    planfile = Path('records/recovery_development_plan.json')
    planfile.write_text(json.dumps(plan, indent=2))
    template = {'out': '', 'freeze_manifest': str(planfile), 'phase': 'development',
                'data_seed': 17, 'noise_seed': 1729, 'parent': parent, 'device': 'musa',
                'q_apply': qmedium, 'noise_scheme': 'subspace', 'architecture': 'L',
                'method': 'standard_adam', 'data': dev['config']['data'], 'target_ids': [],
                'task_layout': 'all', 'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
                'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': selection['lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': 64, 'eval_steps': [8, 16, 32, 64]}
    trials = {}
    for rate in plan['lr_candidates']:
        c = dict(template, inner_lr=rate)
        trials[rate] = execute(f'recovery_dev_medium_lr{rate:g}_s17', c)
    def score(result):
        final = result['final']
        residual = final['language']['nll'] - result['clean']['language']['nll']
        return final['task']['candidate_accuracy'] + final['task']['strict_EM'] - max(0., residual - .2)
    rate = max(trials, key=lambda r: (score(trials[r]), -r))
    selected = trials[rate]
    clean_accuracy = window['curves'][0]['qa']['candidate_accuracy']
    if selected['final']['task']['candidate_accuracy'] < clean_accuracy - .1:
        raise RuntimeError('Selected dose has not passed ordinary full-recovery validation; no formal freeze')
    events = [json.loads(x) for x in (Path(selected['config']['out']) / 'events.jsonl').read_text().splitlines()]
    target = selected['final']['task']
    candidates = [e for e in events if e['event'] == 'evaluation' and e['step'] in template['eval_steps']
                  and e['metrics']['task']['candidate_accuracy'] >= target['candidate_accuracy'] - .01
                  and e['metrics']['task']['strict_EM'] >= target['strict_EM'] - .01
                  and e['language_residual_nats'] <= .2]
    if not candidates:
        raise RuntimeError('Ordinary full recovery does not meet the dev stopping rule')
    K = min(e['step'] for e in candidates)
    validations = {}
    for label, q in [('clean', 0.), ('light', qlight)]:
        validations[label] = execute(f'recovery_dev_{label}_lr{rate:g}_s17', dict(template, inner_lr=rate, q_apply=q))
    validations['paired_medium'] = execute('recovery_dev_paired_medium_s17', dict(template, inner_lr=rate, method='paired_adam'))
    factor_candidates = []
    # All eligible runs used the same 128-step dev fact budget. No held-out
    # knowledge or recovery-test scores choose the fixed A/B ratio.
    for item in Path('runs').glob('*/result.json'):
        result = json.loads(item.read_text())
        c = result.get('config', {})
        if (c.get('data') != 'data/synthetic/dev_parent.jsonl' or c.get('rank') != selection['rank']
                or c.get('steps') != 128 or c.get('targets') != 'all7' or c.get('scale') != 1.
                or c.get('lr') != selection['lr'] or c.get('ba_ratio') not in [1., 4., 16.]):
            continue
        data = [json.loads(x) for x in (item.parent / 'events.jsonl').read_text().splitlines()]
        metric = [x for x in data if x['event'] == 'evaluation'][-1]
        factor_candidates.append((metric['candidate_accuracy'] + metric['strict_EM'], -c['ba_ratio'], c['ba_ratio']))
    if not factor_candidates:
        raise RuntimeError('Matched factor-LR development baselines are missing')
    ratio = max(factor_candidates)[2]
    validations['factor_medium'] = execute('recovery_dev_factor_medium_s17', dict(template, inner_lr=rate, method='factor_lr', ba_ratio=ratio))
    for scheme in ['shrink', 'uniform', 'factor']:
        validations[scheme] = execute(f'recovery_dev_{scheme}_s17', dict(template, inner_lr=rate, noise_scheme=scheme))
    events = [json.loads(x) for x in (Path(selected['config']['out']) / 'events.jsonl').read_text().splitlines()]
    norms = [e['update_norm'] for e in events if e['event'] == 'recover']
    normfile = Path('records/recovery_dev_standard_update_norms.json')
    normfile.write_text(json.dumps(norms))
    validations['fixed_update_norm_shrink'] = execute('recovery_dev_shrink_fixednorm_s17',
                                                     dict(template, inner_lr=rate, noise_scheme='shrink', match_update_norms=str(normfile)))
    frozen = {'status': 'CALIBRATION_FROZEN', 'q_light': qlight, 'q_medium': qmedium,
              'recovery_lr': rate, 'capacity_lr': selection['lr'], 'rank': selection['rank'],
              'targets': selection['targets'], 'scale': selection['scale'], 'recovery_K': K,
              'factor_ba_ratio': ratio,
              'stopping_rule_frozen': True, 'ordinary_recovery_validated': True,
              'development_parent': parent, 'development_plan': str(planfile),
              'world_manifest': json.loads(Path('records/development_plan_v4.json').read_text())['world_manifest'],
              'trials': {str(r): v['config']['out'] for r, v in trials.items()},
              'controls': {k: v['config']['out'] for k, v in validations.items()},
              'formal_data_used': False,
              'next': 'Real three-route H2 and endpoint meta-optimizer profile before full phi schedules; this is not phi freezing.'}
    Path('records/calibration_frozen.json').write_text(json.dumps(frozen, indent=2))
    print(json.dumps(frozen), flush=True)


if __name__ == '__main__':
    main()
