"""Simulate complete eight-round/probe files; never claim actual LLM effects."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import sha256
from confirmation_queue import select_second
import summarize_continual as summary
from validate_recovery_statistics import rejects, save


def jsonl(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in data))


def fixture():
    files = []
    for index, count in enumerate([512] + [32]*12):
        data = [{'sample_id': f'w{index}:f{i}:{role}:0', 'fact_id': f'w{index}:f{i}',
                 'world_id': f'w{index}', 'entity': f'w{index}:e{i//8}', 'role': role,
                 'kind': 'fact', 'answer': 'SIMULATED'} for i in range(count) for role in ['support', 'query']]
        file = f'data/world{index}.jsonl'; jsonl(file, data); files.append(file)
    worlds, probes = files[1:9], files[9:]
    save('records/final_parents_ready.json', {'matched_R_exposure': True, 'phi_frozen_before_training': True,
         'parents': {'all': {'checkpoint': 'all.pt', 'acquisition_gate_passed': True, 'base_unchanged': True}}})
    query = {f: {r['sample_id']: r for r in summary.rows(f) if r['role'] == 'query'} for f in files}
    bindings = {f: {r['fact_id'] for r in q.values()} for f, q in query.items()}

    def evaluate(path, queries, threshold):
        data = []
        for r in queries.values():
            n = int(r['fact_id'].split(':f')[1]) % 8
            p = {k: r[k] for k in ['sample_id', 'world_id', 'fact_id', 'answer']}
            p.update(rank_correct=int(n < threshold), strict_EM=int(n < max(0, threshold-1)))
            data.append(p)
        jsonl(path, data)
        return {'n': len(data), 'candidate_accuracy': sum(p['rank_correct'] for p in data)/len(data),
                'strict_EM': sum(p['strict_EM'] for p in data)/len(data), 'nll': 1., 'abnormal_rate': 0.}

    def cost():
        return {'same_prompt_same_underlying_base': True, 'warmups': 2, 'repetitions': 5,
                'timings': {k: {'seconds': [.1]*5} for k in ['adapter_enabled', 'adapter_disabled_same_base']},
                'adapter_storage_bytes': 400}

    def probe_files(root, prefix, threshold):
        records = []
        for i, f in enumerate(probes):
            curves = [{'steps': step, **evaluate(Path(root)/prefix/f'fresh_{i}_k{step}.jsonl', query[f], threshold)}
                      for step in [1, 4, 16]]
            records.append({'world': f, 'rank': 16, 'curves': curves})
        return records

    schedule_paths, plans = {}, {}
    for seed in [17, 29, 43]:
        order = worlds.copy(); random.Random(seed).shuffle(order)
        plan = {'status': 'FROZEN_CONTINUAL_SCHEDULE', 'phi_frozen_before_selection': True,
                'stream_probe_parent_bindings_disjoint': True, 'parent': 'all.pt', 'parent_data': files[0],
                'worlds': order, 'probe_worlds': probes, 'day_steps': 2, 'night_steps': 2,
                'day_lr': .0003, 'night_lr': .0001, 'seed': seed, 'replay_seed': seed*100+12,
                'probe_seed': 37, 'seq': 512, 'effective_tokens': 512, 'language_eval': 'language/final.jsonl',
                'language_eval_tokens': 4096, 'measure_inference': True, 'inference_repetitions': 5,
                'data_sha256': {f: sha256(f) for f in files}}
        plans[seed] = plan; schedule_paths[seed] = f'records/schedule{seed}.json'; save(schedule_paths[seed], plan)
    base_c = {'out': 'runs/base_reference', 'parent': 'all.pt', 'probe_worlds': probes}
    base = {'status': 'PASS', 'config': base_c, 'common_extra_rank': 16, 'steps': [1, 4, 16],
            'shared_reference': True, 'base_unchanged': True, 'base_digest': 'SAME_ORIGINAL_BASE',
            'repeated_as_independent_0_4_8_measurements': False,
            'probes': probe_files(base_c['out'], Path('rank16'), 2), 'inference': cost()}
    save(base_c['out']+'/config.json', base_c); save(base_c['out']+'/result.json', base)

    def create(seed, org, method):
        phi = f'phi{seed}_{org}.pt'; Path(phi).write_bytes(f'SIMULATED own phi {seed} {org}'.encode())
        freeze = {'status': 'FROZEN', 'formal_data_used': False, 'stopping_rule_frozen': True,
                  'organization': org, 'training_noise': 'mixed', 'phi': phi, 'phi_sha256': sha256(phi),
                  'q_medium': .7, 'capacity_lr': .0003, 'recovery_lr': .0001, 'architecture': 'L'}
        if seed != 17:
            freeze['independent_phi_seed'] = seed
        freeze_file = f'freeze{seed}_{org}.json'; save(freeze_file, freeze)
        plan = plans[seed]
        c = {k: v for k, v in plan.items() if k not in ['status', 'phi_frozen_before_selection',
               'stream_probe_parent_bindings_disjoint', 'data_sha256']}
        c.update(out=f'runs/{seed}_{org}_{method}', phase='formal', rounds=8, organization=org,
                 architecture='L',
                 freeze_manifest=freeze_file, schedule_manifest=schedule_paths[seed],
                 q_apply=.7 if method.startswith('noise') else 0.,
                 night_method='phi' if method.endswith('phi') else 'standard_adam')
        if method.endswith('phi'):
            c['phi'] = phi
        if seed != 17:
            c['confirmation_seed'] = seed
        root = Path(c['out']); save(root/'config.json', c)
        threshold = 6 if method.endswith('phi') else 4
        if org == 'O2' and method.endswith('phi'):
            threshold = 7  # Explicit synthetic pilot selection, never a real outcome.
        events = []
        if org == 'O4':
            events.append({'event': 'merge_initial_history', 'merge_count': 1, 'adapter_reset': True,
                           'KV_cache_discarded': True, 'working_BF16_rounding_sq': 0., 'FP32_master_sq': 1.})
        known = bindings[files[0]].copy()
        for rnd in range(9):
            if rnd:
                world = c['worlds'][rnd-1]; known |= bindings[world]
                for phase in ['day', 'night']:
                    count = 50 if org == 'O2' or org == 'O3' and phase == 'day' else 100
                    model = {'rank': 32, 'adapter_parameters': 100, 'trainable_parameters': count}
                    events.append({'event': phase+'_start', 'round': rnd, 'model': model})
                    events.extend({'event': phase, 'round': rnd, 'k': k, 'task_tokens': 512, 'language_tokens': 512,
                                   'seconds': 1., 'cpu_rss_bytes': 1000, 'peak_allocated_bytes': 1000} for k in [1, 2])
                events.append({'event': 'day_complete', 'round': rnd, 'fact_stream': {'covered': sorted(bindings[world])}})
                selected = sorted(known, key=lambda k: hashlib.sha256(f'{c["replay_seed"]}:{k}'.encode()).hexdigest())[:128]
                events.append({'event': 'night_complete', 'round': rnd, 'replay_ids': selected,
                               'replay_coverage': {'covered': selected}})
                for phase in ['day', 'night']:
                    metric = evaluate(root/f'new_{phase}_round_{rnd:02d}.jsonl', query[world], threshold)
                    events.append({'event': f'new_{phase}_evaluation', 'round': rnd, 'metrics': metric})
                events.append({'event': 'checkpoint_storage', 'round': rnd, 'bytes': 1000,
                               'adapter_bytes': 400, 'includes_FP32_merged_base': org == 'O4'})
                if org == 'O4':
                    events.append({'event': 'merge', 'round': rnd, 'merge_count': rnd+1, 'adapter_reset': True,
                                   'KV_cache_discarded': True, 'working_BF16_rounding_sq': 0., 'FP32_master_sq': 1.})
            history = {k: r for f in [files[0], *c['worlds'][:rnd]] for k, r in query[f].items()}
            # A late forgetting example gives a nonzero acquisition-to-final contrast.
            metric = evaluate(root/f'history_round_{rnd:02d}.jsonl', history,
                              threshold - (1 if rnd == 8 and method.endswith('phi') else 0))
            events.append({'event': 'history_evaluation', 'round': rnd, 'language': {'nll': 2.}, **metric})
            if rnd in [0, 4, 8]:
                capacity = probe_files(root, Path(f'probe_round_{rnd}'), threshold)
                events.append({'event': 'fresh_capacity', 'round': rnd, 'common_extra_rank': 16, 'worlds': capacity})
                events.append({'event': 'inference_cost', 'round': rnd, 'measurement': cost(),
                               'merge_count': rnd+1 if org == 'O4' else 0,
                               'cumulative_merge_rank_upper_bound': 32*(rnd+1) if org == 'O4' else 0})
        jsonl(root/'events.jsonl', events)
        r = {'status': 'PASS', 'config': c, 'completed_rounds': 8, 'replay_capacity': 128,
             'phi_frozen': True, 'total_adapter_rank': 32, 'base_unchanged': org != 'O4',
             'cumulative_merge_rank_upper_bound': 288 if org == 'O4' else 0, 'base_digest': 'SAME_ORIGINAL_BASE'}
        save(root/'result.json', r)
        return {'organization': org, 'method': method, 'run': str(root), 'result': r, 'completed_rounds': 8}

    pilot = {'status': 'SIXTEEN_EIGHT_ROUND_PILOTS_COMPLETE', 'plan': plans[17], 'base_reference': base,
             'outcomes': [create(17, org, method) for org in ['O1', 'O2', 'O3', 'O4'] for method in summary.METHODS]}
    save('records/continual_pilot_ready_v1.json', pilot)
    selection = select_second(pilot); assert selection['organization'] == 'O2'
    records = []
    for seed in [29, 43]:
        path = f'records/confirm{seed}.json'; records.append(path)
        save(path, {'status': 'INDEPENDENT_EIGHT_ROUND_CONFIRMATION_COMPLETE', 'seed': seed,
                    'organizations': ['O1', 'O2'], 'shared_parent_conditioning': True,
                    'outcomes': [create(seed, org, method) for org in ['O1', 'O2'] for method in summary.METHODS]})
    save('records/confirmation_ready_v1.json', {'status': 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE',
         'seeds': [29, 43], 'organization_selection': selection, 'continual_records': records})


def main():
    tests = {}; previous = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix='gaosi_continual_stats_') as tmp:
            os.chdir(tmp); fixture()
            evidence, base, probes, second, provenance = summary.collect('full')
            assert len(evidence) == 32 and second == 'O2'
            assert len(summary.collect('primary')[0]) == 16
            tests['simulated16_pilots_and32_total_trajectories_with_shared_base_probes'] = 'PASS'
            a, b = evidence[(17, 'O1', 'clean_phi')], evidence[(17, 'O1', 'clean_Adam')]
            for scope, expected in [('parent', .125), ('stream', .125), ('forgetting', -.125)]:
                data = {seed: summary.endpoint(evidence[(seed, 'O1', 'clean_phi')],
                                               evidence[(seed, 'O1', 'clean_Adam')], 'candidate_accuracy', scope)
                        for seed in [17, 29, 43]}
                r = summary.bootstrap(data, 100, scope == 'forgetting')
                assert math.isclose(r['difference'], expected) and r['independent_phi_training_seeds'] == 3
            tests['hand_computed_endpoints_and_acquisition_to_final_forgetting_by_actual_world_ID'] = 'PASS'
            assert a['config']['worlds'] != evidence[(29, 'O1', 'clean_phi')]['config']['worlds']
            assert set(summary.endpoint(a, b, 'strict_EM', 'stream')['stream']) == set(a['config']['worlds'])
            tests['different_round_orders_are_aligned_by_world_not_treated_as_independent_rounds'] = 'PASS'
            wrong = copy.deepcopy(a); wrong['updates'][(1, 'day')][0]['task_tokens'] += 1
            rejects(lambda: summary.verify_pair(wrong, b), 'token exposure')
            tests['actual_token_exposure_pairing_enforced'] = 'PASS'
            root = Path(a['config']['out']); file = root/'events.jsonl'; before = file.read_bytes()
            trace = summary.rows(file)
            next(e for e in trace if e['event'] == 'night_complete')['replay_ids'][0] = 'future_fact'
            jsonl(file, trace)
            rejects(lambda: summary.load_run(str(root), 17, 'O1', 'clean_phi', {}), 'exact128')
            file.write_bytes(before)
            tests['future_or_wrong_reservoir_binding_rejected'] = 'PASS'
            trace = summary.rows(file); trace.append(next(e for e in trace if e['event'] == 'history_evaluation'))
            jsonl(file, trace)
            rejects(lambda: summary.load_run(str(root), 17, 'O1', 'clean_phi', {}), 'history_evaluation')
            file.write_bytes(before)
            tests['duplicate_round_evidence_cannot_be_pseudoreplication'] = 'PASS'
            target = root/'history_round_08.jsonl'; original = target.read_bytes()
            target.write_bytes(b''.join(original.splitlines(keepends=True)[:-1]))
            rejects(lambda: summary.load_run(str(root), 17, 'O1', 'clean_phi', {}), 'denominator')
            target.write_bytes(original)
            tests['full_parent_plus256_history_queries_required'] = 'PASS'
            target = root/'probe_round_4/fresh_0_k4.jsonl'; original = target.read_bytes()
            values = summary.rows(target); values[0]['answer'] = 'leaked_or_changed'; jsonl(target, values)
            rejects(lambda: summary.load_run(str(root), 17, 'O1', 'clean_phi', {}), 'identity')
            target.write_bytes(original)
            tests['common_rank16_probe_raw_query_identity_and_metrics_checked'] = 'PASS'
            target = Path('records/confirmation_ready_v1.json'); original = target.read_bytes()
            r = json.loads(original); r['organization_selection']['organization'] = 'O3'; save(target, r)
            rejects(lambda: summary.collect('full'), 'exploratory pilot selection')
            target.write_bytes(original)
            tests['second_organization_selection_recomputed_from_preserved_pilots'] = 'PASS'
            target = Path('records/continual_pilot_ready_v1.json'); original = target.read_bytes()
            r = json.loads(original); r['outcomes'].append(r['outcomes'][0]); save(target, r)
            rejects(lambda: summary.collect('primary'), 'Duplicate continual')
            r['outcomes'] = r['outcomes'][2:]; save(target, r)
            rejects(lambda: summary.collect('primary'), 'Missing/extra')
            target.write_bytes(original)
            tests['missing_or_duplicate_organization_cells_rejected'] = 'PASS'
            r = summary.summarize('full', draws=100); summary.write_summary(r, 'export')
            assert r['run_count'] == 32 and len(r['raw_curves']) == 32*9 and len(r['probe_curves']) == 32*36
            assert not r['overall_experiment_complete']
            assert all(Path('export', n).exists() for n in ['result.json', 'contrasts.csv', 'raw_curves.csv', 'costs.csv', 'probe_curves.csv'])
            assert all(c['independent_phi_training_seeds'] == 2 for c in r['contrasts'] if c['scope'] == 'independent_confirmations')
            assert all(c['independent_phi_training_seeds'] == 1 for c in r['contrasts'] if c['scope'] == 'seed17_pilots')
            tests['all_raw_curves_costs_and_shared_reference_contrasts_exported_with_separate_seed_scopes'] = 'PASS'
            phi = Path('phi17_O1.pt'); phi.write_bytes(phi.read_bytes()+b'tampered')
            rejects(lambda: summary.load_run(str(root), 17, 'O1', 'clean_phi', {}), 'intact frozen')
            tests['frozen_phi_bytes_checked'] = 'PASS'
    finally:
        os.chdir(previous)
    r = {'status': 'PASS', 'scope': 'Simulated512-parent/8x32-stream/4x32-probe records only; no model or effect evidence',
         'tests': tests, 'count': len(tests)}
    save('checks/continual_statistics_cpu.json', r); print(json.dumps(r, indent=2))


if __name__ == '__main__':
    main()
