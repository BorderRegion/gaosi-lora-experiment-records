"""Freeze continuation rates on dev, then run pure-R and entity diagnostics."""
import argparse
import copy
import json
from pathlib import Path
import random

from final_parent_queue import last_evaluation
from formal_recovery_queue import execute, intact_manifest
from organization_queue import fit_parent, wait_for
from phi_development_queue import preserve_json


POLICY = {
    'version': 1,
    'rate_selection': 'Top two distinct stock-Adam recovery rates on the existing dev medium-dose grid, evaluated at the frozen primary K; mean candidate/EM minus language residual above .2. Tie prefers lower rate. No formal or reflow outcomes select rates.',
    'continuation': 'Fresh stock Adam, CE(selected support) with mass1, clip norm1; no extra noise, QR, phi, language gradient, or F gradient in pure-R mode.',
    'budgets': [20, 80],
    'pure_R': 'Every primary-K full/quarter-R D-minus Adam/phi endpoint, three paired noise seeds, continues on the SAME COMPLETE R pool. One matched retain-only reference per rate, shared across origins, not independent noise replicates.',
    'entity': 'One fixed entity: lexicographically first entity in the final parent. Withhold all eight attributes; select two taught IDs by seed91 before seeing outcomes and evaluate the other six separately. This is a single entity diagnostic, not population evidence.',
    'entity_parents': 'New matched all/retain-only pair on the SAME final library; .5 entity-F + .5 other-R + .25 language versus zero F; same R/optimizer initialization/seed/budget, 256 each. All-parent entity-F also needs candidate>=.85 and strictEM>=.75 with all8 queries; both extend to512 once if either gate fails.',
    'entity_recovery': 'Medium q and true frozen primary K; Adam/phi D-minus on the same corrupted bytes; three noise seeds; full R.',
    'entity_reteaching': 'Only two taught support bindings; no six withheld labels or language gradients. Same 20/80 budget and frozen rates for phi-Dminus, Adam-Dminus and matched entity-retain-only.',
    'evaluation': 'F, all R, taught2/untaught6 where applicable, strict generation, candidate scores, abnormal output, language; checkpoints at20/80 on one80-step trajectory.',
    'formal_data_used_for_policy_or_rates': False,
    'not_completed_here': ['continual organizations', '7B phi transfer/local training', 'independent phi training confirmations', 'base-noise boundary', 'conditional real-text task'],
}


def freeze_rates(calibration):
    trials = []
    K = calibration['recovery_K']
    for rate, folder in calibration['trials'].items():
        result = json.loads((Path(folder) / 'result.json').read_text())
        c = result['config']
        if (result['status'] != 'PASS' or not result['base_unchanged']
                or c['method'] != 'standard_adam' or c['phase'] != 'development'
                or c['parent'] != calibration['development_parent']
                or c['data'] != 'data/synthetic/dev_parent.jsonl'
                or c['q_apply'] != calibration['q_medium'] or float(rate) != c['inner_lr']):
            raise ValueError('Reflow rates must come from the matching dev stock-Adam grid')
        events = [json.loads(line) for line in (Path(folder) / 'events.jsonl').read_text().splitlines()]
        endpoint = next(event['metrics'] for event in events
                        if event['event'] == 'evaluation' and event['step'] == K)
        residual = endpoint['language']['nll'] - result['clean']['language']['nll']
        score = .5 * (endpoint['task']['candidate_accuracy'] + endpoint['task']['strict_EM'])
        score -= max(0., residual - .2)
        trials.append({'lr': float(rate), 'score': score, 'K': K, 'metrics': endpoint,
                       'language_residual_nats': residual, 'run': folder})
    ranked = sorted(trials, key=lambda trial: (-trial['score'], trial['lr']))
    if len({trial['lr'] for trial in ranked}) < 2:
        raise ValueError('Need at least two distinct tested development rates')
    selected = []
    for trial in ranked:
        if trial['lr'] not in selected:
            selected.append(trial['lr'])
        if len(selected) == 2:
            break
    record = {'status': 'REFLOW_LRS_FROZEN', 'policy': POLICY, 'lrs': selected,
              'recovery_K': K, 'development_parent': calibration['development_parent'],
              'q_medium': calibration['q_medium'], 'trials': trials,
              'formal_data_used': False, 'reflow_outcomes_used': False,
              'rate_scope': 'Transferred from dev recovery; no claim of optimized reflow-specific rates'}
    preserve_json('records/reflow_lrs_frozen_v1.json', record)
    return record


