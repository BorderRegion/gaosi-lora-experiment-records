"""Read-only continuation evidence, paired by source corruption and support."""
from statistics import mean
import json
from pathlib import Path
import random

from .recovery_statistics import (average_corruptions, hierarchical_contrast,
                                  keyed, read_json)
from .selective_statistics import predictions, remember


POLICY = {
    'version': 1,
    'scope': 'Pure complete-R continuation20/80 and one fixed entity reteach-two/untaught-six diagnostic. Both development-frozen rates, primary-K sources only.',
    'contrasts': 'phi-Dminus minus Adam-Dminus at0/20/80, and paired change-from0 contrasts at20/80; each versus the one matched retain-only continuation reference. F/R and candidate/strict EM separate. Positive F change means more target answers, not selective success.',
    'pure_R_inference': 'Average three corruptions within entity for seed17. Independent phi seeds outside shared entity draws; 2000 percentile bootstrap draws/seed7719. Seed17, confirmations29/43 and all three reported separately on common noise1729.',
    'entity_inference': 'Exactly one outcome-independent target entity, two taught IDs fixed by seed91. Never resample its six untaught attributes as six independent entities. Collapse each query part to the fixed request before seed inference; seed17 has no population interval. Multi-seed intervals describe phi-seed variability conditional on that same request and shared parents.',
    'references': 'Matched retain-only is shared across source methods, fractions and corruptions within continuation seed/rate/request. It does not enlarge the number of independent phi trainings. Reference comparisons deliberately have different source adapters.',
    'audit': 'Recompute raw query metrics, full F/R and taught2/untaught6 identities, source checkpoint hashes, frozen phi/rates, exact Dminus primary endpoint, complete0/20/80 trajectory, selected-only support IDs and identical paired token exposures.',
    'interpretation': 'Report F/R/language jointly, including raw initial and20/80 curves, coverage and costs. No automatic success/erasure label. Pure adapter continuation cannot erase immutable W0.',
    'settings_selected_by_summary': False,
}


def read_rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def pools(config):
    data = read_rows(config['data'])
    query = keyed([r for r in data if r['role'] == 'query' and r['sample_id'].endswith(':0')])
    if (len(query) != 512 or len({r['fact_id'] for r in query.values()}) != 512
            or any(r['kind'] != 'fact' or not r.get('entity') for r in query.values())):
        raise ValueError('Reflow requires the complete512 fact query library')
    target = set(config['target_ids'])
    F = {k: r for k, r in query.items() if r['fact_id'] in target}
    R = {k: r for k, r in query.items() if r['fact_id'] not in target}
    if {r['fact_id'] for r in F.values()} != target:
        raise ValueError('Missing reflow target query identities')
    if config['mode'] == 'pure_R':
        if (len(F) != 64 or len(R) != 448 or config['taught_ids']
                or {r['entity'] for r in F.values()} != {r['entity'] for r in R.values()}):
            raise ValueError('Pure-R request must preserve full F64/R448 queries')
        selected = {r['fact_id'] for r in R.values()}
        result = {'F': F, 'R': R}
    elif config['mode'] == 'reteach_two':
        entity = min(r['entity'] for r in query.values())
        complete_entity = {r['fact_id'] for r in query.values() if r['entity'] == entity}
        taught = sorted(complete_entity)
        random.Random(91).shuffle(taught)
        taught = set(taught[:2])
        if (len(F) != 8 or len(R) != 504 or target != complete_entity
                or set(config['taught_ids']) != taught):
            raise ValueError('Need the fixed first entity and seed91 taught2/untaught6 request')
        result = {'F': F, 'R': R,
                  'taught_two': {k: r for k, r in F.items() if r['fact_id'] in taught},
                  'untaught_six': {k: r for k, r in F.items() if r['fact_id'] not in taught}}
        selected = taught
    else:
        raise ValueError('Unknown reflow request')
    support = {r['fact_id'] for r in data if r['role'] == 'support' and r['fact_id'] in selected}
    if support != selected:
        raise ValueError('Reflow training pool lacks selected support facts')
    return result, selected


