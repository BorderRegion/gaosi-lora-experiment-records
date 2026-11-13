"""Export continuation curves after complete primary/independent-seed grids."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import read_json
from gaosi.reflow_statistics import POLICY, contrast, load_reflow
from gaosi.selective_statistics import remember


def collect(stage):
    provenance, records = {}, {}

    def metadata(path):
        remember(path, provenance)
        return read_json(path)

    primary = metadata('records/reflow_ready_v1.json')
    rates = metadata('records/reflow_lrs_frozen_v1.json')['lrs']
    if primary['status'] != 'REFLOW_AND_ENTITY_DIAGNOSTICS_COMPLETE' or len(set(rates)) != 2:
        raise ValueError('Need the complete primary reflow/entity grid and two frozen rates')
    final = metadata('records/final_parents_ready.json')
    entity = metadata('records/entity_reflow_parents_ready_v1.json')
    for parents in [final, entity]:
        if (not parents['matched_R_exposure'] or not parents['phi_frozen_before_training']
                or not all(p['acquisition_gate_passed'] and p['base_unchanged'] for p in parents['parents'].values())):
            raise ValueError('Reflow requires acquired matched post-freeze parent references')
    if entity['status'] != 'ENTITY_PARENTS_READY' or not entity['entity_target_gate']['passed']:
        raise ValueError('The fixed entity must be acquired before corruption')

    def add(path, seed):
        r = load_reflow(path, seed, provenance)
        c, source = r['config'], r['source']
        parents = final if c['mode'] == 'pure_R' else entity
        if c['parent'] != parents['parents']['all']['checkpoint']:
            raise ValueError('Continuation all-parent differs from the matched request parent')
        request = parents if c['mode'] == 'pure_R' else parents['request']
        if (set(c['target_ids']) != set(request['target_ids'])
                or c['mode'] == 'reteach_two' and set(c['taught_ids']) != set(request['taught_ids'])):
            raise ValueError('Continuation targets/taught IDs differ from the declared matched-parent request')
        if source['method'] == 'retain_only' and c['source_checkpoint'] != parents['parents']['retain_only']['checkpoint']:
            raise ValueError('Continuation reference is not the matched retain-only parent')
        key = (seed, c['mode'], source['fraction'], source['noise'], source['method'], c['inner_lr'])
        if key in records:
            raise ValueError('Duplicate continuation grid cell/shared reference')
        records[key] = r

    for row in primary['outcomes']:
        add(row['run'], 17)
    seeds = [17]
    if stage == 'full':
        ready = metadata('records/confirmation_ready_v1.json')
        if (ready['status'] != 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE'
                or ready['seeds'] != [29, 43] or not ready['shared_parent_conditioning']):
            raise ValueError('Need both independent phi-seed confirmations')
        for seed, path in zip([29, 43], ready['recovery_records']):
            r = metadata(path)
            if r['status'] != 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE' or r['seed'] != seed:
                raise ValueError('Independent reflow source identity differs')
            for row in r['outcomes']:
                if row['block'] in ['pureR', 'entity_reteach2']:
                    add(row['run'], seed)
        seeds += [29, 43]
    expected = set()
    for seed in seeds:
        for mode in ['pure_R', 'reteach_two']:
            for rate in rates:
                expected.add((seed, mode, None, None, 'retain_only', rate))
                for fraction in [1., .25] if mode == 'pure_R' else [1.]:
                    for noise in [1729, 1730, 1731] if seed == 17 else [1729]:
                        for method in ['standard_adam', 'phi']:
                            expected.add((seed, mode, fraction, noise, method, rate))
    if records.keys() != expected:
        raise ValueError('Missing/extra continuation primary or confirmation grid cells')
    for key, r in records.items():
        other = records[(*key[:-1], rates[1] if key[-1] == rates[0] else rates[0])]
        if r['result']['source_sha256'] != other['result']['source_sha256']:
            raise ValueError('Both continuation rates must start from the same exact source bytes')
    return records, rates, provenance


def summarize(stage, draws=2000):
    records, rates, provenance = collect(stage)
    scopes = [('seed17_three_corruptions', [17], [1729, 1730, 1731]),
              ('seed17_common_noise', [17], [1729])]
    if stage == 'full':
        scopes += [('independent_confirmations', [29, 43], [1729]),
                   ('all_three_phi_seeds', [17, 29, 43], [1729])]
    contrasts = []
    for scope, seeds, noises in scopes:
        for mode in ['pure_R', 'reteach_two']:
            for fraction in [1., .25] if mode == 'pure_R' else [1.]:
                for rate in rates:
                    for left, right in [('phi', 'standard_adam'), ('phi', 'retain_only'), ('standard_adam', 'retain_only')]:
                        for part in ['F', 'R'] if mode == 'pure_R' else ['F', 'R', 'taught_two', 'untaught_six']:
                            for metric in ['candidate_accuracy', 'strict_EM']:
                                for step, change in [(0, False), (20, False), (80, False), (20, True), (80, True)]:
                                    result = contrast(records, seeds, noises, mode, fraction, rate, left, right,
                                                      part, metric, step, change, draws)
                                    contrasts.append(dict(scope=scope, request=mode, origin_R_fraction=fraction,
                                                          lr=rate, left=left, right=right, query_part=part,
                                                          metric=metric, continuation_step=step,
                                                          change_from_step0=change, repeated_noise_seeds=noises, **result))
    raw = []
    for (seed, mode, fraction, noise, method, rate), record in records.items():
        c, r = record['config'], record['result']
        for step, metrics in record['curves'].items():
            row = {'run': c['out'], 'phi_training_seed': seed, 'request': mode, 'origin_R_fraction': fraction,
                   'noise_seed': noise, 'origin_method': method, 'lr': rate, 'continuation_step': step,
                   'source_checkpoint': c['source_checkpoint'], 'source_sha256': r['source_sha256'],
                   'shared_retain_reference': method == 'retain_only', 'training_pool': r['pool'],
                   'coverage': record['coverage_by_step'][step], 'coverage_at80': r['coverage'],
                   'step_seconds_through_measurement': sum(e['step_seconds'] for e in record['updates'] if e['step'] <= step),
                   'continuation_total_step_seconds': record['step_seconds'],
                   'peak_allocated_bytes': record['peak_allocated_bytes']}
            row.update({f'{part}.{k}': value for part, m in metrics.items() for k, value in m.items()})
            raw.append(row)
    return {'status': 'PASS', 'summary_type': 'PAIRED_REFLOW_AND_FIXED_ENTITY_DIAGNOSTICS',
            'stage': stage, 'policy': POLICY, 'run_count': len(records), 'contrasts': contrasts, 'raw_curves': raw,
            'bootstrap_draws': draws, 'input_sha256': provenance,
            'analysis_source_sha256': {str(p): remember(p, {}) for p in
                                      [Path(__file__).resolve(), Path(__file__).resolve().parents[1] / 'gaosi/reflow_statistics.py',
                                       Path(__file__).resolve().parents[1] / 'gaosi/selective_statistics.py',
                                       Path(__file__).resolve().parents[1] / 'gaosi/recovery_statistics.py']},
            'same_paragraph_R': 'N/A: independent synthetic entries', 'overall_experiment_complete': False}


def write_summary(result, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    for name, rows in [('curves', result['raw_curves']), ('contrasts', result['contrasts'])]:
        rows = [{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()} for row in rows]
        with (out / f'{name}.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for row in rows for k in row}))
            w.writeheader(); w.writerows(rows)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', required=True, choices=['primary', 'full'])
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = summarize(args.stage)
    write_summary(result, args.out)
    print(json.dumps({'status': result['status'], 'run_count': result['run_count'], 'out': args.out}))


if __name__ == '__main__':
    main()
