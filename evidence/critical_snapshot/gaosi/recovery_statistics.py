"""Paired main-recovery summaries; no torch imports or model updates."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import mean


METHODS = ['standard_adam', 'paired_adam', 'factor_lr', 'phi_mix', 'phi_clean']
POLICY = {
    'version': 1,
    'scope': 'Primary-horizon main recovery only; selective/reflow/continual/7B and conditional extensions remain separate.',
    'contrasts': [['phi_mix', 'standard_adam'], ['phi_mix', 'paired_adam'],
                  ['phi_mix', 'factor_lr'], ['phi_mix', 'phi_clean']],
    'accuracy': 'Paired task candidate accuracy and strict EM, first averaged within entity for fact tasks. Each rule world is one cluster. Equal worlds within fact/rule, then equal family weight.',
    'known': 'Average repeated noise-seed measurements within each entity before inference. Noise seeds are repeated corruptions, never independent phi training.',
    'bootstrap': 'Percentile paired hierarchical bootstrap: independent phi seed at the outside; shared stratified world/entity draws across seeds. Families fixed, matched example IDs, common task subset only. 2000 draws, deterministic analysis seed7719.',
    'seed17': 'Full32-world primary-seed estimate is conditional on that one trained phi. Three-seed estimate uses known plus the predeclared first4 fact/first4 rule worlds; seed17 is also reported separately.',
    'interpretation': 'Intervals are descriptive/exploratory with only three phi seeds, conditional on shared parent adapters and these task families. No p-values or automatic superiority claim; all per-seed estimates retained.',
    'other_metrics': 'Raw clean/noisy/final task/keep/language metrics, actual coverage and compute/time/memory are exported without changing their production denominators. NLL and costs are not given entity-bootstrap intervals here.',
    'settings_selected_by_summary': False,
}


def read_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def keyed(rows):
    result = {}
    for row in rows:
        key = row['sample_id']
        if key in result:
            raise ValueError('Duplicate query sample ID')
        result[key] = row
    if not result:
        raise ValueError('Empty endpoint query evidence')
    return result


def load_run(path, label, expected_K, provenance):
    root = Path(path)
    result = read_json(root / 'result.json')
    c = read_json(root / 'config.json')
    if (result['config'] != c or c['out'] != str(root) or result['status'] != 'PASS'
            or c['phase'] != 'formal' or c['task_layout'] != 'all'
            or c['K'] != expected_K or not result['base_unchanged']
            or result['coverage'] != result['pool'] or not result['phi_frozen']):
        raise ValueError('Only complete actual-primary-horizon main trajectories may be summarized')
    if result.get('keep_coverage') and result['keep_coverage']['covered'] != result['keep_coverage']['pool']:
        raise ValueError('Incomplete known-fact retention support coverage')
    freeze = read_json(c['freeze_manifest'])
    if (freeze['status'] != 'FROZEN' or not freeze['stopping_rule_frozen']
            or freeze['formal_data_used'] or freeze['recovery_K'] != expected_K):
        raise ValueError('Invalid frozen primary recovery rule')
    if label != 'factor_lr' and c['inner_lr'] != freeze['recovery_lr']:
        raise ValueError('Main method LR differs from the frozen recovery setting')
    if label.startswith('phi_'):
        if (c['method'] != 'phi' or c['phi'] != freeze['phi']
                or freeze['training_noise'] != ('clean' if label == 'phi_clean' else 'mixed')):
            raise ValueError('Training-noise label differs from the frozen phi')
    elif c['method'] != label:
        raise ValueError('Baseline label differs from the actual method')
    source = keyed([r for r in [json.loads(s) for s in Path(c['data']).read_text().splitlines() if s.strip()]
                    if r['role'] == 'query' and (r['kind'] != 'fact' or r['sample_id'].endswith(':0'))])
    file = root / f'eval_{expected_K:04d}_task.jsonl'
    predictions = keyed([json.loads(s) for s in file.read_text().splitlines() if s.strip()])
    if source.keys() != predictions.keys():
        raise ValueError('Endpoint predictions must cover exactly the declared query pool')
    for key, row in predictions.items():
        if (row['answer'] != source[key]['answer'] or row['world_id'] != source[key]['world_id']
                or any(row[k] not in [0, 1] for k in ['rank_correct', 'strict_EM'])):
            raise ValueError('Endpoint answer/identity/binary metric mismatch')
    for metric, key in [('candidate_accuracy', 'rank_correct'), ('strict_EM', 'strict_EM')]:
        if not math.isclose(mean(r[key] for r in predictions.values()),
                            result['final']['task'][metric], abs_tol=1e-12):
            raise ValueError('Endpoint example evidence differs from the reported metric')
    if result['final']['task']['n'] != len(predictions):
        raise ValueError('Endpoint query count mismatch')
    trace = [json.loads(s) for s in (root / 'events.jsonl').read_text().splitlines() if s.strip()]
    updates = [e for e in trace if e['event'] == 'recover']
    if [e['step'] for e in updates] != list(range(1, expected_K + 1)):
        raise ValueError('Missing/duplicated updates in completed primary trajectory')
    endpoints = [e for e in trace if e['event'] == 'evaluation' and e['step'] == expected_K]
    if len(endpoints) != 1 or endpoints[0]['metrics'] != result['final']:
        raise ValueError('Primary endpoint event differs or is duplicated')
    initial = Path(c.get('initial_adapter', str(root / 'initial.pt')))
    for p in [root / 'result.json', root / 'config.json', file, root / 'events.jsonl',
              Path(c['data']), Path(c['freeze_manifest']), initial]:
        name = str(p)
        if name not in provenance:
            provenance[name] = sha256(p)
    return {'config': c, 'result': result, 'queries': source, 'predictions': predictions,
            'initial_sha256': provenance[str(initial)],
            'recovery_step_seconds': sum(e['step_seconds'] for e in updates),
            'peak_allocated_bytes': max((e.get('peak_allocated_bytes', 0) for e in trace), default=0)}


def paired_clusters(left, right, metric):
    keys = ['parent', 'data', 'retain_data', 'organization', 'K', 'data_seed', 'noise_seed',
            'q_apply', 'noise_scheme', 'effective_tokens', 'retain_weight', 'language_eval',
            'language_eval_tokens', 'D', 'retain_subset_fraction', 'subset_seed']
    if any(left['config'].get(k) != right['config'].get(k) for k in keys):
        raise ValueError('Unpaired task/support/corruption/horizon settings')
    if (left['initial_sha256'] != right['initial_sha256']
            or left['queries'] != right['queries']
            or left['predictions'].keys() != right['predictions'].keys()):
        raise ValueError('Paired methods need identical initial bytes and query identities')
    clusters = defaultdict(list)
    field = {'candidate_accuracy': 'rank_correct', 'strict_EM': 'strict_EM'}[metric]
    for key, row in left['queries'].items():
        if row['kind'] == 'fact' and not row.get('entity'):
            raise ValueError('Fact inference requires an explicit entity identity')
        entity = row['entity'] if row['kind'] == 'fact' else '__rule_world__'
        clusters[entity].append(left['predictions'][key][field] - right['predictions'][key][field])
    return {key: mean(values) for key, values in sorted(clusters.items())}


def percentile(values, p):
    values = sorted(values)
    x = (len(values) - 1) * p
    lo = int(x)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (x - lo) * (values[hi] - values[lo])


def hierarchical_contrast(data, draws=2000, seed=7719):
    """data[phi_seed][family][world][entity] = paired accuracy difference."""
    if not data or draws < 100:
        raise ValueError('Need paired evidence and at least100 bootstrap draws')
    seeds = sorted(data)
    reference = data[seeds[0]]
    shape = lambda d: {f: {w: set(c) for w, c in worlds.items()} for f, worlds in d.items()}
    if any(shape(data[s]) != shape(reference) for s in seeds):
        raise ValueError('All phi seeds must use exactly the same task/entity subset')
    if (not reference or any(not worlds or any(not c for c in worlds.values()) for worlds in reference.values())
            or any(not math.isfinite(v) or not -1 <= v <= 1
                   for d in data.values() for worlds in d.values() for c in worlds.values() for v in c.values())):
        raise ValueError('Invalid paired cluster evidence')
    estimate = lambda d: mean(mean(mean(c.values()) for c in worlds.values()) for worlds in d.values())
    per_seed = {str(s): estimate(data[s]) for s in seeds}
    rng = random.Random(seed)
    replicates = []
    for _ in range(draws):
        chosen_seeds = rng.choices(seeds, k=len(seeds))
        family_values = []
        for family, worlds in reference.items():
            world_values = []
            for world in rng.choices(sorted(worlds), k=len(worlds)):
                entities = sorted(worlds[world])
                chosen_entities = rng.choices(entities, k=len(entities))
                world_values.append(mean(mean(data[s][family][world][e] for e in chosen_entities)
                                         for s in chosen_seeds))
            family_values.append(mean(world_values))
        replicates.append(mean(family_values))
    information_units = len(seeds) > 1 or any(len(w) > 1 or any(len(c) > 1 for c in w.values())
                                           for w in reference.values())
    return {'difference': mean(per_seed.values()), 'per_phi_seed': per_seed,
            'percentile_interval95': [percentile(replicates, .025), percentile(replicates, .975)]
            if information_units else None,
            'independent_phi_training_seeds': len(seeds),
            'worlds_per_family': {f: len(w) for f, w in reference.items()},
            'entities_per_world': {f: {w: len(c) for w, c in worlds.items()} for f, worlds in reference.items()},
            'bootstrap_draws': draws, 'analysis_seed': seed,
            'conditional_on_one_trained_phi': len(seeds) == 1,
            'shared_parent_conditioning': True, 'positive_favors_left_method': True}


def average_corruptions(repeats):
    """Repeated noise draws stay within entity; they do not enlarge seed count."""
    if not repeats or any(set(r) != set(repeats[0]) for r in repeats):
        raise ValueError('Repeated corruptions must preserve entity identities')
    return {entity: mean(r[entity] for r in repeats) for entity in repeats[0]}
