"""Simulated F64/R448 evidence; no model or learning-effect claims."""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gaosi.recovery_statistics import sha256
from gaosi.selective_statistics import load_selective, pair
import summarize_selective as summary
from validate_recovery_statistics import rejects, save


def fixture():
    data = []
    for e in range(64):
        for a in range(8):
            data.append({'sample_id': f'final:e{e}:a{a}:query:0', 'world_id': 'final_parent', 'kind': 'fact',
                         'role': 'query', 'fact_id': f'final:e{e}:a{a}', 'entity': f'e{e}', 'answer': 'answer'})
    Path('data').mkdir()
    Path('data/final.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in data))
    targets = [r['fact_id'] for r in data if ':a0' in r['fact_id']]
    freeze = {}
    for seed in [17, 29, 43]:
        phi = Path(f'phi{seed}.pt'); phi.write_bytes(f'simulated phi{seed}'.encode())
        f = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'formal_data_used': False,
             'recovery_K': 2, 'q_medium': .7, 'recovery_lr': .0003, 'training_noise': 'mixed',
             'phi': str(phi), 'phi_sha256': sha256(phi)}
        if seed != 17:
            f['independent_phi_seed'] = seed
        freeze[seed] = f'freeze{seed}.json'; save(freeze[seed], f)

    def create(seed, fraction, noise, method, D, K):
        out = f'runs/s{seed}_R{fraction}_{noise}_{method}_{D}_K{K}'
        initial = Path(f'initial_{seed}_{noise}.pt'); initial.write_bytes(f'common{seed}{noise}'.encode())
        c = {'out': out, 'phase': 'formal', 'task_layout': 'selective', 'K': K, 'parent': 'all.pt',
             'data': 'data/final.jsonl', 'target_ids': targets, 'data_seed': seed, 'noise_seed': noise,
             'q_apply': .7, 'organization': 'O1', 'noise_scheme': 'subspace', 'effective_tokens': 512,
             'retain_weight': .25, 'inner_lr': .0003, 'language_eval': 'final_language.jsonl',
             'language_eval_tokens': 4096, 'D': D, 'retain_subset_fraction': fraction, 'subset_seed': 91,
             'method': method, 'initial_adapter': str(initial), 'freeze_manifest': freeze[seed]}
        if method == 'phi':
            c['phi'] = f'phi{seed}.pt'
        if seed != 17:
            c['confirmation_seed'] = seed
        save(out + '/config.json', c)
        metrics = {}
        for part in ['F', 'R']:
            source = [r for r in data if (r['fact_id'] in targets) == (part == 'F')]
            fraction_correct = (.75 if D == 'plus' else .25) if part == 'F' else .75
            fraction_correct += .125 if method == 'phi' else 0.
            threshold = round(len(source) * fraction_correct)
            rows = []
            for i, r in enumerate(source):
                p = {k: r[k] for k in ['sample_id', 'world_id', 'fact_id', 'answer']}
                p.update(rank_correct=int(i < threshold), strict_EM=int(i < threshold-len(source)//8))
                rows.append(p)
            metrics[part] = {'n': len(rows), 'candidate_accuracy': sum(r['rank_correct'] for r in rows)/len(rows),
                             'strict_EM': sum(r['strict_EM'] for r in rows)/len(rows), 'nll': 1.}
            Path(out, f'eval_{K:04d}_{part}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        metrics['language'] = {'nll': 2.}
        r = {'status': 'PASS', 'config': c, 'base_unchanged': True, 'phi_frozen': True,
             'coverage': round(448*fraction), 'pool': round(448*fraction), 'coverage_slot': 'R',
             'clean': metrics, 'initial': metrics, 'final': metrics}
        save(out + '/result.json', r)
        events = [{'event': 'recover', 'step': k, 'step_seconds': 1.,
                   'route_tokens': {'F': 512 if D == 'plus' else 0, 'R': 512, 'language': 512}}
                  for k in range(1, K+1)]
        events.append({'event': 'evaluation', 'step': K, 'metrics': metrics})
        Path(out, 'events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in events))
        return c

    outcomes = []
    for fraction in [1., .25]:
        for noise in [1729, 1730, 1731]:
            for K in ([2, 64] if noise == 1729 else [2]):
                for method in ['standard_adam', 'phi']:
                    for D in ['plus', 'minus']:
                        c = create(17, fraction, noise, method, D, K)
                        outcomes.append({'run': c['out'], 'primary': K == 2, 'K': K, 'retain_fraction': fraction,
                                         'method': method, 'D': D, 'noise_seed': noise})
    formal = {'status': 'FORMAL_RECOVERY_GRID_COMPLETE_REFLOW_PENDING', 'primary_K': 2,
              'plan': {'parent': 'all.pt', 'target_ids': targets}, 'selective_outcomes': outcomes,
              'references': 'runs/references'}
    save('records/formal_recovery_ready_v2.json', formal)
    records = []
    for seed in [29, 43]:
        entries = []
        for fraction in [1., .25]:
            for method in ['standard_adam', 'phi']:
                for D in ['plus', 'minus']:
                    c = create(seed, fraction, 1729, method, D, 2)
                    entries.append({'block': f'selective_R{fraction}_{method}_{D}', 'run': c['out']})
        path = f'records/confirm{seed}.json'; records.append(path)
        save(path, {'status': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE', 'seed': seed, 'outcomes': entries})
    save('records/confirmation_ready_v1.json', {'status': 'TWO_INDEPENDENT_PHI_SEED_ORDER_CONFIRMATIONS_COMPLETE',
         'seeds': [29, 43], 'shared_parent_conditioning': True, 'recovery_records': records})
    parents = {mode: {'checkpoint': path, 'acquisition_gate_passed': True} for mode,path in [('all','all.pt'),('retain_only','retain.pt')]}
    save('records/final_parents_ready.json', {'matched_R_exposure': True, 'phi_frozen_before_training': True, 'parents': parents})
    c = {'out': 'runs/references', 'data': 'data/final.jsonl', 'target_ids': targets,
         'parents': {key: p['checkpoint'] for key,p in parents.items()}}
    source = Path('runs/s17_R1.0_1729_standard_adam_plus_K2')
    metrics = json.loads((source/'result.json').read_text())['final']
    for label in ['base','parent_all','retain_only']:
        for part in ['F','R']:
            Path('runs/references').mkdir(exist_ok=True)
            Path('runs/references', f'{label}_{part}.jsonl').write_bytes((source/f'eval_0002_{part}.jsonl').read_bytes())
    save('runs/references/config.json', c)
    save('runs/references/result.json', {'status':'PASS','config':c,'base_unchanged':True,
         'base_equals_drop_adapter':True,'results':{label:metrics for label in ['base','drop_adapter','parent_all','retain_only']}})
    return formal


def main():
    tests = {}
    previous = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix='gaosi_selective_stats_') as tmp:
            os.chdir(tmp)
            formal = fixture()
            p = summary.summarize('primary', draws=100)
            f = summary.summarize('full', draws=100)
            assert (p['primary_run_count'],p['separate_K64_diagnostic_count']) == (24,8)
            assert (f['primary_run_count'],f['separate_K64_diagnostic_count']) == (40,8)
            assert not f['overall_experiment_complete']
            for r in f['contrasts']:
                expected = .125 if r['left'][0]=='phi' and r['right'][0]=='standard_adam' else (.5 if r['query_part']=='F' else 0.)
                assert math.isclose(r['difference'],expected)
                assert not r['direction_implies_selective_success']
            tests['simulated_primary24_three_seed40_and_separate8_K64_exact_paired_effects'] = 'PASS'
            assert all(r['final.R.n']==448 and r['final.F.n']==64 for r in f['raw_runs'])
            assert {r['pool'] for r in f['raw_runs']}=={112,448}
            tests['quarter_R_support_keeps_full448_R_and64_F_query_denominators'] = 'PASS'
            summary.write_summary(f,'export')
            assert all(Path('export',x).exists() for x in ['result.json','runs.csv','contrasts.csv','references.json'])
            assert f['base_drop_reference_is_shared'] and f['references']['base']==f['references']['drop_adapter']
            tests['references_shared_without_extra_replicates_and_all_metrics_exported'] = 'PASS'
            assert all(r['independent_phi_training_seeds']==3 for r in f['contrasts'] if r['scope']=='all_three_phi_seeds')
            assert all(r['independent_phi_training_seeds']==1 for r in f['contrasts'] if r['scope']=='primary_three_corruptions')
            tests['noise_not_training_seed_and_seed17_separate_from_confirmations'] = 'PASS'
            root=Path('runs/s17_R0.25_1729_phi_minus_K2')
            left=load_selective(str(root),17,2,2,{})
            right=load_selective('runs/s17_R0.25_1729_standard_adam_plus_K2',17,2,2,{})
            wrong=copy.deepcopy(left); wrong['initial_sha256']='changed'
            rejects(lambda:pair(wrong,right,'F','strict_EM'),'paired state')
            wrong=copy.deepcopy(left); wrong['updates'][0]['route_tokens']['R']=1024
            rejects(lambda:pair(wrong,right,'R','candidate_accuracy'),'identical R/language')
            tests['same_initial_bytes_and_R_language_exposure_pairing_required'] = 'PASS'
            events=root/'events.jsonl'; text=events.read_text(); rows=[json.loads(s) for s in text.splitlines()]
            rows[0]['route_tokens']['F']=1; events.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            rejects(lambda:load_selective(str(root),17,2,2,{}),'target support')
            events.write_text(text)
            tests['Dminus_target_support_tokens_rejected'] = 'PASS'
            file=root/'eval_0002_R.jsonl'; text=file.read_text(); file.write_text(''.join(text.splitlines(True)[:112]))
            rejects(lambda:load_selective(str(root),17,2,2,{}),'denominator')
            file.write_text(text)
            tests['query_pool_shrinkage_cannot_hide_forgetting'] = 'PASS'
            bad=copy.deepcopy(formal); bad['selective_outcomes'][0]['primary']=False
            save('records/formal_recovery_ready_v2.json',bad)
            rejects(lambda:summary.collect('primary'),'primary classification')
            save('records/formal_recovery_ready_v2.json',formal)
            bad=copy.deepcopy(formal); bad['selective_outcomes'].pop()
            save('records/formal_recovery_ready_v2.json',bad)
            rejects(lambda:summary.collect('primary'),'Missing/extra')
            save('records/formal_recovery_ready_v2.json',formal)
            tests['primary_K_classification_and_complete_grid_enforced'] = 'PASS'
            file=Path('runs/references/result.json'); ref=json.loads(file.read_text()); bad=copy.deepcopy(ref)
            bad['results']['drop_adapter']['F']['candidate_accuracy']=0
            save(file,bad)
            rejects(lambda:summary.collect('primary'),'base/drop-adapter')
            save(file,ref)
            tests['base_drop_alias_mismatch_rejected'] = 'PASS'
            phi=Path('phi17.pt'); before=phi.read_bytes(); phi.write_bytes(before+b'changed')
            rejects(lambda:load_selective(str(root),17,2,2,{}),'phi bytes')
            tests['frozen_phi_bytes_verified_before_statistics'] = 'PASS'
    finally:
        os.chdir(previous)
    result={'status':'PASS','scope':'Pure CPU read-only statistics with simulated F64/R448 grids; no model or effect evidence',
            'tests':tests,'count':len(tests)}
    save('checks/selective_statistics_cpu.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
