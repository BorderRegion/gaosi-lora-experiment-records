"""Two independent phi seeds/orders, frozen key recovery and continual contrasts."""
import copy
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path.cwd()))
from gaosi.scale import sha256
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 1, 'independent_phi_seeds': [29, 43],
    'start': 'Fresh Phi initialization and fresh meta Adam for each mixed/clean rule, selected architecture only. Never initialize an independent seed from seed17 phi.',
    'meta_order': 'Permute the same meta worlds and paired meta parents with each independent seed; support/noise episode seeds also change. O4 history/current pairs stay attached.',
    'training_budget': 'Exactly the development-selected core2560 or single extension3584, plus512 organization updates. Clean O1 trained from scratch at the same total budget. No new rank/LR/q/K/architecture search.',
    'second_organization': 'After all sixteen eight-round pilots: O2/O3/O4 score=.5*(abs(clean phi minus clean Adam)+abs(noise phi minus noise Adam)), each contrast averages mean history and mean new-night mean(candidate,EM) over rounds1..8. Highest score; tie O2 then O3 then O4. Selection uses magnitude including negative results and is reported, not treated as confirmatory evidence.',
    'continual': 'O1 and selected second organization, clean/noise stock Adam and clean/noise own mixed phi, two new eight-round orders. Same first8 final fact worlds, disjoint original four probes, budgets and learning rates. Common rank16 probes at0/4/8 and inference measurements unchanged.',
    'main_tasks': 'Known final parent plus first4 final fact and first4 final rule worlds by ID, fixed before results.',
    'main_methods': ['standard_adam', 'paired_adam', 'factor_lr', 'phi_mix', 'phi_clean'],
    'main_doses': 'zero and frozen medium, actual frozen primary K, noise1729; all methods share damaged adapter bytes and independent support seed within each replicate.',
    'selective': 'Full and25%R, stock Adam/own mixed phi, Dplus/Dminus, medium/noise1729, actual primary K; complete selected-R endpoint coverage.',
    'reflow': 'Both frozen LRs, pure complete R for20/80 from each full/quarter-R primary Dminus Adam/phi endpoint, plus matched retain-only. Fixed entity request also repeats Adam/phi Dminus and two-of-eight teaching20/80 with matched retain-only.',
    'parent_scope': 'Existing meta parents, O4 history/current parents and final all/retain/entity parents are shared. These are independent phi training/support/order confirmations conditional on shared parents, not independent parent training or independent noise replicates.',
    'data_permission': 'Only meta parents/worlds and inner/meta-query language enter phi gradients. Shared final parents/outcomes never select initialization, training budget, rates, doses, stopping or structure. Only second-organization selection uses the explicitly exploratory pilot rule.',
    'inference': 'Aggregate by world/entity within each independent phi seed; do not count task/noise/round repeats as independent phi training. Retain seed17 pilot selection separately from seed29/43 confirmation.',
    'formal_outcomes_select_training_settings': False,
}


def reordered_manifest(source, seed):
    value = copy.deepcopy(source)
    slots = [i for i, w in enumerate(value['worlds']) if w['split'] == 'meta']
    worlds = [value['worlds'][i] for i in slots]
    random.Random(seed).shuffle(worlds)
    for index, world in zip(slots, worlds):
        value['worlds'][index] = world
    return value


