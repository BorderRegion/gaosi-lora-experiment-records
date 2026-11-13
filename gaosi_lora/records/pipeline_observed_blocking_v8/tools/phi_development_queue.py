"""Dev-only endpoint comparisons after full L/X cores; never freezes final phi.

The task sample and score are declared before reading model outcomes. Final
facts remain excluded, and baselines use the same parent/data/noise bytes.
"""
import datetime as dt
import argparse
import hashlib
import json
from pathlib import Path
import time

from calibration_queue import execute
from phi_core_queue import train


POLICY = {
    'version': 1,
    'selection_data': 'development_only',
    'world_selection': 'First two fact and first two rule dev worlds sorted by world_id',
    'q': ['zero', 'medium'],
    'K': 64,
    'eval_steps': [8, 16, 32, 64],
    'score': 'Equal weight known/fact/rule groups; mean candidate/strict_EM minus language and keep NLL residual above .1 nats. Equal weight doses, tie prefers L.',
    'late_improvement': 'If either architecture improves dev score by >.01 from 2048 to 2560, extend BOTH once by 64x16, then reevaluate.',
    'formal_phi_frozen': False,
}


def preserve_json(path, value):
    path = Path(path)
    if path.exists() and json.loads(path.read_text()) != value:
        raise ValueError(f'Existing declaration differs: {path}')
    path.write_text(json.dumps(value, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--core-job', default='phi_core_queue_observed_blocking_v8')
    args = p.parse_args()
    preserve_json('records/phi_selection_policy.json', POLICY)
    while not Path('records/phi_core_ready.json').exists():
        job = Path('records/jobs') / (args.core_job + '.json')
        if job.exists() and json.loads(job.read_text())['status'] != 'RUNNING':
            raise RuntimeError('Core ended without complete L/X and horizon runs')
        time.sleep(30)
    calibration = json.loads(Path('records/calibration_frozen.json').read_text())
    parents = json.loads(Path('records/parents_ready.json').read_text())
    parent = next(p for p in parents if p['config']['data'].endswith('dev_parent.jsonl'))
    worlds = json.loads(Path(calibration['world_manifest']).read_text())['worlds']
    tasks = [('known', parent['config']['data'])]
    for kind, group in [('fact', 'fact'), ('affine_integer', 'rule')]:
        chosen = sorted([w for w in worlds if w['split'] == 'dev' and w['kind'] == kind],
                        key=lambda w: w['world_id'])[:2]
        assert len(chosen) == 2
        tasks += [(group, str(Path('data/synthetic') / w['file'])) for w in chosen]
    phi_paths = {}
    for architecture in ['L', 'X']:
        for steps in [2048, 2560]:
            file = f'runs/phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8/phi_{steps:04d}.pt'
            if not Path(file).exists():
                raise RuntimeError(f'Missing declared learning-curve snapshot {file}')
            phi_paths[f'{architecture}_{steps}'] = file
    plan = {'status': 'PHI_DEVELOPMENT_PLAN', 'policy': POLICY,
            'allowed_data': sorted({file for _, file in tasks}),
            'allowed_parents': [parent['checkpoint']],
            'allowed_phis': {file: hashlib.sha256(Path(file).read_bytes()).hexdigest()
                            for file in phi_paths.values()},
            'lr_candidates': [calibration['recovery_lr']], 'K_candidates': [64],
            'q_candidates': [0., calibration['q_medium']], 'noise_schemes': ['subspace'],
            'tasks': tasks, 'formal_data_used': False}
    planfile = 'records/phi_recovery_development_plan.json'
    # JSON roundtrip makes the tuple/list representation stable across restart.
    preserve_json(planfile, json.loads(json.dumps(plan)))
    template = {'out': '', 'freeze_manifest': planfile, 'phase': 'phi_development',
                'data_seed': 17, 'noise_seed': 1729, 'parent': parent['checkpoint'],
                'device': 'musa', 'q_apply': 0., 'noise_scheme': 'subspace',
                'architecture': 'L', 'method': 'phi', 'data': '', 'target_ids': [],
                'task_layout': 'all', 'retain_subset_fraction': 1., 'subset_seed': 91,
                'D': 'plus', 'retain_data': parent['config']['data'],
                'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': calibration['recovery_lr'], 'effective_tokens': 512,
                'retain_weight': .25, 'K': 64, 'eval_steps': POLICY['eval_steps']}

    def evaluate(label, method, architecture='L', phi=None):
        outcomes = []
        for index, (group, file) in enumerate(tasks):
            for dose, q in [('clean', 0.), ('medium', calibration['q_medium'])]:
                c = dict(template, method=method, architecture=architecture, data=file, q_apply=q)
                if phi is not None:
                    c['phi'] = phi
                result = execute(f'phi_dev_{label}_t{index}_{dose}_s17', c)
                final, clean = result['final'], result['clean']
                task = final['task']
                score = .5 * (task['candidate_accuracy'] + task['strict_EM'])
                score -= max(0., final['language']['nll'] - clean['language']['nll'] - .1)
                score -= max(0., final['keep']['nll'] - clean['keep']['nll'] - .1)
                outcomes.append({'group': group, 'data': file, 'dose': dose,
                                 'run': result['config']['out'], 'score': score})
        groups = {g: [r['score'] for r in outcomes if r['group'] == g]
                  for g in ['known', 'fact', 'rule']}
        score = sum(sum(v) / len(v) for v in groups.values()) / len(groups)
        return {'score': score, 'outcomes': outcomes}

    results = {label: evaluate(label, 'phi', label.split('_')[0], file)
               for label, file in phi_paths.items()}
    for method in ['standard_adam', 'paired_adam']:
        results[method] = evaluate(method, method)
    late = any(results[f'{a}_2560']['score'] > results[f'{a}_2048']['score'] + .01
               for a in ['L', 'X'])
    assessment = {'status': 'DEV_EVALUATION_COMPLETE', 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'policy': POLICY, 'results': results, 'matched_extension_required': late,
                  'formal_phi_frozen': False}
    Path('records/phi_development_assessment.json').write_text(json.dumps(assessment, indent=2))
    if late:
        for architecture in ['L', 'X']:
            c = json.loads(Path(f'configs/phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8.json').read_text())
            c.update(episode_K=c['episode_K'] + [16] * 64, start_episode=192,
                     initial_phi=phi_paths[f'{architecture}_2560'], load_initial_meta_optimizer=True,
                     purpose='single_matched_development_extension_64x16')
            result = train(f'phi_mix_O1_{architecture}_H2_extend3584_s17_observed_blocking_v8', c)
            phi_paths[f'{architecture}_3584'] = result['checkpoint']
        for architecture in ['L', 'X']:
            file = phi_paths[f'{architecture}_3584']
            plan['allowed_phis'][file] = hashlib.sha256(Path(file).read_bytes()).hexdigest()
        extension_plan = 'records/phi_recovery_development_extension_plan.json'
        preserve_json(extension_plan, json.loads(json.dumps(plan)))
        template['freeze_manifest'] = extension_plan
        for architecture in ['L', 'X']:
            label = f'{architecture}_3584'
            results[label] = evaluate(label, 'phi', architecture, phi_paths[label])
    final_steps = 3584 if late else 2560
    architecture = max(['L', 'X'], key=lambda a: (results[f'{a}_{final_steps}']['score'], a == 'L'))
    assessment.update(status='ARCHITECTURE_SELECTED_PENDING_ORGANIZATION_ADAPTATION',
                      architecture=architecture, core_inner_steps=final_steps,
                      checkpoint=phi_paths[f'{architecture}_{final_steps}'],
                      next='O1/O2/O3/O4 equal 512-step specialization and matched O1 phi_clean before formal phi freezing.')
    Path('records/phi_architecture_selected.json').write_text(json.dumps(assessment, indent=2))
    Path('records/phi_development_assessment.json').write_text(json.dumps(assessment, indent=2))
    print(json.dumps(assessment), flush=True)


if __name__ == '__main__':
    main()
