"""Read-only selective-recovery evidence and paired entity-level contrasts."""
from pathlib import Path
from statistics import mean
import json
import math

from .recovery_statistics import (average_corruptions, hierarchical_contrast, keyed,
                                  read_json, sha256)


POLICY = {
    'version': 1,
    'scope': 'O1 attribute-request selective recovery and its base/drop/retain-only references. Reflow, entity reteaching and continual/7B remain separate.',
    'primary': 'Use the actual frozen K trajectory; K64 diagnostics are exported separately and never substituted at intermediate steps.',
    'contrasts': 'phi minus Adam under each D direction, and Dplus minus Dminus within each method; F and R accuracy/EM reported separately without a success-direction label.',
    'query_scope': 'Evaluate full F64 and R448 for both full and25-percent support-R pools. The reduced support pool never changes query denominators. All R are same-entity other attributes in this request; mixed paragraphs are not present.',
    'inference': 'Paired entities within the single final library, noise repeats averaged inside entity. Primary three-noise description conditional on seed17; common noise1729 subsets for seed17, confirmations29/43, all three. Independent phi seeds outside entity draws, shared entity draws across seeds; 2000 percentile bootstrap draws/seed7719.',
    'references': 'One shared base/drop measurement, parent-all and matched retain-only. No reference or repeated corruption is counted as an independent phi training.',
    'interpretation': 'Raw F/R/language must be read jointly. F reduction alone is not selective success or permanent erasure. O1 immutable W0 remains recoverable by disabling the adapter. Three-seed intervals are descriptive and conditional on shared parent training.',
    'settings_selected_by_results': False,
}


def remember(path, provenance):
    name = str(path)
    if name not in provenance:
        provenance[name] = sha256(path)
    return provenance[name]


def query_pools(config):
    source = [json.loads(s) for s in Path(config['data']).read_text().splitlines() if s.strip()]
    queries = keyed([r for r in source if r['role'] == 'query' and r['sample_id'].endswith(':0')])
    if any(r['kind'] != 'fact' or not r.get('entity') or not r.get('fact_id') for r in queries.values()):
        raise ValueError('Selective statistics require explicit fact and entity identities')
    targets = set(config['target_ids'])
    F = {k: r for k, r in queries.items() if r['fact_id'] in targets}
    R = {k: r for k, r in queries.items() if r['fact_id'] not in targets}
    if (len(F) != 64 or len(R) != 448 or {r['fact_id'] for r in F.values()} != targets
            or len({r['entity'] for r in F.values()}) != 64
            or {r['entity'] for r in F.values()} != {r['entity'] for r in R.values()}):
        raise ValueError('Attribute request must preserve the full F64/R448 query pools')
    if len({r['fact_id'] for r in queries.values()}) != 512:
        raise ValueError('Need exactly one query per final binding')
    return {'F': F, 'R': R}


def predictions(path, source, metrics, provenance):
    rows = keyed([json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()])
    if rows.keys() != source.keys() or metrics['n'] != len(source):
        raise ValueError('Selective endpoint query denominator/identity changed')
    for key, r in rows.items():
        if (any(r[k] != source[key][k] for k in ['world_id', 'fact_id', 'answer'])
                or any(r[k] not in [0, 1] for k in ['rank_correct', 'strict_EM'])):
            raise ValueError('Selective answer/identity/binary metric mismatch')
    for field, metric in [('rank_correct', 'candidate_accuracy'), ('strict_EM', 'strict_EM')]:
        if not math.isclose(mean(r[field] for r in rows.values()), metrics[metric], abs_tol=1e-12):
            raise ValueError('Selective raw predictions differ from the reported endpoint')
    remember(path, provenance)
    return rows


