"""One fixed development parent/dose, frozen rules, actual primary horizon."""
import copy
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from gaosi.scale import sha256, valid_freeze
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 1, 'scope': 'One O1 development parent; no final data or training updates to phi.',
    'methods': ['standard_adam', 'paired_adam', 'phi_mix', 'phi_clean'],
    'schemes': ['subspace', 'factor', 'uniform', 'shrink'],
    'dose': 'Frozen medium q; factor p uses that numerical value only and is not an equal functional/effective-weight dose.',
    'pairing': 'Same parent, primary K, LR, data/noise seed, support streams. Within each scheme all methods use the same initial adapter bytes; schemes intentionally differ.',
    'support': 'Known recovery uses task CE + .25*inner-language CE, same as the ordinary recovery dose calibration and formal known-parent branch.',
    'update_norm_control': 'At the same primary K, rescale phi_mix/subspace and standard_Adam/shrink each step to the corresponding standard_Adam/subspace global adapter update norm. Phi next-step last_update features use the actual scaled/rounded update; this controls magnitude only, not direction.',
    'report': 'Full clean/noisy/endpoint candidate, strict EM, NLL, abnormal output; actual composed relative effective-weight damage and clean/noisy energy; injected/recovered/residual task and language losses; curves, time, memory, compute and norm matching error.',
    'interpretation': 'Factor/uniform are transfers of subspace-trained phi. Same numerical dose is not damage matching; single developer parent/noise seed is descriptive, cannot establish Gaussian uniqueness or distribution superiority. No locally retrained factor-phi claim.',
    'changes_training_or_formal_settings': False, 'formal_data_used': False,
}


def events(result):
    return [json.loads(line) for line in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]


def norms(result):
    found = [e for e in events(result) if e['event'] == 'recover']
    if len(found) != result['config']['K'] or [e['step'] for e in found] != list(range(1, result['config']['K'] + 1)):
        raise ValueError('Norm reference must contain every primary-horizon step exactly once')
    values = [e['update_norm'] for e in found]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Norm reference must contain finite nonnegative actual update norms')
    return values


def summary(result, label):
    record = {key: result[key] for key in ['config', 'initial', 'clean', 'final', 'loss_changes',
                                          'coverage', 'pool', 'base_unchanged', 'compute_counters']}
    trace = events(result)
    record.update(label=label, noise={k: v for k, v in result['noise'].items() if k != 'factors'},
                  curves={str(e['step']): e['metrics'] for e in trace if e['event'] == 'evaluation'},
                  peak_allocated_bytes=max((e.get('peak_allocated_bytes', 0) for e in trace), default=0),
                  recovery_step_seconds=sum(e['step_seconds'] for e in trace if e['event'] == 'recover'),
                  whole_process_seconds=max((e.get('process_wall_seconds', 0) for e in trace), default=0))
    if not {'0', str(result['config']['K'])} <= record['curves'].keys():
        raise ValueError('Noise-control summary needs noisy and exact-primary endpoint evaluations')
    if result['coverage'] != result['pool'] or not result['base_unchanged']:
        raise ValueError('Noise-control endpoint requires complete support and unchanged base')
    if result['config'].get('match_update_norms'):
        target = json.loads(Path(result['config']['match_update_norms']).read_text())
        actual = norms(result)
        errors = [abs(a - b) for a, b in zip(actual, target)]
        record['max_update_norm_absolute_error'] = max(errors)
        record['max_update_norm_relative_error'] = max(d / max(abs(t), 1e-30) for d, t in zip(errors, target))
        if any(d > 2e-6 + 2e-4 * abs(t) for d, t in zip(errors, target)):
            raise ValueError('Applied updates failed the declared norm matching tolerance')
    return record


def entry(name, config, freezes, artifacts):
    c = copy.deepcopy(config)
    manifest = f'records/noise_control_entries/{name}.json'
    c.update(out='runs/' + name, noise_control_entry=manifest)
    Path('records/noise_control_entries').mkdir(parents=True, exist_ok=True)
    paths = set(artifacts) | set(freezes.values()) | {c['parent'], c['freeze_manifest']}
    for key in ['phi', 'initial_adapter', 'match_update_norms']:
        if c.get(key):
            paths.add(c[key])
    for path in list(paths):
        if Path(path).suffix == '.pt':
            for label in ['config.json', 'result.json']:
                sidecar = Path(path).parent / label
                if sidecar.exists():
                    paths.add(str(sidecar))
    data = [c['data'], 'data/language/inner.jsonl', c['language_eval']]
    preserve_json(manifest, {'status': 'FROZEN_NOISE_CONTROL_ENTRY', 'config': c,
                            'phi_freezes': freezes,
                            'artifact_sha256': {p: sha256(p) for p in sorted(paths)},
                            'data_sha256': {p: sha256(p) for p in sorted(data)}})
    path = f'configs/{name}.json'
    preserve_json(path, c)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.noise_controls', '--config', path],
                     Path(c['out']) / 'result.json')
    if result['config'] != c:
        raise ValueError('Preserved noise-control result has a different config')
    return result