def select_second(pilot):
    if pilot['status'] != 'SIXTEEN_EIGHT_ROUND_PILOTS_COMPLETE':
        raise ValueError('Organization selection requires all sixteen preserved pilots')
    expected = {(org, method) for org in ['O1', 'O2', 'O3', 'O4']
                for method in ['clean_Adam', 'noise_Adam', 'clean_phi', 'noise_phi']}
    if {(r['organization'], r['method']) for r in pilot['outcomes']} != expected or len(pilot['outcomes']) != 16:
        raise ValueError('Incomplete or duplicated pilot organization/method grid')
    scores = {}
    for outcome in pilot['outcomes']:
        if outcome['completed_rounds'] != 8 or outcome['result']['status'] != 'PASS':
            raise ValueError('Only complete valid eight-round pilots may select the organization')
        events = [json.loads(line) for line in (Path(outcome['run']) / 'events.jsonl').read_text().splitlines()]
        means = []
        for event, nested in [('history_evaluation', False), ('new_night_evaluation', True)]:
            found = [e for e in events if e['event'] == event and e['round'] in range(1, 9)]
            if len(found) != 8 or {e['round'] for e in found} != set(range(1, 9)):
                raise ValueError('Pilot must contain each declared history/new-night measurement once')
            metrics = [e['metrics'] if nested else e for e in found]
            if any(not math.isfinite(m[k]) or not 0 <= m[k] <= 1
                   for m in metrics for k in ['candidate_accuracy', 'strict_EM']):
                raise ValueError('Pilot selection requires finite valid measurements')
            means.append(sum(.5 * (m['candidate_accuracy'] + m['strict_EM']) for m in metrics) / 8)
        scores[(outcome['organization'], outcome['method'])] = sum(means) / 2
    rows = []
    for org in ['O2', 'O3', 'O4']:
        clean = scores[(org, 'clean_phi')] - scores[(org, 'clean_Adam')]
        noisy = scores[(org, 'noise_phi')] - scores[(org, 'noise_Adam')]
        rows.append({'organization': org, 'clean_contrast': clean, 'noise_contrast': noisy,
                     'information_score': .5 * (abs(clean) + abs(noisy))})
    selected = max(rows, key=lambda r: (r['information_score'], -int(r['organization'][1])))
    return {'status': 'SECOND_ORGANIZATION_SELECTED_FROM_PILOTS', 'organization': selected['organization'],
            'candidates': rows, 'policy': POLICY['second_organization'],
            'pilot_selection_is_exploratory': True, 'independent_confirmation_complete': False}


def entry(name, config, action, seed, artifacts, data, freezes):
    c = copy.deepcopy(config)
    manifest = f'records/confirmation_entries/{name}.json'
    c.update(out='runs/' + name, confirmation_entry_manifest=manifest,
             confirmation_action=action, confirmation_seed=seed)
    Path('records/confirmation_entries').mkdir(parents=True, exist_ok=True)
    sources = set(artifacts) | set(freezes)
    # Numerical runners also consume result/config sidecars; freeze these bytes.
    for path in list(sources):
        if Path(path).suffix == '.pt':
            for label in ['result.json', 'config.json']:
                sidecar = Path(path).parent / label
                if sidecar.exists():
                    sources.add(str(sidecar))
    preserve_json(manifest, {'status': 'FROZEN_CONFIRMATION_ENTRY', 'config': c,
                            'independent_phi_seed': seed, 'freeze_manifests': freezes,
                            'artifact_sha256': {p: sha256(p) for p in sorted(sources)},
                            'data_sha256': {p: sha256(p) for p in sorted(set(data))}})
    path = f'configs/{name}.json'
    preserve_json(path, c)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.confirmation', '--config', path],
                     Path(c['out']) / 'result.json')
    saved_config = result.get('config')
    if saved_config is None and action == 'meta':
        saved_config = json.loads((Path(c['out']) / 'config.json').read_text())
    if saved_config != c:
        raise ValueError('Preserved independent run has different settings')
    return dict(result, config=c)


