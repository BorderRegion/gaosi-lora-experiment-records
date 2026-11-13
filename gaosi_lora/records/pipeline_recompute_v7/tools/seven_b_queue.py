"""7B frozen-rule transfer and local adaptation, without final-score selection."""
import argparse
import copy
import datetime as dt
import json
from pathlib import Path

from organization_queue import wait_for, fit_parent
from phi_core_queue import run_job
from phi_development_queue import preserve_json
from formal_recovery_queue import intact_manifest

# This module is also imported by CPU tests without importing torch.
import sys
sys.path.insert(0, str(Path.cwd()))
from gaosi.scale import sha256, check_config, valid_freeze


POLICY = {
    'version': 1,
    'prerequisites': 'All 3B phi frozen and successful early 7B acquisition/ordinary recovery; no 3B superiority condition.',
    'formal_worlds': 'First four facts and first four integer-affine rules sorted by world_id, all final split.',
    'development_worlds': 'Known dev library and first two facts / first two rules, equal known/fact/rule weight.',
    'transfer_methods': ['standard_adam', 'paired_adam', 'phi_mix', 'phi_clean'],
    'q_apply': ['zero', '3b_medium'],
    'dose_scope': 'Transferred development doses, not matched functional corruption across model sizes.',
    'primary_K': 'Actual 3B-development-frozen horizon, same for every frozen/local comparison.',
    'seeds': {'data': 17, 'noise': 1729, 'meta': 17},
    'meta_parents': 'Independent meta-A and meta-B 7B ordinary LoRA parents, LR1e-4 from early development, 256 then a matched continuation to 512 if either gate fails.',
    'local_initialization': 'Corresponding frozen 3B O1 mixed and clean phi, including its meta-optimizer; matching 7B schedules.',
    'local_budget': 'Both 64x8=512, then both +128x16=2560 regardless of formal outcomes. Evaluate 512, 2048 and 2560 on development only.',
    'local_extension': 'If either mixed/clean improves development score by >.01 from 2048 to 2560, extend BOTH once by 64x16 to3584. No final-result stopping or tuning.',
    'local_score': 'Equal known/fact/rule group mean of (candidate+strictEM)/2 minus keep/language residual over .1 nats; doses equal.',
    'backend': 'Try real 7B three-support exact H2 at512. Only documented OOM or missing higher-order operator permits H1 with a recorded scope limitation; unknown failures stop for diagnosis.',
    'pairing': 'One saved canonical noisy adapter per dose reused byte-for-byte by every world/method/local stage.',
    'formal_language': 'final split; dev results use dev language; inner and meta-query stay disjoint.',
    'claim': 'One independent phi training seed and one 8-world scale pilot. Budget completion alone is not proof of convergence.',
    'formal_data_can_select_settings': False,
}


def select_worlds(manifest, split, count):
    result = []
    for kind in ['fact', 'affine_integer']:
        group = sorted([w for w in manifest['worlds'] if w['split'] == split and w['kind'] == kind],
                       key=lambda w: w['world_id'])[:count]
        if len(group) != count:
            raise ValueError('Scale pilot lacks its predeclared worlds')
        result += [(kind, str(Path('data/synthetic') / w['file'])) for w in group]
    return result


def validate_isolation(parent_files, groups):
    """Check all meta/dev/final IDs and fact bindings before submitting jobs."""
    previous_worlds, previous_facts = set(), set()
    counts = {}
    for scope, paths in [('meta', parent_files['meta']), ('dev', [parent_files['dev']]), *groups.items()]:
        for path in paths:
            data = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
            world_ids = {r['world_id'] for r in data}
            facts = {r['fact_id'] for r in data if 'fact_id' in r}
            if (not data or any(not w.startswith(scope) for w in world_ids)
                    or previous_worlds & world_ids or previous_facts & facts):
                raise ValueError('7B meta/dev/final task worlds or fact bindings overlap')
            for role in ['support', 'query']:
                if not any(r['role'] == role for r in data):
                    raise ValueError('Scale task lacks support/query')
            if facts:
                supports = {r['fact_id'] for r in data if r['role'] == 'support'}
                queries = {r['fact_id'] for r in data if r['role'] == 'query'}
                if supports != queries:
                    raise ValueError('Scale fact task has missing query/support bindings')
            previous_worlds |= world_ids
            previous_facts |= facts
            counts[path] = {'worlds': len(world_ids), 'bindings': len(facts), 'rows': len(data)}
    return counts


