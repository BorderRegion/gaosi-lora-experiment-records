"""Read completed main-grid evidence and export paired, clustered estimates."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import (METHODS, POLICY, average_corruptions, hierarchical_contrast,
                                       load_run, paired_clusters, read_json, sha256)


def collect(stage):
    provenance = {}

    def metadata(path):
        provenance[path] = sha256(path)
        return read_json(path)

    original = metadata('records/formal_main_grid_ready_v2.json')
    if original['status'] != 'MAIN_GRID_COMPLETE':
        raise ValueError('The complete primary main grid is required')
    plan = original['plan']
    K = plan['primary_K']
    mixed = metadata(plan['freeze_manifests']['mixed'])
    clean = metadata(plan['freeze_manifests']['clean'])
    if any(mixed[k] != clean[k] for k in ['recovery_K', 'recovery_lr', 'q_medium', 'q_light',
                                         'architecture', 'total_training_inner_steps']):
        raise ValueError('Clean/mixed settings or training opportunities differ')
    tasks = {t['label']: t for t in plan['tasks']}
    if (len(tasks) != len(plan['tasks']) or tasks.get('known', {}).get('group') != 'known'
            or sum(t['group'] == 'fact' for t in tasks.values()) != 16
            or sum(t['group'] == 'rule' for t in tasks.values()) != 16):
        raise ValueError('Need known plus all sixteen fact/sixteen rule worlds')
    common = ['known']
    for family in ['fact', 'rule']:
        common += sorted(t for t in tasks if tasks[t]['group'] == family)[:4]
    evidence = {}

    def add(seed, task, dose, noise, method, run):
        key = (seed, task, dose, noise, method)
        if key in evidence or method not in METHODS or task not in tasks:
            raise ValueError('Duplicated or undeclared main-grid cell')
        record = load_run(run, method, K, provenance)
        c = record['config']
        q = {'clean': 0., 'light': mixed['q_light'], 'medium': mixed['q_medium']}[dose]
        if (c['data'] != tasks[task]['data'] or c['parent'] != plan['parent']
                or c['data_seed'] != seed or c['noise_seed'] != noise or c['q_apply'] != q
                or c['organization'] != 'O1' or c['noise_scheme'] != 'subspace'):
            raise ValueError('Main-grid label/parent/seed/dose differs from actual run')
        if method.startswith('phi_'):
            f = read_json(c['freeze_manifest'])
            phi = f['phi']
            if phi not in provenance:
                provenance[phi] = sha256(phi)
            if (provenance[phi] != f['phi_sha256']
                    or (seed != 17 and f.get('independent_phi_seed') != seed)):
                raise ValueError('Phi bytes or independent training identity differ')
        if seed != 17 and c.get('confirmation_seed') != seed:
            raise ValueError('Independent evaluation requires its frozen confirmation identity')
        evidence[key] = record

    for item in original['outcomes']:
        if item['primary_K'] != K or item['group'] != tasks[item['task']]['group']:
            raise ValueError('Primary task/horizon declaration differs')
        add(17, item['task'], item['dose'], item['noise_seed'], item['method'], item['run'])
    expected = set()
    for task in tasks:
        doses = [('clean', 1729), ('medium', 1729)]
        if task == 'known':
            doses += [('light', 1729), ('medium', 1730), ('medium', 1731)]
        expected |= {(17, task, d, n, m) for d, n in doses for m in METHODS}
    if stage == 'full':
        complete = metadata('records/confirmation_ready_v1.json')
        if (complete['status'] != 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE'
                or complete['seeds'] != [29, 43] or not complete['shared_parent_conditioning']):
            raise ValueError('Two independent trained-phi confirmations are required')
        for seed, path in zip([29, 43], complete['recovery_records']):
            record = metadata(path)
            if (record['status'] != 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE' or record['seed'] != seed
                    or {tuple(t) for t in record['task_scope']}
                    != {(t, tasks[t]['data']) for t in common}):
                raise ValueError('Confirmation task subset differs from its predeclared scope')
            byfile = {tasks[t]['data']: t for t in common}
            for item in record['outcomes']:
                if not item['block'].startswith('main_'):
                    continue
                c = item['config']
                task = byfile[c['data']]
                method = c['method']
                if method == 'phi':
                    f = read_json(c['freeze_manifest'])
                    method = 'phi_clean' if f['training_noise'] == 'clean' else 'phi_mix'
                dose = 'clean' if c['q_apply'] == 0 else 'medium'
                add(seed, task, dose, 1729, method, item['run'])
            expected |= {(seed, task, dose, 1729, method) for task in common
                         for dose in ['clean', 'medium'] for method in METHODS}
    if set(evidence) != expected:
        raise ValueError('Missing or extra main-grid cells; partial evidence cannot produce a complete summary')
    # Validate pairing even for contrasts/extra doses that are only exported raw.
    for seed, task, dose, noise, method in evidence:
        if method != 'standard_adam':
            paired_clusters(evidence[(seed, task, dose, noise, method)],
                            evidence[(seed, task, dose, noise, 'standard_adam')], 'candidate_accuracy')
    return evidence, tasks, common, provenance


def summarize(stage, draws=2000):
    evidence, tasks, common, provenance = collect(stage)
    contrasts = []
    scopes = [('primary_all32', [17], sorted(tasks)), ('primary_common8', [17], common)]
    if stage == 'full':
        scopes += [('independent_confirmations_common8', [29, 43], common),
                   ('all_three_phi_seeds_common8', [17, 29, 43], common)]
    for scope, seeds, subset in scopes:
        for task_group in ['known', 'fact', 'rule', 'new']:
            chosen = [t for t in subset if (tasks[t]['group'] == task_group
                                            if task_group != 'new' else t != 'known')]
            for dose in ['clean', 'medium']:
                # Primary known aggregates its three medium corruption draws.
                # The common confirmation subset uses noise1729 for every seed.
                noise_seeds = [1729, 1730, 1731] if scope == 'primary_all32' and task_group == 'known' and dose == 'medium' else [1729]
                for left, right in POLICY['contrasts']:
                    for metric in ['candidate_accuracy', 'strict_EM']:
                        data = {}
                        for seed in seeds:
                            data[seed] = {}
                            for task in chosen:
                                repeats = [paired_clusters(evidence[(seed, task, dose, n, left)],
                                                           evidence[(seed, task, dose, n, right)], metric)
                                           for n in noise_seeds]
                                data[seed].setdefault(tasks[task]['group'], {})[task] = average_corruptions(repeats)
                        estimate = hierarchical_contrast(data, draws=draws)
                        contrasts.append(dict(scope=scope, task_group=task_group, dose=dose,
                                              left=left, right=right, metric=metric,
                                              repeated_noise_seeds=noise_seeds, **estimate))
    raw = []
    for (seed, task, dose, noise, method), r in sorted(evidence.items()):
        item = {'phi_training_seed': seed, 'task': task, 'family': tasks[task]['group'],
                'dose': dose, 'noise_seed': noise, 'method': method, 'run': r['config']['out'],
                'K': r['config']['K'], 'q_apply': r['config']['q_apply'], 'inner_lr': r['config']['inner_lr'],
                'initial_sha256': r['initial_sha256'], 'coverage': r['result']['coverage'],
                'pool': r['result']['pool'], 'recovery_step_seconds': r['recovery_step_seconds'],
                'peak_allocated_bytes': r['peak_allocated_bytes']}
        for stage_name in ['clean', 'initial', 'final']:
            for part, metrics in r['result'][stage_name].items():
                item.update({f'{stage_name}.{part}.{k}': v for k, v in metrics.items()})
        item.update({f'compute.{k}': v for k, v in r['result']['compute_counters'].items()})
        raw.append(item)
    sources = [Path(__file__).resolve(), Path(__file__).resolve().parents[1] / 'gaosi/recovery_statistics.py']
    return {'status': 'PASS', 'summary_type': 'PRIMARY_MAIN_RECOVERY_SUMMARY' if stage == 'primary' else 'THREE_SEED_MAIN_RECOVERY_SUMMARY',
            'policy': POLICY, 'stage': stage, 'raw_run_count': len(raw), 'contrasts': contrasts,
            'raw_runs': raw, 'input_sha256': provenance, 'bootstrap_draws': draws,
            'analysis_source_sha256': {str(p): sha256(p) for p in sources},
            'overall_experiment_complete': False,
            'remaining_report_blocks': ['selective and reflow', 'continual and representation probes',
                                        '7B and noise/boundary/horizon diagnostics', 'conditional real-text/longer-stream decision',
                                        'protocol completion audit and final interpretation']}


def write_summary(result, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    fields = sorted({key for row in result['raw_runs'] for key in row})
    with (out / 'runs.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result['raw_runs'])
    compact = []
    for r in result['contrasts']:
        compact.append({key: json.dumps(value) if isinstance(value, (dict, list)) else value
                        for key, value in r.items() if key != 'entities_per_world'})
    with (out / 'contrasts.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(compact[0]))
        writer.writeheader()
        writer.writerows(compact)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', choices=['primary', 'full'], required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = summarize(args.stage)
    write_summary(result, args.out)
    print(json.dumps({'status': result['status'], 'out': args.out, 'run_count': result['raw_run_count']}))


if __name__ == '__main__':
    main()