def entity_parents(ready, freeze_file):
    datafile = ready['parents']['all']['config']['data']
    data = [json.loads(line) for line in Path(datafile).read_text().splitlines() if line.strip()]
    entity = min(row['entity'] for row in data)
    target_ids = sorted({row['fact_id'] for row in data if row['entity'] == entity})
    retain_ids = sorted({row['fact_id'] for row in data} - set(target_ids))
    taught_ids = target_ids.copy()
    random.Random(91).shuffle(taught_ids)
    taught_ids = sorted(taught_ids[:2])
    if len(target_ids) != 8 or len(retain_ids) != 504:
        raise ValueError('Entity request expects the fixed 64x8 final library')
    plan = {'status': 'POST_FREEZE_ENTITY_PARENT_PLAN', 'policy': POLICY,
            'phi_freeze_manifest': freeze_file, 'data': datafile, 'entity': entity,
            'target_ids': target_ids, 'retain_ids': retain_ids, 'taught_ids': taught_ids,
            'untaught_ids': sorted(set(target_ids) - set(taught_ids)),
            'entity_selected_by_outcome': False, 'initial_steps': 256, 'one_matched_extension': 512}
    preserve_json('records/entity_reflow_parent_plan_v1.json', plan)
    template = copy.deepcopy(ready['parents']['all']['config'])
    for key in ['out', 'continue_from', 'query_data', 'eval_limit']:
        template.pop(key, None)
    retainfile = Path('data/synthetic/final_entity_retain_only_v1.jsonl')
    text = ''.join(json.dumps(row) + '\n' for row in data if row['fact_id'] in set(retain_ids))
    if retainfile.exists() and retainfile.read_text() != text:
        raise ValueError('Entity retain reference data changed')
    retainfile.write_text(text)
    template.update(target_ids=target_ids, phi_freeze_manifest=freeze_file, steps=256,
                    max_epochs=0, eval_steps=[16, 32, 64, 128, 256], stop_on_acquisition=False,
                    purpose='fixed_entity_request_matched_all_and_retain_only')
    outcomes = {}
    for mode in ['all', 'retain_only']:
        c = dict(template, parent_mode=mode)
        if mode == 'retain_only':
            c['query_data'] = str(retainfile)
        outcomes[mode] = fit_parent(f'parent_3b_entity_{mode}_matched_s17_observed_blocking_v8_v1', c)
    target_gate = {}

    def acquired():
        all_result = outcomes['all']
        predictions = [json.loads(line) for line in
                       (Path(all_result['config']['out']) / f'eval_{all_result["steps"]:04d}.jsonl').read_text().splitlines()]
        measured = entity_target_gate(predictions, target_ids)
        target_gate.update(measured)
        return all(result['acquisition_gate_passed'] for result in outcomes.values()) and measured['passed']

    if not acquired():
        for mode, previous in list(outcomes.items()):
            c = dict(previous['config'], steps=512, continue_from=previous['checkpoint'],
                     eval_steps=[512], purpose='single_matched_entity_parent_extension')
            outcomes[mode] = fit_parent(f'parent_3b_entity_{mode}_matched_extend512_s17_observed_blocking_v8', c)
    if (not acquired() or not all(result['base_unchanged'] for result in outcomes.values())
            or outcomes['all']['fact_stream'] != outcomes['retain_only']['fact_stream']
            or outcomes['all']['config']['steps'] != outcomes['retain_only']['config']['steps']):
        raise RuntimeError('Entity acquisition or matched R exposure failed; preserve negative result')
    record = {'status': 'ENTITY_PARENTS_READY', 'parents': outcomes, 'request': plan,
              'metrics': {key: last_evaluation(result) for key, result in outcomes.items()},
              'entity_target_gate': target_gate,
              'matched_R_exposure': True, 'phi_frozen_before_training': True}
    preserve_json('records/entity_reflow_parents_ready_v1.json', record)
    return record