def source_evidence(c, frozen, query, seed, provenance):
    root = Path(c['source_checkpoint']).parent
    result = read_json(root / 'result.json')
    s = read_json(root / 'config.json')
    if result['status'] != 'PASS' or result['config'] != s or not result['base_unchanged']:
        raise ValueError('Reflow source must be a complete unchanged-base result')
    if s['data'] != c['data'] or set(s['target_ids']) != set(c['target_ids']):
        raise ValueError('Reflow source data/request differs')
    remember(root / 'result.json', provenance)
    remember(root / 'config.json', provenance)
    if c['source_kind'] == 'retain_only':
        if s.get('parent_mode') != 'retain_only' or not result['acquisition_gate_passed']:
            raise ValueError('Reference requires an acquired matched retain-only source')
        return {'method': 'retain_only', 'noise': None, 'fraction': None,
                'source_config': s, 'updates': [], 'initial_sha256': None}
    if (c['source_kind'] != 'selective' or s['task_layout'] != 'selective' or s['phase'] != 'formal'
            or s['organization'] != 'O1' or s['D'] != 'minus' or s['parent'] != c['parent']
            or s['K'] != frozen['recovery_K'] or s['q_apply'] != frozen['q_medium']
            or s['noise_scheme'] != 'subspace' or s['inner_lr'] != frozen['recovery_lr']
            or s['method'] not in ['standard_adam', 'phi'] or s['data_seed'] != seed
            or s['freeze_manifest'] != c['freeze_manifest'] or not result['phi_frozen']
            or result['coverage'] != result['pool']):
        raise ValueError('Need the exact frozen primary-K medium-dose Dminus source')
    fraction = s['retain_subset_fraction']
    expected = round(len(query['R']) * fraction)
    if (fraction not in [1., .25] or result['pool'] != expected or result['coverage_slot'] != 'R'
            or c['mode'] == 'reteach_two' and fraction != 1.):
        raise ValueError('Dminus source support pool differs from its declared request')
    if s['method'] == 'phi' and s['phi'] != frozen['phi']:
        raise ValueError('Dminus phi source identity differs')
    if seed != 17 and s.get('confirmation_seed') != seed:
        raise ValueError('Dminus source lacks independent phi-seed identity')
    trace = read_rows(root / 'events.jsonl')
    updates = [e for e in trace if e['event'] == 'recover']
    endpoint = [e for e in trace if e['event'] == 'evaluation' and e['step'] == s['K']]
    if ([e['step'] for e in updates] != list(range(1, s['K'] + 1)) or len(endpoint) != 1
            or endpoint[0]['metrics'] != result['final']
            or any(e['route_tokens'].get('F', 0) != 0 for e in updates)):
        raise ValueError('Dminus source trace is incomplete or contains F support tokens')
    parsed = {part: predictions(root / f'eval_{s["K"]:04d}_{part}.jsonl', query[part],
                               result['final'][part], provenance) for part in ['F', 'R']}
    initial = s.get('initial_adapter', str(root / 'initial.pt'))
    remember(root / 'events.jsonl', provenance)
    return {'method': s['method'], 'noise': s['noise_seed'], 'fraction': fraction,
            'source_config': s, 'updates': updates, 'source_predictions': parsed,
            'initial_sha256': remember(initial, provenance)}


