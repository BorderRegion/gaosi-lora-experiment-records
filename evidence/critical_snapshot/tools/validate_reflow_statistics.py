"""Simulated production-format continuation evidence; no model/effect claims."""
import copy
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import read_json, sha256
from gaosi.reflow_statistics import contrast, load_reflow, paired_values, pools
import summarize_reflow as summary
from validate_recovery_statistics import rejects, save


def jsonl(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def fixture():
    data = []
    for entity in range(64):
        for attribute in range(8):
            for role in ['support', 'query']:
                data.append({'sample_id': f'e{entity:02d}:a{attribute}:{role}:0', 'fact_id': f'e{entity:02d}:a{attribute}',
                             'world_id': 'final_parent', 'entity': f'e{entity:02d}', 'role': role,
                             'kind': 'fact', 'answer': 'simulated_answer'})
    jsonl('data/final.jsonl', data)
    all_ids = sorted({r['fact_id'] for r in data})
    targets = {'pure_R': [i for i in all_ids if i.endswith(':a0')],
               'reteach_two': [i for i in all_ids if i.startswith('e00:')]}
    taught = targets['reteach_two'].copy(); random.Random(91).shuffle(taught); taught = sorted(taught[:2])
    rates = [.0001, .0003]
    save('records/reflow_lrs_frozen_v1.json', {'status': 'REFLOW_LRS_FROZEN', 'formal_data_used': False,
         'reflow_outcomes_used': False, 'lrs': rates, 'recovery_K': 2, 'development_parent': 'dev.pt'})
    parents = {}
    for mode in targets:
        records = {}
        for kind in ['all', 'retain_only']:
            out = f'parents/{mode}_{kind}'
            c = {'data': 'data/final.jsonl', 'target_ids': targets[mode], 'parent_mode': kind}
            save(out + '/config.json', c)
            save(out + '/result.json', {'status': 'PASS', 'config': c, 'base_unchanged': True,
                                        'acquisition_gate_passed': True})
            Path(out, 'latest.pt').write_bytes(f'SIMULATED parent {mode} {kind}'.encode())
            records[kind] = {'checkpoint': out + '/latest.pt', 'acquisition_gate_passed': True, 'base_unchanged': True}
        parents[mode] = {'parents': records, 'matched_R_exposure': True, 'phi_frozen_before_training': True}
    parents['pure_R']['target_ids'] = targets['pure_R']
    parents['reteach_two'].update(status='ENTITY_PARENTS_READY', entity_target_gate={'passed': True},
                                 request={'target_ids': targets['reteach_two'], 'taught_ids': taught})
    save('records/final_parents_ready.json', parents['pure_R'])
    save('records/entity_reflow_parents_ready_v1.json', parents['reteach_two'])
    freezes = {}
    for seed in [17, 29, 43]:
        phi = f'phi{seed}.pt'; Path(phi).write_bytes(f'SIMULATED trained phi{seed}'.encode())
        f = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'formal_data_used': False,
             'organization': 'O1', 'training_noise': 'mixed', 'phi': phi, 'phi_sha256': sha256(phi),
             'recovery_K': 2, 'q_medium': .7, 'recovery_lr': .0003, 'development_parent': 'dev.pt'}
        if seed != 17:
            f['independent_phi_seed'] = seed
        freezes[seed] = f'freeze{seed}.json'; save(freezes[seed], f)

    def create(seed, mode, fraction, noise, method, lr):
        label = f'{seed}_{mode}_{fraction}_{noise}_{method}'
        out = f'runs/{label}_{lr:g}'
        source_out = f'sources/{label}'
        source_path = (parents[mode]['parents']['retain_only']['checkpoint'] if method == 'retain_only'
                       else source_out + '/latest.pt')
        c = {'out': out, 'freeze_manifest': freezes[seed], 'lr_freeze_manifest': 'records/reflow_lrs_frozen_v1.json',
             'parent': parents[mode]['parents']['all']['checkpoint'], 'source_checkpoint': source_path,
             'source_kind': 'retain_only' if method == 'retain_only' else 'selective',
             'data': 'data/final.jsonl', 'target_ids': targets[mode], 'taught_ids': taught if mode == 'reteach_two' else [],
             'mode': mode, 'inner_lr': lr, 'data_seed': seed, 'K': 80, 'eval_steps': [20, 80],
             'effective_tokens': 512, 'language_eval': 'language/final.jsonl', 'language_eval_tokens': 4096}
        if seed != 17:
            c['confirmation_seed'] = seed
        query, selected = pools(c)

        def observations(step):
            # Deterministic hand-checkable paired differences; no learned effects.
            answers = {}
            for r in query['F'].values():
                entity, attribute = int(r['entity'][1:]), int(r['fact_id'].split(':a')[1])
                index = entity if mode == 'pure_R' else attribute
                threshold = {'standard_adam': 2, 'phi': 4, 'retain_only': 0}[method]
                threshold += (1 if step == 20 else 2 if step == 80 else 0)
                if method == 'phi' and step == 80:
                    threshold += 1
                answers[r['sample_id']] = (int(index % 8 < threshold), int(index % 8 < max(0, threshold - 1)))
            for r in query['R'].values():
                index = int(r['entity'][1:])
                threshold = 7 if method == 'phi' else 6
                answers[r['sample_id']] = (int(index % 8 < threshold), int(index % 8 < threshold - 1))
            parsed, metrics = {}, {}
            for part, rows in query.items():
                parsed[part] = []
                for key, r in rows.items():
                    p = {k: r[k] for k in ['sample_id', 'world_id', 'fact_id', 'answer']}
                    p.update(rank_correct=answers[key][0], strict_EM=answers[key][1])
                    parsed[part].append(p)
                metrics[part] = {'n': len(rows), 'candidate_accuracy': sum(r['rank_correct'] for r in parsed[part])/len(rows),
                                 'strict_EM': sum(r['strict_EM'] for r in parsed[part])/len(rows), 'nll': 1., 'abnormal_rate': 0.}
            metrics['language'] = {'nll': 2., 'answer_tokens': 4096}
            return parsed, metrics

        all_curves = {step: observations(step) for step in [0, 20, 80]}
        if method != 'retain_only':
            initial = f'initial_{seed}_{mode}_{noise}.pt'
            Path(initial).write_bytes(f'SIMULATED common corruption {seed} {mode} {noise}'.encode())
            s = {'out': source_out, 'data': c['data'], 'target_ids': c['target_ids'], 'parent': c['parent'],
                 'task_layout': 'selective', 'phase': 'formal', 'organization': 'O1', 'D': 'minus',
                 'K': 2, 'q_apply': .7, 'noise_scheme': 'subspace', 'inner_lr': .0003,
                 'method': method, 'data_seed': seed, 'noise_seed': noise, 'freeze_manifest': freezes[seed],
                 'retain_subset_fraction': fraction, 'effective_tokens': 512, 'retain_weight': .25,
                 'language_eval': c['language_eval'], 'language_eval_tokens': 4096, 'subset_seed': 91,
                 'initial_adapter': initial}
            if method == 'phi':
                s['phi'] = f'phi{seed}.pt'
            if seed != 17:
                s['confirmation_seed'] = seed
            parsed, metrics = all_curves[0]
            # A selective source evaluates F/R; reteaching parts belong to continuation only.
            metrics = {p: metrics[p] for p in ['F', 'R', 'language']}
            save(source_out + '/config.json', s)
            save(source_out + '/result.json', {'status': 'PASS', 'config': s, 'base_unchanged': True,
                 'phi_frozen': True, 'coverage': round(len(query['R'])*fraction),
                 'pool': round(len(query['R'])*fraction), 'coverage_slot': 'R', 'final': metrics})
            events = [{'event': 'recover', 'step': k, 'route_tokens': {'F': 0, 'R': 512, 'language': 512}} for k in [1, 2]]
            events += [{'event': 'evaluation', 'step': 2, 'metrics': metrics}]
            jsonl(source_out + '/events.jsonl', events)
            for part in ['F', 'R']:
                jsonl(f'{source_out}/eval_0002_{part}.jsonl', parsed[part])
            Path(source_path).write_bytes(f'SIMULATED endpoint {label}'.encode())
        save(out + '/config.json', c)
        source_hash = sha256(source_path)
        events = [{'event': 'reflow_policy', 'mode': mode, 'source_sha256': source_hash,
                   'training_ids': sorted(selected), 'additional_noise': False, 'additional_QR': False, 'phi_called': False,
                   'full_query_ids': {p: sorted(r['fact_id'] for r in rows.values()) for p, rows in query.items()}}]
        for step in range(81):
            if step:
                events.append({'event': 'reflow', 'step': step, 'support_tokens': 512, 'step_seconds': 1.})
            if step in all_curves:
                parsed, metrics = all_curves[step]
                events.append({'event': 'evaluation', 'step': step, 'metrics': metrics,
                               'coverage': 0 if step == 0 else len(selected), 'pool': len(selected)})
                for part in query:
                    jsonl(f'{out}/eval_{step:04d}_{part}.jsonl', parsed[part])
        jsonl(out + '/events.jsonl', events)
        save(out + '/result.json', {'status': 'PASS', 'config': c, 'base_unchanged': True,
             'source_sha256': source_hash, 'coverage': len(selected), 'pool': len(selected),
             'stream': {'covered': sorted(selected)}, 'initial': all_curves[0][1], 'final': all_curves[80][1]})
        return out

    primary, confirmations = [], []
    for seed in [17, 29, 43]:
        outcomes = []
        for mode in targets:
            for lr in rates:
                paths = [create(seed, mode, None, None, 'retain_only', lr)]
                for fraction in [1., .25] if mode == 'pure_R' else [1.]:
                    for noise in [1729, 1730, 1731] if seed == 17 else [1729]:
                        for method in ['standard_adam', 'phi']:
                            paths.append(create(seed, mode, fraction, noise, method, lr))
                outcomes += [{'block': 'pureR' if mode == 'pure_R' else 'entity_reteach2', 'run': p} for p in paths]
        if seed == 17:
            primary = outcomes
        else:
            path = f'records/confirm{seed}.json'; confirmations.append(path)
            save(path, {'status': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE', 'seed': seed, 'outcomes': outcomes})
    save('records/reflow_ready_v1.json', {'status': 'REFLOW_AND_ENTITY_DIAGNOSTICS_COMPLETE', 'outcomes': primary})
    save('records/confirmation_ready_v1.json', {'status': 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE',
         'seeds': [29, 43], 'shared_parent_conditioning': True, 'recovery_records': confirmations})


def main():
    tests, previous = {}, Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix='gaosi_reflow_stats_') as tmp:
            os.chdir(tmp)
            fixture()
            records, rates, provenance = summary.collect('full')
            assert len(records) == 72
            assert len(summary.collect('primary')[0]) == 40
            tests['simulated_production_primary40_and_three_seed72_complete_grids'] = 'PASS'
            rate = rates[0]
            phi = records[(17, 'pure_R', .25, 1729, 'phi', rate)]
            adam = records[(17, 'pure_R', .25, 1729, 'standard_adam', rate)]
            initial = paired_values(phi, adam, 'F', 'candidate_accuracy', 0)
            assert math.isclose(sum(initial.values())/64, .25)
            delta = paired_values(phi, adam, 'F', 'candidate_accuracy', 80, True)
            assert math.isclose(sum(delta.values())/64, .125)
            tests['hand_calculated_accuracy_and_paired_change_from_zero'] = 'PASS'
            for step, change, expected in [(0, False, .25), (80, True, .125)]:
                r = contrast(records, [17, 29, 43], [1729], 'pure_R', .25, rate,
                             'phi', 'standard_adam', 'F', 'candidate_accuracy', step, change, 100)
                assert math.isclose(r['difference'], expected) and r['independent_phi_training_seeds'] == 3
                assert not r['direction_implies_selective_success']
            tests['paired_entity_hierarchy_and_true_independent_phi_seed_counts'] = 'PASS'
            r = contrast(records, [17], [1729, 1730, 1731], 'reteach_two', 1., rate,
                         'phi', 'standard_adam', 'untaught_six', 'candidate_accuracy', 80, False, 100)
            assert r['percentile_interval95'] is None and r['independent_phi_training_seeds'] == 1
            assert r['entities_per_world']['fixed_library']['fixed_request'] == 1
            tests['one_entity_six_untaught_facts_are_not_six_population_replicates'] = 'PASS'
            assert phi['result']['pool'] == 448 and len(phi['queries']['R']) == 448
            assert {r['result']['pool'] for r in records.values()} == {2, 448}
            tests['quarter_source_continues_on_complete448_R_and_only_two_taught_support_ids'] = 'PASS'
            wrong = copy.deepcopy(phi); wrong['source']['initial_sha256'] = 'different'
            rejects(lambda: paired_values(wrong, adam, 'F', 'strict_EM', 20), 'same initial bytes')
            wrong = copy.deepcopy(phi); wrong['updates'][0]['support_tokens'] = 1024
            rejects(lambda: paired_values(wrong, adam, 'R', 'strict_EM', 80), 'token exposure')
            tests['same_corruption_and_continuation_token_exposure_enforced'] = 'PASS'
            root = Path(phi['config']['out'])
            source = Path(phi['config']['source_checkpoint'])
            before = source.read_bytes(); source.write_bytes(before + b'tampered')
            rejects(lambda: load_reflow(str(root), 17, {}), 'checkpoint bytes changed')
            source.write_bytes(before)
            tests['source_checkpoint_hash_enforced'] = 'PASS'
            events = root / 'events.jsonl'; before = events.read_bytes()
            trace = [json.loads(s) for s in before.decode().splitlines()]
            trace[0]['training_ids'].append(phi['config']['target_ids'][0]); jsonl(events, trace)
            rejects(lambda: load_reflow(str(root), 17, {}), 'wrong support IDs')
            events.write_bytes(before)
            source_events = source.parent / 'events.jsonl'; source_before = source_events.read_bytes()
            trace = [json.loads(s) for s in source_before.decode().splitlines()]
            trace[0]['route_tokens']['F'] = 1; jsonl(source_events, trace)
            rejects(lambda: load_reflow(str(root), 17, {}), 'F support tokens')
            source_events.write_bytes(source_before)
            tests['pure_R_cannot_include_F_and_Dminus_source_cannot_hide_F_tokens'] = 'PASS'
            file = root / 'eval_0020_R.jsonl'; before = file.read_bytes()
            file.write_bytes(b''.join(before.splitlines(keepends=True)[:112]))
            rejects(lambda: load_reflow(str(root), 17, {}), 'denominator')
            file.write_bytes(before)
            tests['full_query_denominators_enforced_at_intermediate20_step'] = 'PASS'
            file = Path('records/reflow_ready_v1.json'); before = file.read_bytes(); r = read_json(file)
            r['outcomes'].append(r['outcomes'][0]); save(file, r)
            rejects(lambda: summary.collect('primary'), 'Duplicate continuation')
            r['outcomes'] = r['outcomes'][2:]; save(file, r)
            rejects(lambda: summary.collect('primary'), 'Missing/extra')
            file.write_bytes(before)
            tests['shared_reference_duplicates_and_missing_grid_rejected'] = 'PASS'
            # Exercise all contrasts and JSON/CSV export using explicitly simulated data.
            full = summary.summarize('full', draws=100)
            assert full['run_count'] == 72 and not full['overall_experiment_complete']
            assert len(full['raw_curves']) == 216
            summary.write_summary(full, 'export')
            assert all(Path('export', f).exists() for f in ['result.json', 'curves.csv', 'contrasts.csv'])
            assert len([r for r in full['raw_curves'] if r['shared_retain_reference']]) == 36
            tests['all0_20_80_curves_and_contrasts_export_without_reference_pseudoreplication'] = 'PASS'
            phi_file = Path('phi17.pt'); phi_file.write_bytes(phi_file.read_bytes() + b'tampered')
            rejects(lambda: load_reflow(str(root), 17, {}), 'intact and development-frozen')
            tests['frozen_phi_bytes_checked_before_summary'] = 'PASS'
    finally:
        os.chdir(previous)
    r = {'status': 'PASS', 'scope': 'Pure CPU simulated continuation evidence; no model or learned-effect evidence',
         'tests': tests, 'count': len(tests)}
    save('checks/reflow_statistics_cpu.json', r)
    print(json.dumps(r, indent=2))


if __name__ == '__main__':
    main()
