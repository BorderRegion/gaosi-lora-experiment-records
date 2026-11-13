"""Frozen O1 holdout grid and selective comparisons; reflow remains separate."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 2,
    'parent_requirement': 'all phi frozen before paired final all/retain-only fitting; complete acquisition and matched R exposure',
    'main_tasks': 'known final parent plus all 32 final worlds; no selection using final scores',
    'main_methods': ['standard_adam', 'paired_adam', 'factor_lr', 'phi_mix', 'phi_clean'],
    'factor_lr': 'Recovery-specific dev-only 3x3 ratio/LR screen; its frozen setting replaces the acquisition-only ratio.',
    'main_doses': 'zero and development-frozen medium; known task also light',
    'primary_K': 'development-frozen recovery_K, used as the actual trajectory horizon',
    'main_curve': '0 and predefined 8/16/32/64 steps up to primary_K',
    'known_noise_seeds': [1729, 1730, 1731],
    'new_world_noise_seed': 1729,
    'selective': {'request': '64 attribute-zero bindings fixed in original manifest',
                  'retain_fractions': [1., .25], 'subset_seed': 91,
                  'methods': ['standard_adam_Dplus', 'standard_adam_Dminus', 'phi_mix_Dplus', 'phi_mix_Dminus'],
                  'q': 'medium', 'noise_seeds': [1729, 1730, 1731],
                  'primary_K': 'same frozen primary_K',
                  'diagnostic_K64': 'separate K64 trajectories for noise seed1729 when primary_K differs; their intermediate phi states are not substituted for primary-horizon results'},
    'pairing': 'same initial adapter bytes within parent/noise seed/dose across tasks, methods and D directions',
    'support': 'Dminus F slot zero; R sampling and mass unchanged; endpoint covers every selected R binding',
    'references': ['base', 'drop_adapter', 'retain_only', 'parent_all'],
    'aggregation': 'candidate and strict EM separated; equal world weight within fact/rule, then equal family weight; noise seeds are not independent phi training',
    'not_completed_by_this_queue': ['pure-R 20/80 reflow', 'entity 2-of-8 reteaching', 'continual organizations',
                                    '7B phi transfer/local training', 'independent training confirmations'],
}


def execute(name, config, module='gaosi.recover'):
    c = copy.deepcopy(config)
    c['out'] = 'runs/' + name
    file = Path('configs') / (name + '.json')
    preserve_json(file, c)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', module, '--config', str(file)],
                     Path(c['out']) / 'result.json')
    if result['config'] != c:
        raise ValueError(f'Preserved configuration differs: {name}')
    return result


def intact_manifest(file, training_noise):
    manifest = json.loads(Path(file).read_text())
    if (manifest['status'] != 'FROZEN' or not manifest['stopping_rule_frozen']
            or manifest['organization'] != 'O1' or manifest['training_noise'] != training_noise
            or manifest['formal_data_used']
            or hashlib.sha256(Path(manifest['phi']).read_bytes()).hexdigest() != manifest['phi_sha256']):
        raise ValueError(f'Invalid O1 freeze manifest: {file}')
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--final-parent-job', default='final_parent_queue_recompute_v7')
    parser.add_argument('--factor-job', default='factor_recovery_queue_v1')
    args = parser.parse_args()
    preserve_json('records/formal_recovery_policy_v2.json', POLICY)
    if json.loads(Path('checks/formal_recovery_v2_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Formal entry/reference integration checks must pass')
    ready = wait_for('records/final_parents_ready.json', args.final_parent_job)
    if (ready['status'] != 'FINAL_PARENTS_READY' or not ready['matched_R_exposure']
            or not ready['phi_frozen_before_training']
            or not all(r['acquisition_gate_passed'] for r in ready['parents'].values())):
        raise ValueError('Formal comparisons require valid matched final parents')
    all_frozen = json.loads(Path('records/all_phi_frozen.json').read_text())
    if all_frozen['status'] != 'ALL_PHI_FROZEN':
        raise ValueError('All phi must be frozen before formal data access')
    mixfile, cleanfile = (all_frozen['manifests'][key] for key in ['O1', 'O1_clean'])
    mix, clean = intact_manifest(mixfile, 'mixed'), intact_manifest(cleanfile, 'clean')
    factor = wait_for('records/factor_recovery_frozen_v1.json', args.factor_job)
    if (factor['status'] != 'FACTOR_RECOVERY_FROZEN' or factor['formal_data_used']
            or factor['selection_K'] != mix['recovery_K'] or factor['q_medium'] != mix['q_medium']
            or factor['parent'] != mix['development_parent']):
        raise ValueError('Factor-LR recovery selection must match the frozen development setting')
    if any(mix[key] != clean[key] for key in ['architecture', 'recovery_K', 'recovery_lr', 'q_light', 'q_medium', 'total_training_inner_steps']):
        raise ValueError('Clean/mixed phi settings or training opportunities differ')
    parent = ready['parents']['all']
    K = mix['recovery_K']
    if K not in [8, 16, 32, 64]:
        raise ValueError('Unexpected frozen primary budget')
    manifest = json.loads(Path(mix['world_manifest']).read_text())
    final_worlds = sorted([world for world in manifest['worlds'] if world['split'] == 'final'],
                          key=lambda world: world['world_id'])
    if len(final_worlds) != 32:
        raise ValueError('The main grid requires all 32 final worlds')
    tasks = [{'label': 'known', 'group': 'known', 'data': parent['config']['data']}]
    tasks += [{'label': world['world_id'], 'group': 'fact' if world['kind'] == 'fact' else 'rule',
               'data': str(Path('data/synthetic') / world['file'])} for world in final_worlds]
    plan = {'status': 'FROZEN_FORMAL_RECOVERY_PLAN', 'policy': POLICY, 'tasks': tasks,
            'primary_K': K, 'freeze_manifests': {'mixed': mixfile, 'clean': cleanfile},
            'factor_recovery_selection': 'records/factor_recovery_frozen_v1.json',
            'phi_sha256': {'mixed': mix['phi_sha256'], 'clean': clean['phi_sha256']},
            'parent': parent['checkpoint'], 'final_parent_plan': ready['training_plan'],
            'target_ids': ready['target_ids'], 'retain_ids': ready['retain_ids']}
    preserve_json('records/formal_recovery_plan_v2.json', plan)
    Path('records/formal_completed').mkdir(parents=True, exist_ok=True)
    references = execute('formal_O1_references_s17_v2', {
        'freeze_manifest': mixfile, 'parents': {key: r['checkpoint'] for key, r in ready['parents'].items()},
        'data': parent['config']['data'], 'target_ids': ready['target_ids'], 'seed': 17,
        'device': 'musa', 'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096},
        'gaosi.references')
    template = {'out': '', 'phase': 'formal', 'freeze_manifest': mixfile, 'parent': parent['checkpoint'],
                'organization': 'O1', 'device': 'musa', 'architecture': mix['architecture'],
                'data_seed': 17, 'noise_seed': 1729, 'q_apply': 0., 'noise_scheme': 'subspace',
                'data': '', 'task_layout': 'all', 'target_ids': [], 'D': 'plus',
                'retain_subset_fraction': 1., 'subset_seed': 91, 'method': 'standard_adam',
                'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': mix['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': K, 'eval_steps': [step for step in [8, 16, 32, 64] if step <= K]}
    initial = {}
    outputs = []
    for task in tasks:
        doses = [('clean', 0., 1729), ('medium', mix['q_medium'], 1729)]
        if task['label'] == 'known':
            doses += [('light', mix['q_light'], 1729)]
            doses += [('medium', mix['q_medium'], seed) for seed in [1730, 1731]]
        for dose, q, seed in doses:
            for label in POLICY['main_methods']:
                c = dict(template, data=task['data'], q_apply=q, noise_seed=seed)
                if task['label'] != 'known':
                    c['retain_data'] = parent['config']['data']
                if label.startswith('phi_'):
                    f, frozen = (cleanfile, clean) if label == 'phi_clean' else (mixfile, mix)
                    c.update(method='phi', freeze_manifest=f, phi=frozen['phi'])
                else:
                    c['method'] = label
                if label == 'factor_lr':
                    c.update(ba_ratio=factor['selected_ratio'], inner_lr=factor['selected_lr'])
                key = (q, seed)
                if key in initial:
                    c['initial_adapter'] = initial[key]
                name = f'formal_O1_{task["label"]}_{dose}_{label}_n{seed}_K{K}_v2'
                result = execute(name, c)
                initial.setdefault(key, str(Path(result['config']['out']) / 'initial.pt'))
                outputs.append({'task': task['label'], 'group': task['group'], 'dose': dose, 'method': label,
                                'noise_seed': seed, 'run': result['config']['out'], 'final': result['final'],
                                'loss_changes': result['loss_changes'], 'primary_K': K})
                preserve_json(f'records/formal_completed/{name}.json', outputs[-1])
    preserve_json('records/formal_main_grid_ready_v2.json', {
        'status': 'MAIN_GRID_COMPLETE', 'policy': POLICY, 'plan': plan,
        'references': references['config']['out'], 'outcomes': outputs,
        'independent_phi_training_seeds': 1, 'selective_reflow_continual_scale_complete': False})
    selective = []
    for fraction in [1., .25]:
        for seed in POLICY['selective']['noise_seeds']:
            budgets = [K] + ([64] if K != 64 and seed == 1729 else [])
            for budget in budgets:
                for method in ['standard_adam', 'phi']:
                    for direction in ['plus', 'minus']:
                        c = dict(template, data=parent['config']['data'], task_layout='selective',
                                 target_ids=ready['target_ids'], D=direction, retain_subset_fraction=fraction,
                                 noise_seed=seed, q_apply=mix['q_medium'], method=method, K=budget,
                                 eval_steps=[step for step in [8, 16, 32, 64] if step <= budget],
                                 initial_adapter=initial[(mix['q_medium'], seed)])
                        if method == 'phi':
                            c['phi'] = mix['phi']
                        name = f'formal_selective_R{round(fraction*100)}_{method}_{direction}_n{seed}_K{budget}_v2'
                        result = execute(name, c)
                        selective.append({'retain_fraction': fraction, 'method': method, 'D': direction,
                                          'noise_seed': seed, 'K': budget, 'primary': budget == K,
                                          'run': result['config']['out'], 'checkpoint': result['checkpoint'],
                                          'coverage': result['coverage'], 'pool': result['pool'],
                                          'final': result['final'], 'loss_changes': result['loss_changes']})
                        preserve_json(f'records/formal_completed/{name}.json', selective[-1])
    preserve_json('records/formal_recovery_ready_v2.json', {
        'status': 'FORMAL_RECOVERY_GRID_COMPLETE_REFLOW_PENDING', 'plan': plan,
        'references': references['config']['out'], 'main_outcomes': outputs, 'selective_outcomes': selective,
        'primary_K': K, 'independent_phi_training_seeds': 1,
        'next': POLICY['not_completed_by_this_queue']})


if __name__ == '__main__':
    main()