def main(device='musa'):
    preserve_json('records/noise_controls_policy_v1.json', POLICY)
    if json.loads(Path('checks/noise_controls_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Noise-control entry/schedule checks must pass first')
    all_frozen = wait_for('records/all_phi_frozen.json', 'organization_queue_observed_blocking_v8')
    freezes = {'mixed': all_frozen['manifests']['O1'], 'clean': all_frozen['manifests']['O1_clean']}
    mix, clean = [valid_freeze(freezes[k]) for k in ['mixed', 'clean']]
    calibration = json.loads(Path('records/calibration_frozen.json').read_text())
    parent = next(p for p in json.loads(Path('records/parents_ready.json').read_text())
                  if p['checkpoint'] == mix['development_parent'])
    if (not parent['acquisition_gate_passed'] or calibration['formal_data_used']
            or not calibration['ordinary_recovery_validated']):
        raise ValueError('Noise control needs the acquired development parent and valid frozen doses')
    phis = {f['phi']: f['phi_sha256'] for f in [mix, clean]}
    plan = {'status': 'PHI_DEVELOPMENT_PLAN', 'noise_transfer_diagnostic': True,
            'policy': POLICY, 'formal_data_used': False,
            'allowed_data': [parent['config']['data']], 'allowed_parents': [parent['checkpoint']],
            'allowed_phis': phis, 'lr_candidates': [mix['recovery_lr']],
            'K_candidates': [mix['recovery_K']], 'q_candidates': [mix['q_medium']],
            'noise_schemes': POLICY['schemes'], 'phi_freezes': freezes}
    planfile = 'records/noise_controls_plan_v1.json'
    preserve_json(planfile, plan)
    K = mix['recovery_K']
    template = {'phase': 'phi_development', 'freeze_manifest': planfile,
                'parent': parent['checkpoint'], 'data': parent['config']['data'],
                'organization': 'O1', 'architecture': mix['architecture'], 'device': device,
                'data_seed': 17, 'noise_seed': 1729, 'q_apply': mix['q_medium'],
                'task_layout': 'all', 'target_ids': [], 'D': 'plus',
                'retain_subset_fraction': 1., 'subset_seed': 91,
                'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': mix['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': K, 'eval_steps': [n for n in [8, 16, 32, 64] if n <= K]}
    initial, outcomes, source_norms = {}, [], None
    for scheme in POLICY['schemes']:
        for method in POLICY['methods']:
            c = dict(template, noise_scheme=scheme, method=method)
            if scheme in initial:
                c['initial_adapter'] = initial[scheme]
            if method.startswith('phi_'):
                f = clean if method == 'phi_clean' else mix
                c.update(method='phi', phi=f['phi'])
            result = entry(f'noise_control_{scheme}_{method}_K{K}_s17_observed_blocking_v8', c, freezes,
                           ['records/calibration_frozen.json', 'records/noise_controls_policy_v1.json'])
            initial.setdefault(scheme, str(Path(result['config']['out']) / 'initial.pt'))
            outcomes.append(summary(result, method))
            if scheme == 'subspace' and method == 'standard_adam':
                source_norms = norms(result)
    normfile = 'records/noise_control_primary_stock_update_norms_v1.json'
    preserve_json(normfile, source_norms)
    for scheme, method in [('subspace', 'phi_mix'), ('shrink', 'standard_adam')]:
        c = dict(template, noise_scheme=scheme, method=method, initial_adapter=initial[scheme],
                 match_update_norms=normfile)
        if method == 'phi_mix':
            c.update(method='phi', phi=mix['phi'])
        result = entry(f'noise_control_{scheme}_{method}_fixednorm_K{K}_s17_observed_blocking_v8', c, freezes,
                       ['records/calibration_frozen.json', 'records/noise_controls_policy_v1.json'])
        outcomes.append(summary(result, method + '_fixednorm'))
    preserve_json('records/noise_controls_ready_v1.json',
                  {'status': 'FROZEN_PHI_NOISE_TRANSFER_CONTROLS_COMPLETE', 'plan': plan, 'outcomes': outcomes,
                   'independent_phi_training_seeds': 1, 'noise_seeds': [1729],
                   'distribution_superiority_established': False,
                   'settings_changed_by_results': False, 'overall_experiment_complete': False})


if __name__ == '__main__':
    main()
