"""Summarize primary selective endpoints and preserve separate K64 diagnostics."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import read_json
from gaosi.selective_statistics import (POLICY, load_selective, pair, predictions,
                                        query_pools, remember, summarize_contrast)


def collect(stage):
    provenance = {}

    def metadata(path):
        remember(path, provenance)
        return read_json(path)

    formal = metadata('records/formal_recovery_ready_v2.json')
    if formal['status'] != 'FORMAL_RECOVERY_GRID_COMPLETE_REFLOW_PENDING':
        raise ValueError('Need the complete formal selective grid')
    K = formal['primary_K']
    parent = formal['plan']['parent']
    targets = set(formal['plan']['target_ids'])
    evidence, diagnostics = {}, []
    expected = {(17, fraction, noise, method, D) for fraction in [1., .25]
                for noise in [1729, 1730, 1731] for method in ['standard_adam', 'phi'] for D in ['plus', 'minus']}

    def add(seed, run, primary, declared=None):
        r = read_json(Path(run) / 'result.json')
        c = r['config']
        record = load_selective(run, seed, c['K'], K, provenance)
        key = (seed, c['retain_subset_fraction'], c['noise_seed'], c['method'], c['D'])
        if c['parent'] != parent or set(c['target_ids']) != targets or primary != (c['K'] == K):
            raise ValueError('Selective source parent/request or primary classification differs')
        if declared and any(declared[k] != c[v] for k, v in [('retain_fraction', 'retain_subset_fraction'),
                           ('method', 'method'), ('D', 'D'), ('noise_seed', 'noise_seed'), ('K', 'K')]):
            raise ValueError('Selective source labels differ from its actual config')
        if primary:
            if key in evidence:
                raise ValueError('Duplicate primary selective cell')
            evidence[key] = record
        else:
            if c['K'] != 64 or c['noise_seed'] != 1729 or seed != 17:
                raise ValueError('Unexpected diagnostic horizon or noise/phi seed')
            diagnostics.append(record)

    for outcome in formal['selective_outcomes']:
        add(17, outcome['run'], outcome['primary'], outcome)
    expected_diagnostics = {(fraction, method, D) for fraction in [1., .25]
                            for method in ['standard_adam', 'phi'] for D in ['plus', 'minus']} if K != 64 else set()
    actual = [(r['config']['retain_subset_fraction'], r['config']['method'], r['config']['D']) for r in diagnostics]
    if len(actual) != len(expected_diagnostics) or set(actual) != expected_diagnostics:
        raise ValueError('Missing or duplicate separate K64 diagnostic grid')
    if stage == 'full':
        ready = metadata('records/confirmation_ready_v1.json')
        if (ready['status'] != 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE'
                or ready['seeds'] != [29, 43] or not ready['shared_parent_conditioning']):
            raise ValueError('Need both independent trained-phi confirmations')
        for seed, path in zip([29, 43], ready['recovery_records']):
            record = metadata(path)
            if record['status'] != 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE' or record['seed'] != seed:
                raise ValueError('Independent selective source identity differs')
            for item in record['outcomes']:
                if item['block'].startswith('selective_R'):
                    add(seed, item['run'], True)
            expected |= {(seed, fraction, 1729, method, D) for fraction in [1., .25]
                         for method in ['standard_adam', 'phi'] for D in ['plus', 'minus']}
    if set(evidence) != expected:
        raise ValueError('Missing/extra primary selective cells')
    for seed, fraction, noise, method, D in evidence:
        pair(evidence[(seed, fraction, noise, method, D)],
             evidence[(seed, fraction, noise, 'standard_adam', 'plus')], 'F', 'candidate_accuracy')
    final_parents = metadata('records/final_parents_ready.json')
    if (not final_parents['matched_R_exposure'] or not final_parents['phi_frozen_before_training']
            or not all(p['acquisition_gate_passed'] for p in final_parents['parents'].values())
            or final_parents['parents']['all']['checkpoint'] != parent):
        raise ValueError('References require acquired matched post-freeze final parents')
    root = Path(formal['references'])
    ref = metadata(str(root / 'result.json'))
    c = metadata(str(root / 'config.json'))
    if (ref['status'] != 'PASS' or ref['config'] != c or not ref['base_unchanged']
            or not ref['base_equals_drop_adapter'] or ref['results']['base'] != ref['results']['drop_adapter']
            or c['parents'] != {key: p['checkpoint'] for key, p in final_parents['parents'].items()}
            or set(c['target_ids']) != targets):
        raise ValueError('Invalid base/drop-adapter/matched-retain reference evidence')
    pools = query_pools(c)
    for label in ['base', 'parent_all', 'retain_only']:
        for part in ['F', 'R']:
            predictions(root / f'{label}_{part}.jsonl', pools[part], ref['results'][label][part], provenance)
    remember(c['data'], provenance)
    return evidence, diagnostics, ref, provenance


def summarize(stage, draws=2000):
    evidence, diagnostics, references, provenance = collect(stage)
    contrasts = []
    scopes = [('primary_three_corruptions', [17], [1729, 1730, 1731]), ('primary_common_noise', [17], [1729])]
    if stage == 'full':
        scopes += [('independent_confirmations', [29, 43], [1729]), ('all_three_phi_seeds', [17, 29, 43], [1729])]
    comparisons = [(('phi', D), ('standard_adam', D)) for D in ['plus', 'minus']]
    comparisons += [((method, 'plus'), (method, 'minus')) for method in ['standard_adam', 'phi']]
    for scope, seeds, noises in scopes:
        for fraction in [1., .25]:
            for left, right in comparisons:
                for part in ['F', 'R']:
                    for metric in ['candidate_accuracy', 'strict_EM']:
                        r = summarize_contrast(evidence, seeds, noises, fraction, left, right, part, metric, draws)
                        contrasts.append(dict(scope=scope, support_R_fraction=fraction, repeated_noise_seeds=noises,
                                              left=list(left), right=list(right), query_part=part, metric=metric, **r))
    raw = []
    for primary, records in [(True, list(evidence.values())), (False, diagnostics)]:
        for record in records:
            c, r = record['config'], record['result']
            row = {'primary': primary, 'run': c['out'], 'phi_training_seed': c['data_seed'],
                   'noise_seed': c['noise_seed'], 'method': c['method'], 'D': c['D'],
                   'support_R_fraction': c['retain_subset_fraction'], 'K': c['K'],
                   'coverage': r['coverage'], 'pool': r['pool'], 'initial_sha256': record['initial_sha256'],
                   'recovery_step_seconds': record['recovery_step_seconds'],
                   'peak_allocated_bytes': record['peak_allocated_bytes'], 'same_entity_R_equals_R': True}
            for stage_name in ['clean', 'initial', 'final']:
                for part, metrics in r[stage_name].items():
                    row.update({f'{stage_name}.{part}.{k}': v for k, v in metrics.items()})
            raw.append(row)
    return {'status': 'PASS', 'scope': 'SELECTIVE_RECOVERY_SUMMARY', 'stage': stage, 'policy': POLICY,
            'primary_run_count': len(evidence), 'separate_K64_diagnostic_count': len(diagnostics),
            'contrasts': contrasts, 'raw_runs': raw, 'references': references['results'],
            'base_drop_reference_is_shared': True, 'same_paragraph_R': 'N/A: separate synthetic entries',
            'input_sha256': provenance,
            'analysis_source_sha256': {str(p): remember(p, {}) for p in
                                      [Path(__file__).resolve(), Path(__file__).resolve().parents[1] / 'gaosi/selective_statistics.py',
                                       Path(__file__).resolve().parents[1] / 'gaosi/recovery_statistics.py']},
            'bootstrap_draws': draws, 'overall_experiment_complete': False,
            'remaining': ['pure-R20/80 and two-of-eight reteaching', 'continual/7B/diagnostics',
                          'conditional extensions and final protocol audit']}


def write_summary(result, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    for name, rows in [('runs', result['raw_runs']), ('contrasts', result['contrasts'])]:
        rows = [{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in rows]
        with (out / f'{name}.csv').open('w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=sorted({k for row in rows for k in row}))
            writer.writeheader(); writer.writerows(rows)
    (out / 'references.json').write_text(json.dumps(result['references'], indent=2) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', choices=['primary', 'full'], required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = summarize(args.stage)
    write_summary(result, args.out)
    print(json.dumps({'status': result['status'], 'primary_run_count': result['primary_run_count'], 'out': args.out}))


if __name__ == '__main__':
    main()
