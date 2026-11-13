"""Adversarial paired statistics checks with explicit simulated full-grid evidence."""
import copy
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import (METHODS, average_corruptions, hierarchical_contrast,
                                       load_run, paired_clusters, sha256)
import summarize_recovery as summary


def rejects(fn, fragment):
    try:
        fn()
    except ValueError as error:
        assert fragment in str(error), str(error)
    else:
        raise AssertionError('Invalid paired evidence was accepted')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture():
    tasks = [{'label': 'known', 'group': 'known', 'data': 'data/known.jsonl'}]
    tasks += [{'label': f'{f}{i:02d}', 'group': f, 'data': f'data/{f}{i:02d}.jsonl'}
              for f in ['fact', 'rule'] for i in range(16)]
    for task in tasks:
        t, family = task['label'], task['group']
        facts = family != 'rule'
        rows = [{'sample_id': f'{t}:q:{i}:0', 'world_id': t, 'answer': 'a', 'role': 'query',
                 'kind': 'fact' if facts else 'affine_integer', **({'entity': f'{t}:e{i//2}'} if facts else {})}
                for i in range(4)]
        p = Path(task['data']); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    freezes = {}
    for seed in [17, 29, 43]:
        for method in ['phi_mix', 'phi_clean']:
            phi = Path(f'phi_{method}_{seed}.pt'); phi.write_bytes(f'simulated rule{method}{seed}'.encode())
            f = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'formal_data_used': False,
                 'training_noise': 'clean' if method == 'phi_clean' else 'mixed',
                 'recovery_K': 2, 'recovery_lr': .0003, 'q_medium': .7, 'q_light': .5,
                 'architecture': 'L', 'total_training_inner_steps': 3072,
                 'phi': str(phi), 'phi_sha256': sha256(phi)}
            if seed != 17:
                f['independent_phi_seed'] = seed
            path = f'freezes/{method}_{seed}.json'; save(path, f); freezes[(seed, method)] = path

    def run(seed, task, dose, noise, method):
        name = f'{seed}_{task["label"]}_{dose}_{noise}_{method}'
        out = 'runs/' + name
        initial = Path(f'initial/{seed}_{dose}_{noise}.pt'); initial.parent.mkdir(exist_ok=True)
        initial.write_bytes(f'simulated common initial{seed}{dose}{noise}'.encode())
        c = {'out': out, 'phase': 'formal', 'task_layout': 'all', 'K': 2, 'parent': 'shared_parent.pt',
             'data': task['data'], 'data_seed': seed, 'noise_seed': noise,
             'q_apply': {'clean': 0., 'medium': .7, 'light': .5}[dose], 'organization': 'O1',
             'noise_scheme': 'subspace', 'effective_tokens': 512, 'retain_weight': .25,
             'language_eval': 'language.jsonl', 'language_eval_tokens': 4096, 'inner_lr': .0003,
             'D': 'plus', 'retain_subset_fraction': 1., 'subset_seed': 91,
             'method': 'phi' if method.startswith('phi_') else method,
             'initial_adapter': str(initial), 'freeze_manifest': freezes[(seed, method if method.startswith('phi_') else 'phi_mix')]}
        if method.startswith('phi_'):
            c['phi'] = f'phi_{method}_{seed}.pt'
        if seed != 17:
            c['confirmation_seed'] = seed
        source = [json.loads(s) for s in Path(task['data']).read_text().splitlines()]
        preds = []
        for i, row in enumerate(source):
            prediction = {k: row[k] for k in ['sample_id', 'world_id', 'answer']}
            prediction.update(rank_correct=int(i < (3 if method == 'phi_mix' else 2)),
                              strict_EM=int(i < (2 if method == 'phi_mix' else 1)))
            preds.append(prediction)
        metrics = {'task': {'candidate_accuracy': statistics.mean(r['rank_correct'] for r in preds),
                            'strict_EM': statistics.mean(r['strict_EM'] for r in preds), 'n': 4, 'nll': 1.},
                   'language': {'nll': 2.}}
        result = {'status': 'PASS', 'config': c, 'base_unchanged': True, 'coverage': 4, 'pool': 4,
                  'phi_frozen': True, 'clean': metrics, 'initial': metrics, 'final': metrics,
                  'compute_counters': {'loss_forwards': 4, 'loss_backwards': 2}}
        save(out + '/config.json', c); save(out + '/result.json', result)
        Path(out, 'eval_0002_task.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in preds))
        events = [{'event': 'recover', 'step': i, 'step_seconds': 1., 'peak_allocated_bytes': 123} for i in [1, 2]]
        events.append({'event': 'evaluation', 'step': 2, 'metrics': metrics})
        Path(out, 'events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in events))
        return c

    outputs = []
    for task in tasks:
        doses = [('clean', 1729), ('medium', 1729)]
        if task['label'] == 'known':
            doses += [('light', 1729), ('medium', 1730), ('medium', 1731)]
        for dose, noise in doses:
            for method in METHODS:
                c = run(17, task, dose, noise, method)
                outputs.append({'task': task['label'], 'group': task['group'], 'dose': dose,
                                'noise_seed': noise, 'method': method, 'run': c['out'], 'primary_K': 2})
    main = {'status': 'MAIN_GRID_COMPLETE', 'plan': {'primary_K': 2, 'parent': 'shared_parent.pt', 'tasks': tasks,
            'freeze_manifests': {'mixed': freezes[(17, 'phi_mix')], 'clean': freezes[(17, 'phi_clean')]}},
            'outcomes': outputs}
    save('records/formal_main_grid_ready_v2.json', main)
    common = [t for t in tasks if t['label'] == 'known' or int(t['label'][-2:]) < 4]
    records = []
    for seed in [29, 43]:
        outcomes = []
        for task in common:
            for dose in ['clean', 'medium']:
                for method in METHODS:
                    c = run(seed, task, dose, 1729, method)
                    outcomes.append({'block': 'main_' + Path(c['out']).name, 'config': c, 'run': c['out']})
        path = f'records/confirm{seed}.json'; records.append(path)
        save(path, {'status': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE', 'seed': seed,
                    'task_scope': [[t['label'], t['data']] for t in common], 'outcomes': outcomes})
    save('records/confirmation_ready_v1.json', {'status': 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE',
         'seeds': [29, 43], 'shared_parent_conditioning': True, 'recovery_records': records})
    return main


def main():
    tests = {}
    data = {17: {'fact': {'a': {'e1': 1., 'e2': 0.}, 'b': {'e1': 0.}},
                 'rule': {'r': {'__rule_world__': -1.}}}}
    a = hierarchical_contrast(data, draws=200)
    assert a['difference'] == -.375 and a['independent_phi_training_seeds'] == 1
    assert a['conditional_on_one_trained_phi']
    assert a == hierarchical_contrast(data, draws=200)
    tests['equal_world_then_equal_family_weight_and_deterministic_paired_intervals'] = 'PASS'
    repeats = [{'e1': 1., 'e2': 0.}, {'e1': 0., 'e2': 1.}, {'e1': .5, 'e2': .5}]
    averaged = average_corruptions(repeats)
    assert averaged == {'e1': .5, 'e2': .5}
    a = hierarchical_contrast({17: {'known': {'k': averaged}}}, draws=100)
    assert a['independent_phi_training_seeds'] == 1 and a['difference'] == .5
    tests['noise_repeats_average_within_entity_never_inflate_training_seed_count'] = 'PASS'
    b = {17: {'fact': {'w': {'e': -1.}}}, 29: {'fact': {'w': {'e': 0.}}}, 43: {'fact': {'w': {'e': 1.}}}}
    a = hierarchical_contrast(b, draws=100)
    assert a['difference'] == 0 and a['independent_phi_training_seeds'] == 3 and a['percentile_interval95'][0] < 0
    wrong = copy.deepcopy(b); wrong[43]['fact']['extra'] = {'e': 0.}
    rejects(lambda: hierarchical_contrast(wrong, draws=100), 'same task/entity')
    tests['outer_phi_seed_resampling_and_common_task_subset_required'] = 'PASS'
    assert hierarchical_contrast({17: {'rule': {'r': {'one': .25}}}}, draws=100)['percentile_interval95'] is None
    tests['single_rule_world_does_not_invent_query_independence'] = 'PASS'
    previous = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix='gaosi_statistics_') as d:
            os.chdir(d)
            original = fixture()
            p = summary.summarize('primary', draws=100)
            f = summary.summarize('full', draws=100)
            assert p['raw_run_count'] == 345 and f['raw_run_count'] == 525
            assert all(math.isclose(c['difference'], .25) for c in f['contrasts'])
            assert not f['overall_experiment_complete']
            summary.write_summary(f, 'export')
            assert all(Path('export', n).exists() for n in ['result.json', 'runs.csv', 'contrasts.csv'])
            tests['simulated_345_primary_525_three_seed_grid_real_parser_and_exports'] = 'PASS'
            assert all(c['independent_phi_training_seeds'] == 3 for c in f['contrasts'] if c['scope'].startswith('all_three'))
            assert all(c['independent_phi_training_seeds'] == 2 for c in f['contrasts'] if c['scope'].startswith('independent_'))
            tests['exploratory_seed17_kept_separate_from_two_confirmations_and_common8'] = 'PASS'
            config = 'runs/17_known_clean_1729_phi_mix/config.json'
            c = json.loads(Path(config).read_text())
            left = load_run(c['out'], 'phi_mix', 2, {})
            right = load_run('runs/17_known_clean_1729_standard_adam', 'standard_adam', 2, {})
            wrong = copy.deepcopy(left); wrong['initial_sha256'] = 'changed'
            rejects(lambda: paired_clusters(wrong, right, 'strict_EM'), 'identical initial')
            wrong = copy.deepcopy(left); wrong['config']['data_seed'] = 123
            rejects(lambda: paired_clusters(wrong, right, 'strict_EM'), 'Unpaired')
            tests['initial_bytes_and_support_seed_pairing_enforced'] = 'PASS'
            rejects(lambda: load_run(c['out'], 'phi_mix', 64, {}), 'actual-primary')
            path = Path(c['out'], 'eval_0002_task.jsonl'); text = path.read_text()
            path.write_text(text + text.splitlines(True)[0])
            rejects(lambda: load_run(c['out'], 'phi_mix', 2, {}), 'Duplicate')
            path.write_text(text)
            rows = [json.loads(s) for s in text.splitlines()]; rows[0]['rank_correct'] = 0
            path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            rejects(lambda: load_run(c['out'], 'phi_mix', 2, {}), 'reported metric')
            path.write_text(text)
            tests['K64_substitution_duplicate_queries_and_metric_mismatch_rejected'] = 'PASS'
            missing = copy.deepcopy(original); missing['outcomes'].pop()
            save('records/formal_main_grid_ready_v2.json', missing)
            rejects(lambda: summary.collect('primary'), 'Missing or extra')
            duplicate = copy.deepcopy(original); duplicate['outcomes'].append(duplicate['outcomes'][0])
            save('records/formal_main_grid_ready_v2.json', duplicate)
            rejects(lambda: summary.collect('primary'), 'Duplicated')
            save('records/formal_main_grid_ready_v2.json', original)
            tests['partial_and_duplicate_grid_cannot_emit_complete_summary'] = 'PASS'
            e = Path(c['out'], 'events.jsonl'); text = e.read_text()
            e.write_text(text + text.splitlines(True)[0])
            rejects(lambda: load_run(c['out'], 'phi_mix', 2, {}), 'Missing/duplicated')
            e.write_text(text)
            r = Path(c['out'], 'result.json'); value = json.loads(r.read_text()); value['coverage'] = 3
            save(r, value)
            rejects(lambda: load_run(c['out'], 'phi_mix', 2, {}), 'complete actual-primary')
            tests['actual_update_trace_and_complete_binding_coverage_required'] = 'PASS'
    finally:
        os.chdir(previous)
    result = {'status': 'PASS', 'scope': 'Pure CPU statistical logic and explicitly simulated production-format grids; no real effect evidence',
              'tests': tests, 'count': len(tests)}
    Path('checks/recovery_statistics_cpu.json').parent.mkdir(exist_ok=True)
    save('checks/recovery_statistics_cpu.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