def train_seed(seed, selected, original, second):
    source = json.loads(Path(original['world_manifest']).read_text())
    manifest = f'data/synthetic/confirmation_meta_order_s{seed}_observed_blocking_v8.json'
    preserve_json(manifest, reordered_manifest(source, seed))
    architecture = selected['architecture']
    reference = f'configs/phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8.json'
    core = json.loads(Path(reference).read_text())
    for key in ['initial_phi', 'start_episode', 'load_initial_meta_optimizer', 'out']:
        core.pop(key, None)
    schedule = [8] * 64 + [16] * 128
    if selected['core_inner_steps'] == 3584:
        schedule += [16] * 64
    if sum(schedule) != selected['core_inner_steps']:
        raise ValueError('Independent training must use the development-selected core budget')
    order = list(range(len(core['parents'])))
    random.Random(seed + 1).shuffle(order)
    for key in ['parents', 'parent_data']:
        core[key] = [core[key][i] for i in order]
    core.update(seed=seed, world_manifest=manifest, episode_K=schedule,
                meta_reference_config=reference,
                confirmation_stage='mixed_core_from_zero', purpose='locked_independent_phi_core_from_zero')
    upstream = ['records/phi_architecture_selected.json', 'records/calibration_frozen.json',
                'records/confirmation_policy_v1.json', reference]
    meta_data = [str(Path('data/synthetic') / w['file']) for w in source['worlds'] if w['split'] == 'meta']
    data = meta_data + core['parent_data'] + [manifest, original['world_manifest'],
                                            'data/language/inner.jsonl', 'data/language/meta_query.jsonl']
    result = entry(f'confirm_phi_mix_O1_{architecture}_core_s{seed}_observed_blocking_v8', core, 'meta', seed,
                   upstream + core['parents'], data, [])
    core_phi = result['checkpoint']
    if result['inner_updates'] != selected['core_inner_steps']:
        raise ValueError('Independent mixed core has the wrong development-selected budget')
    freezes = {}
    for org in ['O1', second]:
        c = copy.deepcopy(core)
        c.update(organization=org, episode_K=[8] * 64, known_warm_episodes=0,
                 initial_phi=core_phi, load_initial_meta_optimizer=True,
                 confirmation_stage='organization_specialization', purpose='independent_same_core_specialization512')
        artifacts, sources = upstream + c['parents'] + [core_phi], data[:]
        if org == 'O4':
            o4 = json.loads(Path('records/o4_parents_ready.json').read_text())
            c.update(parents=[r['checkpoint'] for r in o4['parents']],
                     parent_data=[r['config']['data'] for r in o4['parents']],
                     historical_bases=[r['master'] for r in o4['histories']],
                     historical_data=[r['historical_bindings'] for r in o4['histories']])
            for key in ['parents', 'parent_data', 'historical_bases', 'historical_data']:
                c[key] = [c[key][i] for i in order]
            artifacts += ['records/o4_parents_ready.json'] + c['parents'] + c['historical_bases']
            artifacts += [p + '.json' for p in c['historical_bases']]
            sources += c['parent_data'] + c['historical_data']
        adapted = entry(f'confirm_phi_mix_{org}_{architecture}_specialize512_s{seed}_observed_blocking_v8',
                        c, 'meta', seed, artifacts, sources, [])
        if adapted['inner_updates'] != 512:
            raise ValueError('Independent organization specialization has wrong budget')
        freezes[org] = make_freeze(seed, org, 'mixed', adapted['checkpoint'], original, selected)
    clean = copy.deepcopy(core)
    clean.update(episode_K=schedule + [8] * 64, q_cycle=[0.],
                 confirmation_stage='clean_full_from_zero', purpose='independent_clean_matched_from_zero')
    clean_result = entry(f'confirm_phi_clean_O1_{architecture}_matched_s{seed}_observed_blocking_v8', clean, 'meta', seed,
                         upstream + clean['parents'], data, [])
    if clean_result['inner_updates'] != sum(schedule) + 512:
        raise ValueError('Independent clean budget differs from mixed core plus specialization')
    freezes['O1_clean'] = make_freeze(seed, 'O1', 'clean', clean_result['checkpoint'], original, selected)
    preserve_json(f'records/confirmation_phi_frozen_s{seed}_observed_blocking_v8.json',
                  {'status': 'INDEPENDENT_PHI_FROZEN', 'seed': seed, 'manifests': freezes,
                   'shared_parent_conditioning': True, 'meta_order_manifest': manifest,
                   'final_data_used_in_phi_training': False, 'initialized_from_seed17_phi': False})
    return freezes


