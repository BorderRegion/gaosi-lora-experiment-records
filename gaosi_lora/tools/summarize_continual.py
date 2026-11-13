"""Audit all eight-round trajectories and paired common-rank capacity probes."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import hierarchical_contrast, keyed, read_json
from gaosi.selective_statistics import predictions, remember


METHODS = ['clean_Adam', 'noise_Adam', 'clean_phi', 'noise_phi']
COMPARISONS = [('clean_phi', 'clean_Adam'), ('noise_phi', 'noise_Adam'),
               ('noise_phi', 'clean_phi'), ('noise_Adam', 'clean_Adam')]
POLICY = {
    'version': 1,
    'scope': 'Sixteen eight-round organization pilots and O1 plus the explicitly exploratory selected second organization, seeds29/43. Common extra rank16 probes and one shared W0 reference included.',
    'curves': 'Raw history, parent-library, seen-stream, current day/night, language, forgetting and costs by round. Early-round order differs across seeds; no pooled early-round entity interval or claim that rounds are independent trials.',
    'inference': 'For final round8 parent-library/stream endpoints, and per-world acquisition-to-final forgetting: paired hierarchical phi/order seed then world/entity bootstrap,2000 draws/seed7719. Same actual world/query IDs across seeds, worlds equally weighted. Seed17 pilot, seeds29/43 confirmation and combined exploratory summaries remain separate.',
    'probes': 'At rounds0/4/8 each organization receives the same extra scratch rank16, four disjoint fixed worlds,1/4/16 steps. Pair by world/entity and identical probe seed/data/rate. This measures new-adapter learnability, not remaining native adapter rank. W0 measured once, reused without independent-replicate claims.',
    'selection': 'Selected second organization must match the predeclared largest absolute pilot contrast, including negative results. Selection is exploratory; no formal outcomes tune training/rates/budgets.',
    'cost': 'Day/night actual trainable/total adapter parameters, per-step time/tokens/memory, checkpoint and adapter bytes, inference samples, O4 merge rounding/FP32-master/cumulative rank exported. O4 base change is expected and never treated as a frozen-W0 result.',
    'limits': 'Three seeds describe phi/support/order variability conditional on shared parents and fixed tasks. No p-values, automatic superiority labels or indefinite-learning claim. Conditional16/32-round and real-text decisions remain pending.',
    'settings_selected_by_summary': False,
}


def rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def task(path, provenance, all_queries=False):
    remember(path, provenance)
    data = rows(path)
    query = keyed([r for r in data if r['role'] == 'query' and (all_queries or r['sample_id'].endswith(':0'))])
    support = {r['fact_id'] for r in data if r['role'] == 'support'}
    if ({r['fact_id'] for r in query.values()} != support
            or any(r['kind'] != 'fact' or not r.get('entity') for r in query.values())):
        raise ValueError('Continual tasks require fully queried fact/entity support identities')
    return query, support


def indexed(events, name, expected):
    found = [e for e in events if e['event'] == name]
    if [e['round'] for e in found] != list(expected):
        raise ValueError('Missing/duplicated or unordered ' + name + ' rounds')
    return {e['round']: e for e in found}


def read_probes(root, prefix, worlds, evidence, provenance):
    if len(evidence) != 4 or [p['world'] for p in evidence] != worlds:
        raise ValueError('Common capacity probes must use all four fixed worlds in order')
    result = {}
    for i, record in enumerate(evidence):
        if record['rank'] != 16 or [m['steps'] for m in record['curves']] != [1, 4, 16]:
            raise ValueError('Common probes require exactly rank16 and steps1/4/16')
        query, _ = task(record['world'], provenance, all_queries=True)
        for m in record['curves']:
            result[(record['world'], m['steps'])] = {
                'queries': query,
                'predictions': predictions(root / prefix / f'fresh_{i}_k{m["steps"]}.jsonl', query, m, provenance),
                'metrics': m}
    return result


def load_run(path, seed, organization, method, provenance):
    root = Path(path)
    c, r = read_json(root / 'config.json'), read_json(root / 'result.json')
    if (r['status'] != 'PASS' or r['config'] != c or c['out'] != str(root) or c['phase'] != 'formal'
            or r['completed_rounds'] != 8 or c['rounds'] != 8 or r['replay_capacity'] != 128
            or not r['phi_frozen'] or r['total_adapter_rank'] != 32 or c['organization'] != organization
            or c['seed'] != seed or method not in METHODS
            or c['night_method'] != ('phi' if method.endswith('phi') else 'standard_adam')
            or r['base_unchanged'] != (organization != 'O4')):
        raise ValueError('Need a complete declared organization-specific eight-round trajectory')
    freeze = read_json(c['freeze_manifest']); plan = read_json(c['schedule_manifest'])
    if (freeze['status'] != 'FROZEN' or freeze['formal_data_used'] or not freeze['stopping_rule_frozen']
            or freeze['organization'] != organization or freeze['training_noise'] != 'mixed'
            or c['architecture'] != freeze['architecture']
            or remember(freeze['phi'], provenance) != freeze['phi_sha256']
            or c['q_apply'] != (freeze['q_medium'] if method.startswith('noise') else 0.)
            or method.endswith('phi') and c['phi'] != freeze['phi']
            or c['day_lr'] != freeze['capacity_lr'] or c['night_lr'] != freeze['recovery_lr']
            or plan['status'] != 'FROZEN_CONTINUAL_SCHEDULE' or not plan['phi_frozen_before_selection']
            or not plan['stream_probe_parent_bindings_disjoint']):
        raise ValueError('Continual run differs from intact frozen phi/dose/rate/data policy')
    shared = ['parent', 'parent_data', 'worlds', 'probe_worlds', 'day_steps', 'night_steps', 'day_lr', 'night_lr',
              'replay_seed', 'probe_seed', 'seed', 'seq', 'effective_tokens', 'language_eval', 'language_eval_tokens',
              'measure_inference', 'inference_repetitions']
    if (any(c[k] != plan[k] for k in shared) or len(c['worlds']) != 8 or len(c['probe_worlds']) != 4
            or len(set(c['worlds'] + c['probe_worlds'])) != 12 or not c['measure_inference']):
        raise ValueError('Continual schedule/data/support settings changed')
    if seed != 17 and (freeze.get('independent_phi_seed') != seed or c.get('confirmation_seed') != seed):
        raise ValueError('Continual confirmation lacks an independent trained phi identity')
    for file, digest in plan['data_sha256'].items():
        if remember(file, provenance) != digest:
            raise ValueError('Continual data bytes differ from frozen schedule')
    tasks = {}; bindings = {}; seen = set()
    for file in [c['parent_data'], *c['worlds'], *c['probe_worlds']]:
        query, support = task(file, provenance)
        if len(support) != (512 if file == c['parent_data'] else 32) or support & seen:
            raise ValueError('Continual parent/stream/probe bindings overlap or have wrong sizes')
        seen |= support; tasks[file] = query; bindings[file] = support
    events = rows(root / 'events.jsonl')
    history = indexed(events, 'history_evaluation', range(9))
    day = indexed(events, 'new_day_evaluation', range(1, 9))
    night = indexed(events, 'new_night_evaluation', range(1, 9))
    day_done = indexed(events, 'day_complete', range(1, 9))
    night_done = indexed(events, 'night_complete', range(1, 9))
    day_start = indexed(events, 'day_start', range(1, 9))
    night_start = indexed(events, 'night_start', range(1, 9))
    costs = indexed(events, 'inference_cost', [0, 4, 8])
    capacity = indexed(events, 'fresh_capacity', [0, 4, 8])
    storage = indexed(events, 'checkpoint_storage', range(1, 9))
    history_predictions, current, updates = {}, {}, {}
    known = bindings[c['parent_data']].copy()
    for round_number in range(9):
        if round_number:
            world = c['worlds'][round_number - 1]; known |= bindings[world]
            selected = sorted(known, key=lambda key: hashlib.sha256(f'{c["replay_seed"]}:{key}'.encode()).hexdigest())[:128]
            if (night_done[round_number]['replay_ids'] != selected
                    or set(night_done[round_number]['replay_coverage']['covered']) != set(selected)
                    or set(day_done[round_number]['fact_stream']['covered']) != bindings[world]):
                raise ValueError('Replay must be the exact128 current/past reservoir; all day/night facts covered')
            d, n = day_start[round_number]['model'], night_start[round_number]['model']
            if (d['rank'] != 32 or n['rank'] != 32 or d['adapter_parameters'] != n['adapter_parameters']
                    or organization == 'O3' and 2*d['trainable_parameters'] != n['trainable_parameters']
                    or organization in ['O1', 'O3', 'O4'] and n['trainable_parameters'] != n['adapter_parameters']
                    or organization == 'O2' and not d['trainable_parameters'] == n['trainable_parameters'] < n['adapter_parameters']):
                raise ValueError('Organization day/night actual trainable parameter counts are inconsistent')
            for phase in ['day', 'night']:
                seq = [e for e in events if e['event'] == phase and e['round'] == round_number]
                if ([e['k'] for e in seq] != list(range(1, c[phase + '_steps'] + 1))
                        or any(e['task_tokens'] < c['effective_tokens'] or e['language_tokens'] < c['effective_tokens']
                               or e['cpu_rss_bytes'] <= 0 or 'peak_allocated_bytes' not in e for e in seq)):
                    raise ValueError('Incomplete day/night update/token/memory evidence')
                updates[(round_number, phase)] = seq
            current[round_number] = {phase: predictions(root / f'new_{phase}_round_{round_number:02d}.jsonl',
                                                       tasks[world], metric[round_number]['metrics'], provenance)
                                     for phase, metric in [('day', day), ('night', night)]}
        source = {key: row for file in [c['parent_data'], *c['worlds'][:round_number]] for key, row in tasks[file].items()}
        history_predictions[round_number] = predictions(root / f'history_round_{round_number:02d}.jsonl', source,
                                                       history[round_number], provenance)
    probes = {}
    for round_number in [0, 4, 8]:
        e = capacity[round_number]
        if e['common_extra_rank'] != 16:
            raise ValueError('Capacity measurement changed common scratch rank')
        probes[round_number] = read_probes(root, Path(f'probe_round_{round_number}'), c['probe_worlds'], e['worlds'], provenance)
        cost = costs[round_number]
        measurement = cost['measurement']
        if (not measurement['same_prompt_same_underlying_base'] or measurement['warmups'] != 2
                or measurement['repetitions'] != 5
                or any(len(t['seconds']) != 5 or min(t['seconds']) <= 0 for t in measurement['timings'].values())
                or cost['merge_count'] != (round_number+1 if organization == 'O4' else 0)
                or cost['cumulative_merge_rank_upper_bound'] != (32*(round_number+1) if organization == 'O4' else 0)):
            raise ValueError('Inference/merge cost evidence differs from the declared organization policy')
    merges = [e for e in events if e['event'] in ['merge_initial_history', 'merge']]
    if organization == 'O4':
        if ([e['merge_count'] for e in merges] != list(range(1, 10))
                or any(not e['adapter_reset'] or not e['KV_cache_discarded'] or e['working_BF16_rounding_sq'] < 0
                       for e in merges) or r['cumulative_merge_rank_upper_bound'] != 288):
            raise ValueError('O4 must retain all nine FP32 merge/reset/rounding records')
    elif merges or r['cumulative_merge_rank_upper_bound'] != 0:
        raise ValueError('Fixed-base organizations cannot contain hidden merges')
    for p in [root / 'config.json', root / 'result.json', root / 'events.jsonl', c['freeze_manifest'], c['schedule_manifest']]:
        remember(p, provenance)
    return dict(config=c, result=r, tasks=tasks, history=history, day=day, night=night,
                history_predictions=history_predictions, current=current, probes=probes,
                updates=updates, costs=costs, storage=storage, merges=merges,
                stage_models={'day': day_start, 'night': night_start})


def verify_pair(left, right):
    fields = ['parent', 'parent_data', 'organization', 'worlds', 'probe_worlds', 'day_steps', 'night_steps', 'day_lr',
              'night_lr', 'seed', 'replay_seed', 'probe_seed', 'seq', 'effective_tokens', 'language_eval', 'language_eval_tokens']
    if any(left['config'][k] != right['config'][k] for k in fields) or left['tasks'] != right['tasks']:
        raise ValueError('Organization contrasts require identical within-seed schedule/data/support settings')
    for k in left['updates']:
        if any((a['task_tokens'], a['language_tokens']) != (b['task_tokens'], b['language_tokens'])
               for a, b in zip(left['updates'][k], right['updates'][k])):
            raise ValueError('Organization methods must share actual task/language token exposure')


def clusters(query, left, right, metric):
    field = {'candidate_accuracy': 'rank_correct', 'strict_EM': 'strict_EM'}[metric]
    result = {}
    for sample, row in query.items():
        result.setdefault(row['entity'], []).append(left[sample][field] - right[sample][field])
    return {entity: mean(values) for entity, values in result.items()}


def endpoint(left, right, metric, scope):
    verify_pair(left, right)
    c = left['config']
    files = [c['parent_data']] if scope == 'parent' else c['worlds']
    result = {}
    for file in files:
        values = clusters(left['tasks'][file], left['history_predictions'][8], right['history_predictions'][8], metric)
        if scope == 'forgetting':
            round_number = c['worlds'].index(file) + 1
            acquired = clusters(left['tasks'][file], left['current'][round_number]['night'],
                                right['current'][round_number]['night'], metric)
            # Half difference-of-change stays within bootstrap helper's [-1,1] range.
            values = {e: (v-acquired[e])/2 for e, v in values.items()}
        result[file] = values
    return {'parent' if scope == 'parent' else 'stream': result}


def bootstrap(data, draws, double=False):
    r = hierarchical_contrast(data, draws=draws)
    r.pop('positive_favors_left_method')
    if double:
        r['difference'] *= 2
        r['per_phi_seed'] = {k: v*2 for k, v in r['per_phi_seed'].items()}
        if r['percentile_interval95']:
            r['percentile_interval95'] = [v*2 for v in r['percentile_interval95']]
    r['automatic_success_label'] = False
    return r


def collect(stage):
    provenance = {}
    def metadata(path):
        remember(path, provenance)
        return read_json(path)
    pilot = metadata('records/continual_pilot_ready_v1.json')
    if pilot['status'] != 'SIXTEEN_EIGHT_ROUND_PILOTS_COMPLETE':
        raise ValueError('Need all sixteen complete organization pilots')
    parents = metadata('records/final_parents_ready.json')
    if (not parents['matched_R_exposure'] or not parents['phi_frozen_before_training']
            or not parents['parents']['all']['acquisition_gate_passed']
            or not parents['parents']['all']['base_unchanged']
            or parents['parents']['all']['checkpoint'] != pilot['plan']['parent']):
        raise ValueError('Continual library needs an acquired post-freeze final parent')
    outcomes = [(17, row) for row in pilot['outcomes']]
    expected = {(17, org, method) for org in ['O1', 'O2', 'O3', 'O4'] for method in METHODS}
    second = None
    if stage == 'full':
        ready = metadata('records/confirmation_ready_v1.json')
        if ready['status'] != 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE' or ready['seeds'] != [29, 43]:
            raise ValueError('Need both independent organization/order confirmations')
        from confirmation_queue import select_second
        selection = select_second(pilot)
        if ready['organization_selection'] != selection:
            raise ValueError('Second organization differs from the predeclared exploratory pilot selection')
        second = selection['organization']
        for seed, path in zip([29, 43], ready['continual_records']):
            r = metadata(path)
            if (r['status'] != 'INDEPENDENT_EIGHT_ROUND_CONFIRMATION_COMPLETE' or r['seed'] != seed
                    or r['organizations'] != ['O1', second] or not r['shared_parent_conditioning']):
                raise ValueError('Independent continual confirmation identity differs')
            outcomes.extend((seed, row) for row in r['outcomes'])
            expected |= {(seed, org, method) for org in ['O1', second] for method in METHODS}
    evidence = {}
    for seed, row in outcomes:
        key = (seed, row['organization'], row['method'])
        if key in evidence:
            raise ValueError('Duplicate continual organization/method/seed cell')
        evidence[key] = load_run(row['run'], *key, provenance)
    if evidence.keys() != expected:
        raise ValueError('Missing/extra continual organization/method/seed cells')
    shared = pilot['base_reference']; root = Path(shared['config']['out'])
    base = metadata(root / 'result.json')
    c = metadata(root / 'config.json')
    if (base != shared or base['status'] != 'PASS' or base['config'] != c or not base['base_unchanged']
            or base['common_extra_rank'] != 16 or base['steps'] != [1, 4, 16]
            or not base['shared_reference'] or base['repeated_as_independent_0_4_8_measurements']
            or c['probe_worlds'] != pilot['plan']['probe_worlds']):
        raise ValueError('W0 reference must be one shared unchanged-base rank16 probe measurement')
    base_probes = read_probes(root, Path('rank16'), c['probe_worlds'], base['probes'], provenance)
    for key, run in evidence.items():
        reference = evidence[(17, key[1], key[2])]
        invariant = ['parent', 'parent_data', 'day_steps', 'night_steps', 'day_lr', 'night_lr',
                     'probe_worlds', 'probe_seed', 'seq', 'effective_tokens', 'architecture',
                     'language_eval', 'language_eval_tokens', 'q_apply']
        if (set(run['config']['worlds']) != set(pilot['plan']['worlds'])
                or run['config']['parent'] != pilot['plan']['parent']
                or run['config']['probe_worlds'] != pilot['plan']['probe_worlds']
                or run['config']['probe_seed'] != pilot['plan']['probe_seed']
                or any(run['config'][k] != reference['config'][k] for k in invariant)):
            raise ValueError('All seeds must share actual task/probe/parent identities')
        if run['result']['base_digest'] != base['base_digest']:
            raise ValueError('Continual/reference original base identities differ')
        verify_pair(run, evidence[(key[0], key[1], 'clean_Adam')])
    return evidence, base, base_probes, second, provenance


def summarize(stage, draws=2000):
    evidence, base, base_probes, second, provenance = collect(stage)
    scopes = [('seed17_pilots', [17], ['O1', 'O2', 'O3', 'O4'])]
    if stage == 'full':
        scopes += [('independent_confirmations', [29, 43], ['O1', second]),
                   ('all_three_exploratory', [17, 29, 43], ['O1', second])]
    contrasts = []
    for label, seeds, organizations in scopes:
        for org in organizations:
            for left, right in COMPARISONS:
                for metric in ['candidate_accuracy', 'strict_EM']:
                    for scope in ['parent', 'stream', 'forgetting']:
                        data = {s: endpoint(evidence[(s, org, left)], evidence[(s, org, right)], metric, scope) for s in seeds}
                        contrasts.append(dict(scope=label, organization=org, left=left, right=right, metric=metric,
                                              measurement=scope + '_round8', **bootstrap(data, draws, scope == 'forgetting')))
            for left, right in [*COMPARISONS, *[(m, 'W0_shared') for m in METHODS]]:
                for round_number in [0, 4, 8]:
                    for step in [1, 4, 16]:
                        for metric in ['candidate_accuracy', 'strict_EM']:
                            data = {}
                            for seed in seeds:
                                a = evidence[(seed, org, left)]
                                b = evidence[(seed, org, right)] if right != 'W0_shared' else None
                                if b:
                                    verify_pair(a, b)
                                worlds = {}
                                for world in a['config']['probe_worlds']:
                                    x = a['probes'][round_number][(world, step)]
                                    y = b['probes'][round_number][(world, step)] if b else base_probes[(world, step)]
                                    if x['queries'] != y['queries']:
                                        raise ValueError('Common scratch probe query IDs differ')
                                    worlds[world] = clusters(x['queries'], x['predictions'], y['predictions'], metric)
                                data[seed] = {'probe_worlds': worlds}
                            contrasts.append(dict(scope=label, organization=org, left=left, right=right, metric=metric,
                                                  measurement='common_rank16', round=round_number, probe_steps=step,
                                                  **bootstrap(data, draws)))
    curves, costs, probe_curves = [], [], []
    for (seed, org, method), r in evidence.items():
        identity = {'seed': seed, 'organization': org, 'method': method, 'run': r['config']['out']}
        for round_number in range(9):
            row = dict(identity, round=round_number, **{k: v for k, v in r['history'][round_number].items()
                                                      if k not in ['event', 'utc', 'round', 'language']})
            row['language_nll'] = r['history'][round_number]['language']['nll']
            for scope, files in [('parent', [r['config']['parent_data']]), ('seen_stream', r['config']['worlds'][:round_number])]:
                chosen = [r['history_predictions'][round_number][k] for file in files for k in r['tasks'][file]]
                row[scope + '_n'] = len(chosen)
                for field in ['rank_correct', 'strict_EM']:
                    row[scope + '_' + field] = mean(p[field] for p in chosen) if chosen else None
            if round_number:
                for phase in ['day', 'night']:
                    row.update({f'new_{phase}_{k}': v for k, v in r[phase][round_number]['metrics'].items()})
                    events = r['updates'][(round_number, phase)]
                    costs.append(dict(identity, round=round_number, phase=phase, steps=len(events),
                                      seconds=sum(e['seconds'] for e in events),
                                      model=r['stage_models'][phase][round_number]['model'],
                                      task_tokens=sum(e['task_tokens'] for e in events),
                                      language_tokens=sum(e['language_tokens'] for e in events),
                                      peak_allocated_bytes=max(e['peak_allocated_bytes'] for e in events),
                                      cpu_rss_bytes=max(e['cpu_rss_bytes'] for e in events)))
                costs.append(dict(identity, round=round_number, phase='checkpoint', evidence=r['storage'][round_number]))
            curves.append(row)
        for round_number, event in r['costs'].items():
            costs.append(dict(identity, round=round_number, phase='inference', evidence=event))
        for event in r['merges']:
            costs.append(dict(identity, round=event.get('round', 0), phase='merge', evidence=event))
        for round_number, probes in r['probes'].items():
            for (world, step), p in probes.items():
                probe_curves.append(dict(identity, round=round_number, world=world, **p['metrics']))
    return {'status': 'PASS', 'summary_type': 'CONTINUAL_AND_COMMON_RANK16_SUMMARY', 'stage': stage,
            'policy': POLICY, 'run_count': len(evidence), 'selected_second_organization': second,
            'contrasts': contrasts, 'raw_curves': curves, 'costs': costs, 'probe_curves': probe_curves,
            'shared_W0_reference': base, 'input_sha256': provenance, 'bootstrap_draws': draws,
            'analysis_source_sha256': {str(p): remember(p, {}) for p in [Path(__file__).resolve(),
                                      Path(__file__).resolve().parents[1] / 'gaosi/recovery_statistics.py',
                                      Path(__file__).resolve().parents[1] / 'gaosi/selective_statistics.py']},
            'overall_experiment_complete': False,
            'remaining': ['7B/noise/horizon/boundary reports', 'conditional real text and16/32-round decisions', 'full protocol audit']}


def write_summary(result, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    for name in ['contrasts', 'raw_curves', 'costs', 'probe_curves']:
        data = [{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in result[name]]
        with (out / f'{name}.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for r in data for k in r})); w.writeheader(); w.writerows(data)


def main():
    p = argparse.ArgumentParser(); p.add_argument('--stage', choices=['primary', 'full'], required=True)
    p.add_argument('--out', required=True); args = p.parse_args()
    r = summarize(args.stage); write_summary(r, args.out)
    print(json.dumps({'status': r['status'], 'run_count': r['run_count'], 'out': args.out}))


if __name__ == '__main__':
    main()