def load_selective(path, seed, K, primary_K, provenance):
    root = Path(path)
    result = read_json(root / 'result.json')
    c = read_json(root / 'config.json')
    if (result['config'] != c or c['out'] != str(root) or result['status'] != 'PASS'
            or c['phase'] != 'formal' or c['task_layout'] != 'selective' or c['K'] != K
            or not result['base_unchanged'] or result['coverage'] != result['pool']
            or not result['phi_frozen'] or c['data_seed'] != seed or c['organization'] != 'O1'
            or c['method'] not in ['standard_adam', 'phi'] or c['D'] not in ['plus', 'minus']
            or c['retain_subset_fraction'] not in [1., .25] or c.get('retain_data')):
        raise ValueError('Need a complete declared O1 selective trajectory')
    f = read_json(c['freeze_manifest'])
    if (f['status'] != 'FROZEN' or f['formal_data_used'] or not f['stopping_rule_frozen']
            or f['recovery_K'] != primary_K or c['q_apply'] != f['q_medium']
            or c['inner_lr'] != f['recovery_lr'] or c['noise_scheme'] != 'subspace'
            or f['training_noise'] != 'mixed'):
        raise ValueError('Selective dose/rate/primary rule differs from its freeze')
    if c['method'] == 'phi':
        if c['phi'] != f['phi'] or remember(c['phi'], provenance) != f['phi_sha256']:
            raise ValueError('Selective phi bytes differ from the frozen rule')
    if seed != 17 and (c.get('confirmation_seed') != seed or f.get('independent_phi_seed') != seed):
        raise ValueError('Selective confirmation requires its own trained phi identity')
    queries = query_pools(c)
    parsed = {part: predictions(root / f'eval_{K:04d}_{part}.jsonl', source,
                                result['final'][part], provenance) for part, source in queries.items()}
    expected_pool = round(448 * c['retain_subset_fraction'])
    if result['pool'] != expected_pool or result['coverage_slot'] != 'R':
        raise ValueError('Selective support coverage differs from the fixed full/quarter R pool')
    trace = [json.loads(s) for s in (root / 'events.jsonl').read_text().splitlines() if s.strip()]
    updates = [e for e in trace if e['event'] == 'recover']
    endpoints = [e for e in trace if e['event'] == 'evaluation' and e['step'] == K]
    if ([e['step'] for e in updates] != list(range(1, K + 1)) or len(endpoints) != 1
            or endpoints[0]['metrics'] != result['final']):
        raise ValueError('Incomplete/duplicated selective update or endpoint trace')
    if any(e['route_tokens'].get('F', 0) != 0 for e in updates) and c['D'] == 'minus':
        raise ValueError('Dminus evidence contains target support tokens')
    initial = c.get('initial_adapter', str(root / 'initial.pt'))
    digest = remember(initial, provenance)
    for p in [root / 'config.json', root / 'result.json', root / 'events.jsonl',
              c['data'], c['freeze_manifest']]:
        remember(p, provenance)
    return {'config': c, 'result': result, 'queries': queries, 'predictions': parsed,
            'initial_sha256': digest, 'updates': updates,
            'recovery_step_seconds': sum(e['step_seconds'] for e in updates),
            'peak_allocated_bytes': max((e.get('peak_allocated_bytes', 0) for e in trace), default=0)}


def pair(left, right, part, metric):
    keys = ['parent', 'data', 'organization', 'K', 'data_seed', 'noise_seed', 'q_apply', 'noise_scheme',
            'effective_tokens', 'retain_weight', 'language_eval', 'language_eval_tokens',
            'retain_subset_fraction', 'subset_seed', 'target_ids', 'inner_lr']
    if (any(left['config'].get(k) != right['config'].get(k) for k in keys)
            or left['initial_sha256'] != right['initial_sha256'] or left['queries'] != right['queries']):
        raise ValueError('Selective contrasts require paired state/support/request settings')
    for a, b in zip(left['updates'], right['updates']):
        if any(a['route_tokens'][k] != b['route_tokens'][k] for k in ['R', 'language']):
            raise ValueError('D directions/methods must retain identical R/language token exposure')
    key = {'candidate_accuracy': 'rank_correct', 'strict_EM': 'strict_EM'}[metric]
    values = {}
    for sample, row in left['queries'][part].items():
        values.setdefault(row['entity'], []).append(left['predictions'][part][sample][key]
                                                    - right['predictions'][part][sample][key])
    return {entity: mean(v) for entity, v in sorted(values.items())}


def summarize_contrast(evidence, seeds, noises, fraction, left, right, part, metric, draws):
    data = {}
    for seed in seeds:
        repeats = [pair(evidence[(seed, fraction, n, *left)], evidence[(seed, fraction, n, *right)],
                        part, metric) for n in noises]
        data[seed] = {'final_library': {'attribute_request': average_corruptions(repeats)}}
    result = hierarchical_contrast(data, draws=draws)
    result.pop('positive_favors_left_method')
    result['positive_means_higher_left_accuracy'] = True
    result['direction_implies_selective_success'] = False
    return result