def entry(name, config, action, upstream, data_files):
    c = copy.deepcopy(config)
    c.update(out='runs/' + name, scale_action=action,
             scale_entry_manifest=f'records/seven_b_entries/{name}.json')
    freeze_hashes = {}
    for file in upstream:
        valid_freeze(file)
        freeze_hashes[file] = sha256(file)
    artifacts = {}
    for key in ['freeze_manifest', 'initial_phi', 'initial_adapter']:
        if c.get(key):
            artifacts[c[key]] = sha256(c[key])
    parents = c.get('parents', [c.get('parent')])
    for path in parents:
        artifacts[path] = sha256(path)
    plan = {'status': 'FROZEN_SCALE_ENTRY', 'config': c, 'upstream_freezes': freeze_hashes,
            'data_sha256': {file: sha256(file) for file in sorted(set(data_files))},
            'artifact_sha256': artifacts}
    Path('records/seven_b_entries').mkdir(parents=True, exist_ok=True)
    preserve_json(c['scale_entry_manifest'], plan)
    check_config(c)
    return c


def execute(name, config, action, upstream, data_files):
    c = entry(name, config, action, upstream, data_files)
    file = Path('configs') / (name + '.json')
    preserve_json(file, c)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.scale', '--config', str(file)],
                     Path(c['out']) / 'result.json')
    if json.loads((Path(c['out']) / 'config.json').read_text()) != c:
        raise ValueError('Scale result configuration differs from its entry')
    return result


def backend_unavailable(result):
    if result['status'] == 'PASS':
        return None
    text = result.get('error', '') + '\n' + result.get('traceback', '')
    if 'out of memory' in text.lower() or 'OutOfMemoryError' in text:
        return 'H2_UNAVAILABLE_MEMORY'
    if ('derivative for' in text and 'not implemented' in text
            or 'double backward' in text and 'not supported' in text):
        return 'H2_UNAVAILABLE_BACKEND'
    raise RuntimeError('Unclassified 7B profile failure; inspect the preserved trace')


def profile(parent, architecture, horizon):
    name = f'profile_7b_{architecture}_{horizon}_three_routes_s17_v1'
    file = Path('checks') / (name + '.json')
    try:
        run_job(name, ['/usr/bin/python', '-u', 'tools/profile_transition.py',
                       '--checkpoint', parent, '--out', str(file), '--seq', '512',
                       '--effective-tokens', '512', '--architecture', architecture,
                       '--horizon', horizon, '--retain-facts'], file)
    except RuntimeError:
        if not file.exists():
            raise
    result = json.loads(file.read_text())
    if result['command']['checkpoint'] != parent or result['command']['horizon'] != horizon:
        raise ValueError('7B profile result belongs to another prerequisite')
    return result


def dev_score(results):
    grouped = {}
    for group, result in results:
        final, clean = result['final'], result['clean']
        task = final['task']
        score = .5 * (task['candidate_accuracy'] + task['strict_EM'])
        for route in ['language', 'keep']:
            score -= max(0., final[route]['nll'] - clean[route]['nll'] - .1)
        grouped.setdefault(group, []).append(score)
    if set(grouped) != {'known', 'fact', 'affine_integer'}:
        raise ValueError('Scale development must cover known, fact and rule groups')
    return sum(sum(v) / len(v) for v in grouped.values()) / 3