def entity_target_gate(predictions, targets):
    targets = set(targets)
    chosen = [row for row in predictions if row['fact_id'] in targets]
    if len(chosen) != 8 or {row['fact_id'] for row in chosen} != targets:
        return {'passed': False, 'n': len(chosen), 'reason': 'Need all eight distinct F queries'}
    accuracy = sum(row['rank_correct'] for row in chosen) / 8
    em = sum(row['strict_EM'] for row in chosen) / 8
    return {'passed': accuracy >= .85 and em >= .75, 'n': 8,
            'candidate_accuracy': accuracy, 'strict_EM': em}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--calibration-job', default='calibration_queue_v3')
    parser.add_argument('--formal-job', default='formal_recovery_queue_observed_blocking_v8')
    args = parser.parse_args()
    preserve_json('records/reflow_policy_v1.json', POLICY)
    if json.loads(Path('checks/reflow_v2_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Independent reflow, data restriction and restart checks must pass')
    calibration = wait_for('records/calibration_frozen.json', args.calibration_job)
    if (calibration['status'] != 'CALIBRATION_FROZEN' or calibration['formal_data_used']
            or not calibration['ordinary_recovery_validated']):
        raise ValueError('Reflow rates require valid development calibration')
    rates = freeze_rates(calibration)
    formal = wait_for('records/formal_recovery_ready_v2.json', args.formal_job)
    if formal['status'] != 'FORMAL_RECOVERY_GRID_COMPLETE_REFLOW_PENDING':
        raise ValueError('Need complete preserved selective comparisons before continuation')
    ready = json.loads(Path('records/final_parents_ready.json').read_text())
    freeze_file = formal['plan']['freeze_manifests']['mixed']
    frozen = intact_manifest(freeze_file, 'mixed')
    if (frozen['recovery_K'] != rates['recovery_K']
            or frozen['development_parent'] != rates['development_parent']):
        raise ValueError('Continuation settings differ from the frozen recovery development')
    plan = {'status': 'FROZEN_REFLOW_PLAN', 'policy': POLICY, 'formal_plan': formal['plan'],
            'rate_manifest': 'records/reflow_lrs_frozen_v1.json', 'rates': rates['lrs'],
            'primary_K': frozen['recovery_K'], 'noise_seeds': [1729, 1730, 1731],
            'retain_fractions': [1., .25], 'budgets': POLICY['budgets']}
    preserve_json('records/reflow_plan_v1.json', plan)
    Path('records/reflow_completed').mkdir(parents=True, exist_ok=True)
    all_parent = ready['parents']['all']
    template = {'out': '', 'freeze_manifest': freeze_file,
                'lr_freeze_manifest': 'records/reflow_lrs_frozen_v1.json',
                'parent': all_parent['checkpoint'], 'source_checkpoint': '', 'source_kind': 'selective',
                'data': all_parent['config']['data'], 'target_ids': ready['target_ids'], 'taught_ids': [],
                'mode': 'pure_R', 'inner_lr': 0., 'device': 'musa', 'data_seed': 17,
                'K': 80, 'eval_steps': [20, 80], 'effective_tokens': 512,
                'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096}
    outcomes = []

    def continue_source(label, config, metadata):
        for rate in rates['lrs']:
            name = f'reflow_{label}_lr{rate:g}_s17_observed_blocking_v8'
            result = execute(name, dict(config, inner_lr=rate), 'gaosi.reflow')
            events = [json.loads(line) for line in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
            curves = {str(event['step']): event['metrics'] for event in events
                      if event['event'] == 'evaluation' and event['step'] in [0, 20, 80]}
            if set(curves) != {'0', '20', '80'}:
                raise ValueError('Continuation is missing a declared 0/20/80 measurement')
            entry = dict(metadata, lr=rate, run=result['config']['out'],
                         source_sha256=result['source_sha256'], final=result['final'], initial=result['initial'],
                         curves=curves)
            outcomes.append(entry)
            preserve_json(f'records/reflow_completed/{name}.json', entry)

    selected = [outcome for outcome in formal['selective_outcomes']
                if outcome['primary'] and outcome['D'] == 'minus']
    expected = {(fraction, method, seed) for fraction in [1., .25]
                for method in ['standard_adam', 'phi'] for seed in [1729, 1730, 1731]}
    actual = {(row['retain_fraction'], row['method'], row['noise_seed']) for row in selected}
    if len(selected) != len(expected) or actual != expected:
        raise ValueError('Incomplete primary selective source grid')
    for outcome in selected:
        label = f'pureR_originR{round(outcome["retain_fraction"]*100)}_{outcome["method"]}_n{outcome["noise_seed"]}'
        continue_source(label, dict(template, source_checkpoint=outcome['checkpoint']),
                        {'request': 'pure_R', 'origin': outcome})
    continue_source('pureR_matched_retain_only', dict(template, source_kind='retain_only',
                    source_checkpoint=ready['parents']['retain_only']['checkpoint']),
                    {'request': 'pure_R', 'origin': 'matched_retain_only', 'shared_reference': True})

    entity = entity_parents(ready, freeze_file)
    request = entity['request']
    parent = entity['parents']['all']
    entity_template = dict(template, parent=parent['checkpoint'], target_ids=request['target_ids'],
                           mode='reteach_two', taught_ids=request['taught_ids'])
    entity_recovery = {'out': '', 'phase': 'formal', 'freeze_manifest': freeze_file,
        'parent': parent['checkpoint'], 'organization': 'O1', 'device': 'musa',
        'architecture': frozen['architecture'], 'data_seed': 17, 'noise_seed': 1729,
        'q_apply': frozen['q_medium'], 'noise_scheme': 'subspace', 'data': request['data'],
        'task_layout': 'selective', 'target_ids': request['target_ids'], 'D': 'minus',
        'retain_subset_fraction': 1., 'subset_seed': 91, 'method': 'standard_adam',
        'language_eval': 'data/language/final.jsonl', 'language_eval_tokens': 4096,
        'inner_lr': frozen['recovery_lr'], 'effective_tokens': 512, 'retain_weight': .25,
        'K': frozen['recovery_K'], 'eval_steps': [step for step in [8,16,32,64] if step <= frozen['recovery_K']]}
    for seed in [1729, 1730, 1731]:
        initial = None
        for method in ['standard_adam', 'phi']:
            c = dict(entity_recovery, noise_seed=seed, method=method)
            if initial:
                c['initial_adapter'] = initial
            if method == 'phi':
                c['phi'] = frozen['phi']
            name = f'entity_Dminus_{method}_n{seed}_K{frozen["recovery_K"]}_s17_observed_blocking_v8'
            result = execute(name, c)
            if initial is None:
                initial = str(Path(result['config']['out']) / 'initial.pt')
            continue_source(f'entity_reteach2_{method}_n{seed}',
                            dict(entity_template, source_checkpoint=result['checkpoint']),
                            {'request': 'reteach_two', 'method': method, 'noise_seed': seed,
                             'entity_request': request, 'source_run': result['config']['out']})
    continue_source('entity_reteach2_matched_retain_only', dict(entity_template, source_kind='retain_only',
                    source_checkpoint=entity['parents']['retain_only']['checkpoint']),
                    {'request': 'reteach_two', 'origin': 'matched_retain_only', 'shared_reference': True,
                     'entity_request': request})
    preserve_json('records/reflow_ready_v1.json', {'status': 'REFLOW_AND_ENTITY_DIAGNOSTICS_COMPLETE',
                  'plan': plan, 'outcomes': outcomes, 'entity': entity,
                  'independent_phi_training_seeds': 1, 'remaining': POLICY['not_completed_here']})


if __name__ == '__main__':
    main()
