"""One fixed small base perturbation, one matched zero-dose repair control."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.scale import sha256
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 1,
    'purpose': 'Small full-base-noise + LoRA repair diagnostic; not adapter-noise core or a phi training objective.',
    'data': '3B development parent and its full512 query library, inner support language and dev evaluation language only.',
    'base_q': [0., 1e-4],
    'definition': 'Every unique floating pretrained parameter: sqrt(1-q)*W + sqrt(q)*RMS(W)*Z in FP32 then one BF16 working-copy rounding; per-parameter independent CPU generator.',
    'base_scope': 'Embeddings, head, linears, norms and biases included; tied storage once. Zero-RMS parameters stay zero; adapter and non-parameter buffers unchanged.',
    'base_noise_seed': 2701,
    'chunk_elements': 1048576,
    'adapter': 'Same canonical learned nonzero development adapter in both groups, q_apply=0, standard library Adam; no phi and no added adapter noise.',
    'repair_lr': 'Existing development-frozen recovery_lr; never tuned against this diagnostic.',
    'budget': 'One64-step standard Adam trajectory per dose, evaluate0/8/16/32/64. Its frozen primary-K state is a valid budget endpoint because stock Adam does not use K-dependent phi features.',
    'metrics': 'Complete known512 candidate/strict EM/abnormal and dev language; pristine baseline, corrupted initial, repair curve; injected/recovered/residual relative to pristine W0+parent.',
    'precision': 'Report actual base damage after BF16 rounding separately from intended FP32 perturbation.',
    'files': 'Only loaded memory is perturbed; pretrained model files are never written. Checkpoints contain adapter/optimizer and deterministic corruption settings, not a second full model.',
    'interpretation': 'Low-rank compensation feasibility at one declared small dose and budget. Undetectable initial damage or a failed repair does not decide the main hypothesis.',
    'formal_data_used': False,
}


def main():
    preserve_json('records/base_boundary_policy_v1.json', POLICY)
    if json.loads(Path('checks/base_boundary_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Base-boundary numerical and resume checks must pass')
    calibration = wait_for('records/calibration_frozen.json', 'calibration_queue_v3')
    if calibration['status'] != 'CALIBRATION_FROZEN' or calibration['formal_data_used']:
        raise ValueError('Boundary requires development-only frozen calibration')
    parents = json.loads(Path('records/parents_ready.json').read_text())
    parent = next(p for p in parents if p['config']['data'] == 'data/synthetic/dev_parent.jsonl')
    planfile = 'records/base_boundary_plan_v1.json'
    template = {'freeze_manifest': planfile, 'phase': 'development', 'data_seed': 17, 'noise_seed': 1729,
                'parent': parent['checkpoint'], 'device': 'musa', 'q_apply': 0., 'noise_scheme': 'subspace',
                'architecture': 'L', 'method': 'standard_adam', 'data': parent['config']['data'],
                'target_ids': [], 'task_layout': 'all', 'retain_subset_fraction': 1., 'subset_seed': 91,
                'D': 'plus', 'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': calibration['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': 64, 'eval_steps': [8, 16, 32, 64], 'base_noise_seed': POLICY['base_noise_seed'],
                'base_chunk_elements': POLICY['chunk_elements']}
    configs = []
    for label, q in [('zero', 0.), ('small', 1e-4)]:
        c = dict(template, base_q=q, out=f'runs/base_boundary_{label}_Adam64_s17_v1')
        if q:
            c['initial_adapter'] = 'runs/base_boundary_zero_Adam64_s17_v1/initial.pt'
        configs.append(c)
    sources = [template['data'], template['language_eval'], 'data/language/inner.jsonl']
    plan = {'status': 'DEVELOPMENT_PLAN', 'base_boundary_diagnostic': True, 'policy': POLICY,
            'allowed_data': [template['data']], 'allowed_parents': [parent['checkpoint']],
            'lr_candidates': [calibration['recovery_lr']], 'K_candidates': [64],
            'q_candidates': [0.], 'noise_schemes': ['subspace'], 'no_formal_data': True,
            'boundary_configs': configs, 'primary_K': calibration['recovery_K'],
            'calibration_manifest': 'records/calibration_frozen.json',
            'calibration_sha256': sha256('records/calibration_frozen.json'),
            'parent_sha256': sha256(parent['checkpoint']),
            'data_sha256': {p: sha256(p) for p in sources}}
    preserve_json(planfile, plan)
    outcomes = []
    for config in configs:
        name = Path(config['out']).name
        file = Path('configs') / (name + '.json')
        preserve_json(file, config)
        result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.base_boundary', '--config', str(file)],
                         Path(config['out']) / 'boundary_result.json')
        if result['config'] != config or result['repair']['coverage'] != 512:
            raise ValueError('Boundary result lacks declared settings/full binding coverage')
        events = [json.loads(s) for s in (Path(config['out']) / 'events.jsonl').read_text().splitlines()]
        primary = next(e['metrics'] for e in events
                       if e['event'] == 'evaluation' and e['step'] == calibration['recovery_K'])
        outcomes.append({'run': config['out'], 'base_q': config['base_q'],
                         'primary': primary, 'final': result['repair']['final'],
                         'corruption': result['corruption'], 'reference': result['pristine_reference'],
                         'loss_changes_vs_pristine_base': result['loss_changes_vs_pristine_base']})
    if outcomes[0]['reference'] != outcomes[1]['reference']:
        raise ValueError('Paired boundary groups have different pristine measurements')
    preserve_json('records/base_boundary_ready_v1.json',
                  {'status': 'BASE_NOISE_LORA_REPAIR_DIAGNOSTIC_COMPLETE', 'plan': plan, 'outcomes': outcomes,
                   'settings_changed_by_outcomes': False, 'phi_efficacy_measured': False,
                   'main_experiment_completion': False})


if __name__ == '__main__':
    main()