def make_freeze(seed, org, training_noise, checkpoint, original, selected):
    f = copy.deepcopy(original)
    f.update(status='FROZEN', organization=org, training_noise=training_noise,
             phi=checkpoint, phi_sha256=sha256(checkpoint), independent_phi_seed=seed,
             stopping_rule_frozen=True, formal_data_used=False,
             core_inner_steps=selected['core_inner_steps'], specialization_inner_steps=512,
             total_training_inner_steps=selected['core_inner_steps'] + 512,
             source_phi_seed17_used=False, shared_parent_conditioning=True,
             primary_organization_plan=original.get('organization_plan'),
             organization_plan='records/confirmation_policy_v1.json')
    suffix = org + ('_clean' if training_noise == 'clean' else '')
    path = f'records/confirm_phi_frozen_{suffix}_s{seed}_observed_blocking_v8.json'
    preserve_json(path, f)
    return path


def evaluate_seed(seed, freezes, ready, original, factor, entity, rates):
    mixfile, cleanfile = freezes['O1'], freezes['O1_clean']
    mix, clean = [json.loads(Path(p).read_text()) for p in [mixfile, cleanfile]]
    parent = ready['parents']['all']
    K = mix['recovery_K']
    worlds = json.loads(Path(mix['world_manifest']).read_text())['worlds']
    tasks = [('known', parent['config']['data'])]
    for kind in ['fact', 'affine_integer']:
        chosen = sorted([w for w in worlds if w['split'] == 'final' and w['kind'] == kind],
                        key=lambda w: w['world_id'])[:4]
        if len(chosen) != 4:
            raise ValueError('Need four predeclared tasks per family for confirmation')
        tasks += [(w['world_id'], str(Path('data/synthetic') / w['file'])) for w in chosen]
    template = {'phase': 'formal', 'freeze_manifest': mixfile, 'parent': parent['checkpoint'],
                'organization': 'O1', 'device': 'musa', 'architecture': mix['architecture'],
                'data_seed': seed, 'noise_seed': 1729, 'noise_scheme': 'subspace',
                'task_layout': 'all', 'target_ids': [], 'D': 'plus',
                'retain_subset_fraction': 1., 'subset_seed': 91,
                'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096,
                'inner_lr': mix['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
                'K': K, 'eval_steps': [step for step in [8, 16, 32, 64] if step <= K]}
    initial, outcomes = {}, []

    def recover(label, config):
        artifacts = [config['parent']]
        if config.get('initial_adapter'):
            artifacts += [config['initial_adapter']]
        if config.get('phi'):
            artifacts += [config['phi']]
        if config['method'] == 'factor_lr':
            artifacts += ['records/factor_recovery_frozen_v1.json']
        data = [config['data'], config.get('retain_data', config['data']),
                config['language_eval'], 'data/language/inner.jsonl']
        result = entry(f'confirm_{label}_s{seed}_observed_blocking_v8', config, 'recover', seed,
                       artifacts, data, [config['freeze_manifest']])
        if result['coverage'] != result['pool'] or not result['base_unchanged']:
            raise ValueError('Confirmation recovery endpoint lacks full selected support coverage')
        outcomes.append({'block': label, 'run': result['config']['out'], 'final': result['final'],
                         'checkpoint': result['checkpoint'], 'config': result['config']})
        return result

    for label, file in tasks:
        for dose, q in [('clean', 0.), ('medium', mix['q_medium'])]:
            for method in POLICY['main_methods']:
                c = dict(template, data=file, q_apply=q, method=method)
                if label != 'known':
                    c['retain_data'] = parent['config']['data']
                if method.startswith('phi_'):
                    f, frozen = (cleanfile, clean) if method == 'phi_clean' else (mixfile, mix)
                    c.update(method='phi', freeze_manifest=f, phi=frozen['phi'])
                if method == 'factor_lr':
                    c.update(ba_ratio=factor['selected_ratio'], inner_lr=factor['selected_lr'])
                if q in initial:
                    c['initial_adapter'] = initial[q]
                result = recover(f'main_{label}_{dose}_{method}_K{K}', c)
                initial.setdefault(q, str(Path(result['config']['out']) / 'initial.pt'))

    def continue_sources(label, source_parent, request, mode, sources):
        for origin, checkpoint, kind in sources:
            for lr in rates['lrs']:
                c = {'freeze_manifest': mixfile, 'lr_freeze_manifest': 'records/reflow_lrs_frozen_v1.json',
                     'parent': source_parent['checkpoint'], 'source_checkpoint': checkpoint,
                     'source_kind': kind, 'data': source_parent['config']['data'],
                     'target_ids': request['target_ids'], 'taught_ids': request.get('taught_ids', []),
                     'mode': mode, 'inner_lr': lr, 'device': 'musa', 'data_seed': seed,
                     'K': 80, 'eval_steps': [20, 80], 'effective_tokens': 512,
                     'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096}
                result = entry(f'confirm_{label}_{origin}_lr{lr:g}_s{seed}_observed_blocking_v8', c, 'reflow', seed,
                               [checkpoint, source_parent['checkpoint'], 'records/reflow_lrs_frozen_v1.json'],
                               [c['data'], c['language_eval']], [mixfile])
                events = [json.loads(line) for line in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
                curves = {str(e['step']): e['metrics'] for e in events
                          if e['event'] == 'evaluation' and e['step'] in [0, 20, 80]}
                if set(curves) != {'0', '20', '80'} or result['coverage'] != result['pool']:
                    raise ValueError('Independent continuation must have complete support and 0/20/80 measurements')
                outcomes.append({'block': label, 'origin': origin, 'lr': lr, 'run': result['config']['out'],
                                 'initial': result['initial'], 'final': result['final'],
                                 'source_sha256': result['source_sha256'], 'curves': curves})

    sources = []
    for fraction in [1., .25]:
        for method in ['standard_adam', 'phi']:
            for direction in ['plus', 'minus']:
                label = f'selective_R{round(fraction*100)}_{method}_{direction}'
                c = dict(template, data=parent['config']['data'], task_layout='selective',
                         target_ids=ready['target_ids'], D=direction, retain_subset_fraction=fraction,
                         q_apply=mix['q_medium'], method=method, initial_adapter=initial[mix['q_medium']])
                if method == 'phi':
                    c['phi'] = mix['phi']
                result = recover(label, c)
                if direction == 'minus':
                    sources.append((label, result['checkpoint'], 'selective'))
    sources.append(('matched_retain_only', ready['parents']['retain_only']['checkpoint'], 'retain_only'))
    continue_sources('pureR', parent, {'target_ids': ready['target_ids']}, 'pure_R', sources)
    request = entity['request']
    parent = entity['parents']['all']
    sources, entity_initial = [], None
    for method in ['standard_adam', 'phi']:
        c = dict(template, parent=parent['checkpoint'], data=parent['config']['data'],
                 task_layout='selective', target_ids=request['target_ids'], D='minus',
                 q_apply=mix['q_medium'], method=method)
        if entity_initial:
            c['initial_adapter'] = entity_initial
        if method == 'phi':
            c['phi'] = mix['phi']
        result = recover(f'entity_Dminus_{method}', c)
        entity_initial = entity_initial or str(Path(result['config']['out']) / 'initial.pt')
        sources.append((method, result['checkpoint'], 'selective'))
    sources.append(('matched_retain_only', entity['parents']['retain_only']['checkpoint'], 'retain_only'))
    continue_sources('entity_reteach2', parent, request, 'reteach_two', sources)
    preserve_json(f'records/confirmation_recovery_ready_s{seed}_observed_blocking_v8.json',
                  {'status': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE', 'seed': seed, 'outcomes': outcomes,
                   'task_scope': [[label, file] for label, file in tasks],
                   'shared_parent_conditioning': True, 'noise_seed': 1729})


def continual_seed(seed, freezes, pilot, second):
    plan = copy.deepcopy(pilot['plan'])
    worlds = sorted(plan['worlds'])
    random.Random(seed).shuffle(worlds)
    plan.update(worlds=worlds, seed=seed, replay_seed=seed * 100 + 12, probe_seed=37,
                independent_phi_seed=seed, source_seed17_pilot_selection_is_exploratory=True)
    path = f'records/confirmation_continual_schedule_s{seed}_observed_blocking_v8.json'
    preserve_json(path, plan)
    outcomes = []
    for org in ['O1', second]:
        for method in ['clean_Adam', 'noise_Adam', 'clean_phi', 'noise_phi']:
            source = next(r for r in pilot['outcomes'] if r['organization'] == org and r['method'] == method)
            c = copy.deepcopy(source['result']['config'])
            f = json.loads(Path(freezes[org]).read_text())
            c.update(freeze_manifest=freezes[org], schedule_manifest=path, worlds=worlds,
                     seed=seed, replay_seed=plan['replay_seed'], probe_seed=plan['probe_seed'])
            if c['night_method'] == 'phi':
                c['phi'] = f['phi']
            data = [c['parent_data'], *worlds, *c['probe_worlds'],
                    'data/language/inner.jsonl', c['language_eval']]
            result = entry(f'confirm_continual_{org}_{method}_8round_s{seed}_observed_blocking_v8',
                           c, 'continual', seed, [c['parent'], path, f['phi']], data, [freezes[org]])
            if result['completed_rounds'] != 8:
                raise ValueError('Independent continual run ended before eight rounds')
            outcomes.append({'organization': org, 'method': method, 'run': result['config']['out'], 'result': result})
    preserve_json(f'records/confirmation_continual_ready_s{seed}_observed_blocking_v8.json',
                  {'status': 'INDEPENDENT_EIGHT_ROUND_CONFIRMATION_COMPLETE', 'seed': seed,
                   'schedule': path, 'outcomes': outcomes, 'organizations': ['O1', second],
                   'base_reference_shared_from_pilot': pilot['base_reference'], 'shared_parent_conditioning': True})


def main():
    preserve_json('records/confirmation_policy_v1.json', POLICY)
    if json.loads(Path('checks/confirmation_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Independent confirmation entry/schedule checks must pass first')
    pilot = wait_for('records/continual_pilot_ready_v1.json', 'continual_queue_observed_blocking_v8')
    selection = select_second(pilot)
    preserve_json('records/confirmation_organization_selected_v1.json', selection)
    second = selection['organization']
    selected = json.loads(Path('records/phi_architecture_selected.json').read_text())
    all_frozen = json.loads(Path('records/all_phi_frozen.json').read_text())
    original = json.loads(Path(all_frozen['manifests']['O1']).read_text())
    ready = json.loads(Path('records/final_parents_ready.json').read_text())
    factor = json.loads(Path('records/factor_recovery_frozen_v1.json').read_text())
    entity = json.loads(Path('records/entity_reflow_parents_ready_v1.json').read_text())
    rates = json.loads(Path('records/reflow_lrs_frozen_v1.json').read_text())
    if (not ready['matched_R_exposure'] or not ready['phi_frozen_before_training']
            or factor['formal_data_used'] or rates['formal_data_used']
            or factor['selection_K'] != original['recovery_K'] or rates['recovery_K'] != original['recovery_K']):
        raise ValueError('Confirmations require intact original frozen settings and matched parents')
    for seed in POLICY['independent_phi_seeds']:
        freezes = train_seed(seed, selected, original, second)
        evaluate_seed(seed, freezes, ready, original, factor, entity, rates)
        continual_seed(seed, freezes, pilot, second)
    preserve_json('records/confirmation_ready_v1.json',
                  {'status': 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE', 'policy': POLICY,
                   'seeds': POLICY['independent_phi_seeds'], 'organization_selection': selection,
                   'recovery_records': [f'records/confirmation_recovery_ready_s{s}_v1.json' for s in [29, 43]],
                   'continual_records': [f'records/confirmation_continual_ready_s{s}_v1.json' for s in [29, 43]],
                   'shared_parent_conditioning': True, 'independent_phi_training_seeds_including_primary': 3,
                   'overall_experiment_completion': False})


if __name__ == '__main__':
    main()