def load_reflow(path, seed, provenance):
    root = Path(path)
    r, c = read_json(root / 'result.json'), read_json(root / 'config.json')
    if (r['status'] != 'PASS' or r['config'] != c or c['out'] != str(root)
            or not r['base_unchanged'] or c['K'] != 80 or c['eval_steps'] != [20, 80]
            or c['data_seed'] != seed or r['coverage'] != r['pool']):
        raise ValueError('Need a complete declared80-step reflow trajectory')
    f, rates = read_json(c['freeze_manifest']), read_json(c['lr_freeze_manifest'])
    if (f['status'] != 'FROZEN' or not f['stopping_rule_frozen'] or f['formal_data_used']
            or f['organization'] != 'O1' or f['training_noise'] != 'mixed'
            or remember(f['phi'], provenance) != f['phi_sha256']
            or rates['status'] != 'REFLOW_LRS_FROZEN' or rates['formal_data_used']
            or rates.get('reflow_outcomes_used', False) or len(set(rates['lrs'])) != 2
            or c['inner_lr'] not in rates['lrs'] or rates['recovery_K'] != f['recovery_K']
            or rates['development_parent'] != f['development_parent']):
        raise ValueError('Reflow phi/rates must be intact and development-frozen')
    if seed != 17 and (f.get('independent_phi_seed') != seed or c.get('confirmation_seed') != seed):
        raise ValueError('Reflow confirmation requires its own trained phi identity')
    query, selected = pools(c)
    if r['pool'] != len(selected) or set(r['stream']['covered']) != selected:
        raise ValueError('Reflow must cover exactly the selected training facts')
    trace = read_rows(root / 'events.jsonl')
    policies = [e for e in trace if e['event'] == 'reflow_policy']
    updates = [e for e in trace if e['event'] == 'reflow']
    evaluations = [e for e in trace if e['event'] == 'evaluation']
    if ([e['step'] for e in updates] != list(range(1, 81))
            or [e['step'] for e in evaluations] != [0, 20, 80] or len(policies) != 1):
        raise ValueError('Missing or duplicated reflow updates/0-20-80 measurements')
    policy = policies[0]
    if (set(policy['training_ids']) != selected or policy['mode'] != c['mode']
            or any(policy[k] for k in ['additional_noise', 'additional_QR', 'phi_called'])
            or any(set(policy['full_query_ids'][part]) != {row['fact_id'] for row in source.values()}
                   for part, source in query.items())
            or any(e['support_tokens'] < c['effective_tokens'] for e in updates)):
        raise ValueError('Reflow used extra operations, wrong support IDs or wrong query pool')
    digest = remember(c['source_checkpoint'], provenance)
    if r['source_sha256'] != digest or policy['source_sha256'] != digest:
        raise ValueError('Reflow source checkpoint bytes changed')
    if evaluations[0]['metrics'] != r['initial'] or evaluations[-1]['metrics'] != r['final']:
        raise ValueError('Reflow initial/final result differs from its trace')
    curves, parsed = {}, {}
    for e in evaluations:
        step = e['step']
        curves[step] = e['metrics']
        if set(curves[step]) != set(query) | {'language'}:
            raise ValueError('Reflow query parts changed')
        parsed[step] = {part: predictions(root / f'eval_{step:04d}_{part}.jsonl', source,
                                          curves[step][part], provenance) for part, source in query.items()}
        for part in ['taught_two', 'untaught_six'] if c['mode'] == 'reteach_two' else []:
            if any(any(parsed[step][part][k][field] != parsed[step]['F'][k][field]
                       for field in ['rank_correct', 'strict_EM']) for k in query[part]):
                raise ValueError('Taught/untaught predictions must be the same F query observations')
    source = source_evidence(c, f, query, seed, provenance)
    if source['method'] != 'retain_only':
        for part in ['F', 'R']:
            for k in query[part]:
                if any(source['source_predictions'][part][k][field] != parsed[0][part][k][field]
                       for field in ['rank_correct', 'strict_EM']):
                    raise ValueError('Reflow step0 differs from the exact Dminus source endpoint')
    for p in [root / 'result.json', root / 'config.json', root / 'events.jsonl',
              c['data'], c['freeze_manifest'], c['lr_freeze_manifest']]:
        remember(p, provenance)
    return {'config': c, 'result': r, 'queries': query, 'predictions': parsed, 'curves': curves,
            'coverage_by_step': {e['step']: e['coverage'] for e in evaluations},
            'source': source, 'updates': updates,
            'step_seconds': sum(e['step_seconds'] for e in updates),
            'peak_allocated_bytes': max(e.get('peak_allocated_bytes', 0) for e in trace)}