def needs_extension(scores):
    return any(scores[f'{kind}_2560'] > scores[f'{kind}_2048'] + .01
               for kind in ['mixed', 'clean'])


def train_meta_parents(early_parent, meta3):
    results = []
    configs = []
    for i, source in enumerate(meta3):
        c = copy.deepcopy(early_parent['config'])
        for key in ['out', 'continue_from', 'parent_layout', 'parent_mode', 'target_ids', 'phi_freeze_manifest']:
            c.pop(key, None)
        c.update(data=source['config']['data'], lr=1e-4, ba_ratio=1., steps=256,
                 eval_steps=[256], eval_limit=0, stop_on_acquisition=False, max_epochs=0,
                 language_eval_tokens=4096, purpose='7B_meta_parent_not_development_or_final')
        configs.append(c)
        results.append(fit_parent(f'parent_7b_meta_{"ab"[i]}_256_s17_v1', c))
    if not all(r['acquisition_gate_passed'] for r in results):
        for i, (c, previous) in enumerate(zip(configs, results)):
            c = dict(c, continue_from=previous['checkpoint'], steps=512, eval_steps=[512],
                     purpose='single_matched_7B_meta_parent_extension')
            results[i] = fit_parent(f'parent_7b_meta_{"ab"[i]}_continue512_s17_v1', c)
    for r in results:
        events = [json.loads(s) for s in (Path(r['config']['out']) / 'events.jsonl').read_text().splitlines()]
        last = [e for e in events if e['event'] == 'evaluation'][-1]
        if (not r['acquisition_gate_passed'] or not r['base_unchanged']
                or len(r['fact_stream']['covered']) != 512 or last['n'] != 512):
            raise ValueError('7B local meta parent did not pass complete acquisition')
    preserve_json('records/seven_b_meta_parents_ready_v1.json',
                  {'status': 'PASS', 'parents': results, 'cost_is_additional_to_phi_budget': True})
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--freeze-job', default='organization_queue_recompute_v7')
    parser.add_argument('--early-job', default='seven_b_early_queue_v1')
    args = parser.parse_args()
    preserve_json('records/seven_b_policy_v1.json', POLICY)
    if json.loads(Path('checks/seven_b_schedule_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('7B scale entry integration must pass first')
    all_frozen = wait_for('records/all_phi_frozen.json', args.freeze_job)
    if all_frozen['status'] != 'ALL_PHI_FROZEN':
        raise ValueError('All phi must be frozen before 7B held-out selection')
    upstream = [all_frozen['manifests'][key] for key in ['O1', 'O1_clean']]
    freezes = {key: intact_manifest(file, key) for key, file in zip(['mixed', 'clean'], upstream)}
    mixed = freezes['mixed']
    early = wait_for('records/seven_b_early_ready_v1.json', args.early_job)
    if early['status'] != 'EARLY_7B_ORDINARY_RECOVERY_COMPLETE':
        raise ValueError('7B ordinary acquisition/recovery prerequisite is incomplete')
    parent = json.loads((Path(early['parent']).parent / 'result.json').read_text())
    if parent['config']['base_revision'] != 'd149729398750b98c0af14eb82c78cfe92750796':
        raise ValueError('Scale pilot requires the verified 7B revision')
    for key in ['rank', 'targets', 'scale']:
        if parent['config'][key] != mixed[key] or freezes['clean'][key] != mixed[key]:
            raise ValueError('7B scale pilot requires the frozen adapter configuration')
    for key in ['architecture', 'recovery_K', 'recovery_lr', 'q_medium']:
        if freezes['clean'][key] != mixed[key]:
            raise ValueError('Mixed/clean scale comparisons must share their frozen settings')
    manifest = json.loads(Path(mixed['world_manifest']).read_text())
    formal = select_worlds(manifest, 'final', 4)
    development = [('known', parent['config']['data'])] + select_worlds(manifest, 'dev', 2)
    meta3 = sorted([p for p in json.loads(Path('records/parents_ready.json').read_text())
                    if 'meta_parent_' in p['config']['data']], key=lambda p: p['config']['data'])
    if len(meta3) != 2:
        raise ValueError('Local 7B adaptation requires both independent meta parents')
    meta_files = [str(Path('data/synthetic') / w['file']) for w in manifest['worlds'] if w['split'] == 'meta']
    counts = validate_isolation({'meta': [p['config']['data'] for p in meta3],
                                'dev': parent['config']['data']},
                               {'meta': meta_files, 'dev': [p for _, p in development[1:]],
                                'final': [p for _, p in formal]})
    K = mixed['recovery_K']
    plan = {'status': 'SEVEN_B_SCALE_PLAN', 'policy': POLICY, 'formal_worlds': formal,
            'dev_worlds': development, 'parent': parent['checkpoint'],
            'primary_K': K, 'lr': mixed['recovery_lr'], 'q_medium': mixed['q_medium'],
            'data_sha256': {p: sha256(p) for p in counts}, 'binding_counts': counts,
            'settings_selected_from_formal_data': False, '3b_freezes': upstream}
    # Stable JSON representation for tuple/list values across restarts.
    preserve_json('records/seven_b_scale_plan_v1.json', json.loads(json.dumps(plan)))
    template = {'freeze_manifest': upstream[0], 'phase': 'formal', 'parent': parent['checkpoint'],
                'data_seed': 17, 'noise_seed': 1729, 'device': 'musa', 'q_apply': 0.,
                'noise_scheme': 'subspace', 'architecture': mixed['architecture'],
                'method': 'standard_adam', 'data': '', 'target_ids': [], 'task_layout': 'all',
                'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
                'retain_data': parent['config']['data'], 'language_eval': 'data/language/final.jsonl',
                'language_eval_tokens': 4096, 'inner_lr': mixed['recovery_lr'],
                'effective_tokens': 512, 'retain_weight': .25, 'K': K,
                'eval_steps': [k for k in [8, 16, 32, 64] if k <= K]}
    shared_initial = {}
    completed = []

    def evaluate_grid(stage, phi_files):
        outcomes = []
        for index, (group, data) in enumerate(formal):
            for dose, q in [('clean', 0.), ('medium', mixed['q_medium'])]:
                methods = [('standard_adam', None), ('paired_adam', None)] if stage == 'frozen3b' else []
                methods += [('phi_mix' if kind == 'mixed' else 'phi_clean', file)
                            for kind, file in phi_files.items()]
                for label, phi in methods:
                    kind = 'mixed' if label == 'phi_mix' else label.removeprefix('phi_')
                    freeze_file = upstream[1] if kind == 'clean' else upstream[0]
                    if stage != 'frozen3b':
                        freeze_file = f'records/seven_b_phi_{kind}_{stage}_frozen_v1.json'
                    c = dict(template, data=data, q_apply=q, freeze_manifest=freeze_file,
                             method='phi' if phi else label)
                    if phi:
                        c['phi'] = phi
                    if dose in shared_initial:
                        c['initial_adapter'] = shared_initial[dose]
                    name = f'scale7b_{stage}_{label}_w{index}_{dose}_s17_v1'
                    r = execute(name, c, 'recover', upstream + ([freeze_file] if stage != 'frozen3b' else []),
                                [data, c['retain_data'], c['language_eval'], 'data/language/inner.jsonl'])
                    if dose not in shared_initial:
                        shared_initial[dose] = str(Path(r['config']['out']) / 'initial.pt')
                    if not r['base_unchanged'] or r['coverage'] != r['pool']:
                        raise ValueError('7B endpoint lacks frozen base / complete task coverage')
                    outcomes.append({'stage': stage, 'kind': group, 'world': data, 'dose': dose,
                                     'method': label, 'run': r['config']['out'],
                                     'clean': r['clean'], 'initial': r['initial'], 'final': r['final'],
                                     'compute_counters': r['compute_counters']})
        preserve_json(f'records/seven_b_{stage}_outcomes_v1.json', {'status': 'PASS', 'outcomes': outcomes})
        completed.extend(outcomes)

    evaluate_grid('frozen3b', {k: f['phi'] for k, f in freezes.items()})
    parents = train_meta_parents(parent, meta3)
    preferred = mixed['horizon']
    prof = profile(parents[0]['checkpoint'], mixed['architecture'], preferred)
    limitation = backend_unavailable(prof)
    horizon = preferred
    if limitation:
        if preferred != 'EXACT_H2_tail':
            raise RuntimeError('7B H1 readiness failed; stop for diagnosis')
        horizon = 'H1'
        fallback = profile(parents[0]['checkpoint'], mixed['architecture'], horizon)
        if fallback['status'] != 'PASS':
            raise RuntimeError('7B H1 readiness also failed; inspect preserved evidence')
    preserve_json('records/seven_b_backend_ready_v1.json',
                  {'status': 'PASS', 'horizon': horizon, 'preferred_horizon': preferred,
                   'limitation': limitation, 'preferred_profile': prof})
    source_meta = json.loads((Path(mixed['phi']).parent / 'config.json').read_text())
    source_clean_meta = json.loads((Path(freezes['clean']['phi']).parent / 'config.json').read_text())
    if source_meta['meta_lr'] != source_clean_meta['meta_lr']:
        raise ValueError('Mixed/clean source meta-optimizer rates differ')
    core = {'model': parent['config']['model'], 'base_revision': parent['config']['base_revision'],
            'rank': mixed['rank'], 'targets': mixed['targets'], 'scale': mixed['scale'],
            'organization': 'O1', 'device': 'musa', 'architecture': mixed['architecture'], 'seed': 17,
            'parents': [r['checkpoint'] for r in parents],
            'parent_data': [r['config']['data'] for r in parents], 'known_warm_episodes': 0,
            'meta_lr': source_meta['meta_lr'], 'inner_lr': mixed['recovery_lr'], 'seq': 512, 'effective_tokens': 512,
            'retain_weight': .25, 'retain_fact_support': True, 'penalty': 1., 'chunk': 262144,
            'horizon': horizon, 'world_manifest': mixed['world_manifest'],
            'calibration_manifest': 'records/calibration_frozen.json',
            'load_initial_meta_optimizer': True}
    meta_sources = [*meta_files, *core['parent_data'], core['world_manifest'],
                    'data/language/inner.jsonl', 'data/language/meta_query.jsonl']
    paths = {}

    def train_local(kind, budget, initial, start, schedule):
        c = dict(core, q_cycle=[0.] if kind == 'clean' else [0., mixed['q_light'], mixed['q_medium']],
                 episode_K=schedule, initial_phi=initial, start_episode=start,
                 purpose=f'7B_{kind}_local_{budget}_predeclared_no_final_selection')
        r = execute(f'phi7b_{kind}_local{budget}_s17_v1', c, 'meta', upstream, meta_sources)
        if r['inner_updates'] != budget or not r['base_unchanged']:
            raise ValueError('7B local adaptation did not complete its declared budget')
        paths[f'{kind}_{budget}'] = r['checkpoint']
        return r

    def freeze_local(kind, stage, path, budget):
        frozen = dict(freezes[kind], phi=path, phi_sha256=sha256(path),
                      local_model='Qwen2.5-7B', local_inner_updates=budget, local_horizon=horizon,
                      horizon=horizon, source_3b_horizon=freezes[kind]['horizon'],
                      source_3b_training_inner_steps=freezes[kind]['total_training_inner_steps'],
                      total_training_inner_steps=freezes[kind]['total_training_inner_steps'] + budget,
                      no_sufficiency_claim_from_budget=True, settings_selected_from_formal_data=False)
        file = f'records/seven_b_phi_{kind}_{stage}_frozen_v1.json'
        preserve_json(file, frozen)

    for kind in ['mixed', 'clean']:
        r = train_local(kind, 512, freezes[kind]['phi'], 0, [8] * 64)
        freeze_local(kind, 'local512', r['checkpoint'], 512)
    evaluate_grid('local512', {k: paths[f'{k}_512'] for k in ['mixed', 'clean']})
    for kind in ['mixed', 'clean']:
        r = train_local(kind, 2560, paths[f'{kind}_512'], 64, [8] * 64 + [16] * 128)
        paths[f'{kind}_2048'] = str(Path(r['checkpoint']).parent / 'phi_2048.pt')

    def evaluate_dev(kind, budget):
        file = paths[f'{kind}_{budget}']
        devplan = {'status': 'PHI_DEVELOPMENT_PLAN', 'allowed_data': [p for _, p in development],
                   'allowed_parents': [parent['checkpoint']], 'allowed_phis': {file: sha256(file)},
                   'lr_candidates': [mixed['recovery_lr']], 'K_candidates': [K],
                   'q_candidates': [0., mixed['q_medium']], 'noise_schemes': ['subspace'],
                   'formal_data_used': False}
        planfile = f'records/seven_b_dev_{kind}_{budget}_plan_v1.json'
        preserve_json(planfile, devplan)
        outcomes = []
        for index, (group, data) in enumerate(development):
            for dose, q in [('clean', 0.), ('medium', mixed['q_medium'])]:
                c = dict(template, phase='phi_development', freeze_manifest=planfile, data=data,
                         language_eval='data/language/dev.jsonl', method='phi', phi=file, q_apply=q,
                         initial_adapter=shared_initial[dose])
                r = execute(f'scale7b_dev_{kind}_{budget}_t{index}_{dose}_s17_v1', c, 'recover', upstream,
                            [data, c['retain_data'], c['language_eval'], 'data/language/inner.jsonl'])
                outcomes.append((group, r))
        score = dev_score(outcomes)
        preserve_json(f'records/seven_b_dev_{kind}_{budget}_score_v1.json',
                      {'status': 'PASS', 'score': score, 'runs': [r['config']['out'] for _, r in outcomes]})
        return score

    scores = {f'{kind}_{budget}': evaluate_dev(kind, budget)
              for kind in ['mixed', 'clean'] for budget in [512, 2048, 2560]}
    extended = needs_extension(scores)
    end = 3584 if extended else 2560
    if extended:
        for kind in ['mixed', 'clean']:
            train_local(kind, 3584, paths[f'{kind}_2560'], 192, [8] * 64 + [16] * 192)
            scores[f'{kind}_3584'] = evaluate_dev(kind, 3584)
    budget_record = {'status': 'SEVEN_B_LOCAL_BUDGET_FROZEN', 'local_inner_updates': end,
                     'matched_extension': extended, 'dev_scores': scores, 'policy': POLICY,
                     'selected_from_formal_scores': False, 'convergence_proved': False}
    preserve_json('records/seven_b_local_budget_frozen_v1.json', budget_record)
    for kind in ['mixed', 'clean']:
        freeze_local(kind, f'local{end}', paths[f'{kind}_{end}'], end)
    evaluate_grid(f'local{end}', {k: paths[f'{k}_{end}'] for k in ['mixed', 'clean']})
    preserve_json('records/seven_b_scale_ready_v1.json',
                  {'status': 'SEVEN_B_FROZEN_AND_LOCAL_PILOT_COMPLETE',
                   'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'plan': plan,
                   'local_budget': budget_record, 'local_horizon': horizon, 'backend_limitation': limitation,
                   'independent_phi_training_seeds': 1, 'functional_damage_matched': False,
                   'outcomes': completed, 'not_full_experiment_completion': True})


if __name__ == '__main__':
    main()