def paired_values(left, right, part, metric, step, change=False):
    fields = ['parent', 'data', 'target_ids', 'taught_ids', 'mode', 'K', 'data_seed',
              'inner_lr', 'effective_tokens', 'language_eval', 'language_eval_tokens']
    if any(left['config'].get(k) != right['config'].get(k) for k in fields) or left['queries'] != right['queries']:
        raise ValueError('Continuation contrasts require paired request/support/rate settings')
    if any(a['support_tokens'] != b['support_tokens'] for a, b in zip(left['updates'], right['updates'])):
        raise ValueError('Continuation contrasts require identical support token exposure')
    a, b = left['source'], right['source']
    if a['method'] != 'retain_only' and b['method'] != 'retain_only':
        settings = ['parent', 'data', 'K', 'data_seed', 'noise_seed', 'q_apply', 'noise_scheme',
                    'effective_tokens', 'retain_weight', 'language_eval', 'language_eval_tokens',
                    'retain_subset_fraction', 'subset_seed', 'target_ids', 'inner_lr']
        if (a['initial_sha256'] != b['initial_sha256']
                or any(a['source_config'].get(k) != b['source_config'].get(k) for k in settings)
                or any(any(x['route_tokens'][k] != y['route_tokens'][k] for k in ['R', 'language'])
                       for x, y in zip(a['updates'], b['updates']))):
            raise ValueError('Dminus source contrasts require same initial bytes and R/language exposure')
    field = {'candidate_accuracy': 'rank_correct', 'strict_EM': 'strict_EM'}[metric]
    clusters = {}
    for k, row in left['queries'][part].items():
        value = left['predictions'][step][part][k][field] - right['predictions'][step][part][k][field]
        if change:
            value -= left['predictions'][0][part][k][field] - right['predictions'][0][part][k][field]
        clusters.setdefault(row['entity'], []).append(value)
    # Entity-request observations describe one preselected request, including its R.
    if left['config']['mode'] == 'reteach_two':
        return {'fixed_entity_request': mean(mean(v) for v in clusters.values())}
    return {entity: mean(values) for entity, values in sorted(clusters.items())}


def contrast(records, seeds, noises, mode, fraction, rate, left, right, part, metric, step, change, draws):
    data = {}
    for seed in seeds:
        repeats = []
        for noise in noises:
            def get(method):
                key = (seed, mode, None if method == 'retain_only' else fraction,
                       None if method == 'retain_only' else noise, method, rate)
                return records[key]
            repeats.append(paired_values(get(left), get(right), part, metric, step, change))
        data[seed] = {'fixed_library': {'fixed_request': average_corruptions(repeats)}}
    # Differences of changes range [-2,2]; bootstrap the half contrast and restore units.
    if change:
        data = {s: {f: {w: {e: v / 2 for e, v in entities.items()} for w, entities in worlds.items()}
                    for f, worlds in families.items()} for s, families in data.items()}
    result = hierarchical_contrast(data, draws=draws)
    result.pop('positive_favors_left_method')
    if change:
        result['difference'] *= 2
        result['per_phi_seed'] = {s: v * 2 for s, v in result['per_phi_seed'].items()}
        if result['percentile_interval95']:
            result['percentile_interval95'] = [v * 2 for v in result['percentile_interval95']]
    result.update(direction_implies_selective_success=False,
                  interval_scope='phi-seed variability for one fixed entity request' if mode == 'reteach_two'
                  else 'paired phi-seed/entity variability conditional on shared parents')
    return result
