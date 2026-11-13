"""Post-freeze all/retain-only parents with equal R support and language exposure."""
import argparse
import copy
import json
from pathlib import Path

from organization_queue import fit_parent, wait_for
from phi_development_queue import preserve_json


def last_evaluation(result):
    file = Path(result['config']['out']) / 'events.jsonl'
    events = [json.loads(s) for s in file.read_text().splitlines()]
    return [e for e in events if e['event'] == 'evaluation'][-1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--organization-job', default='organization_queue_v1')
    args = parser.parse_args()
    assert json.loads(Path('checks/final_parent_slots_cpu.json').read_text())['status'] == 'PASS'
    frozen = wait_for('records/all_phi_frozen.json', args.organization_job)
    assert frozen['status'] == 'ALL_PHI_FROZEN'
    freeze_file = frozen['manifests']['O1']
    calibration = json.loads(Path(freeze_file).read_text())
    assert calibration['status'] == 'FROZEN'
    parents = json.loads(Path('records/parents_ready.json').read_text())
    template = copy.deepcopy(next(r['config'] for r in parents if r['config']['data'].endswith('dev_parent.jsonl')))
    for key in ['continue_from', 'eval_limit']:
        template.pop(key, None)
    manifest = json.loads(Path(calibration['world_manifest']).read_text())
    target_ids = manifest['parents']['final_parent']['target_ids']
    retain_ids = manifest['parents']['final_parent']['retain_ids']
    assert len(target_ids) == 64 and len(retain_ids) == 448 and not set(target_ids) & set(retain_ids)
    plan = {'status': 'POST_FREEZE_FINAL_PARENT_PLAN', 'phi_freeze_record': 'records/all_phi_frozen.json',
            'phi_freeze_manifest': freeze_file, 'all_target_ids': target_ids, 'all_retain_ids': retain_ids,
            'parent_layout': 'selective', 'all_loss': '.5 F + .5 R + .25 language',
            'retain_only_loss': '0 F + .5 R + .25 language',
            'same_support_streams_seed_and_tokens': True, 'initial_steps': 256,
            'extension': 'If either acquisition gate fails, continue BOTH once to total512; no unequal early stop.',
            'query_not_training_labels': True}
    preserve_json('records/final_parent_training_plan.json', plan)
    source = Path('data/synthetic/final_parent.jsonl')
    data = [json.loads(line) for line in source.read_text().splitlines()]
    ids = {row['fact_id'] for row in data}
    assert ids == set(target_ids + retain_ids)
    retain_data = [row for row in data if row['fact_id'] in set(retain_ids)]
    retain_file = Path('data/synthetic/final_parent_retain_only.jsonl')
    text = ''.join(json.dumps(row) + '\n' for row in retain_data)
    if retain_file.exists() and retain_file.read_text() != text:
        raise ValueError('Existing final retain subset differs')
    retain_file.write_text(text)
    template.update(data=str(source), parent_layout='selective', target_ids=target_ids,
                    phi_freeze_manifest=freeze_file, steps=256, max_epochs=0,
                    eval_steps=[16, 32, 64, 128, 256], stop_on_acquisition=False,
                    purpose='formal_parent_after_all_phi_frozen_matched_R_exposure')
    outcomes = {}
    for mode in ['all', 'retain_only']:
        c = dict(template, parent_mode=mode)
        if mode == 'retain_only':
            c['query_data'] = str(retain_file)
        outcomes[mode] = fit_parent(f'parent_3b_final_{mode}_matched_s17', c)
    if not all(r['acquisition_gate_passed'] for r in outcomes.values()):
        for mode, previous in list(outcomes.items()):
            c = dict(previous['config'], steps=512, continue_from=previous['checkpoint'],
                     eval_steps=[512], purpose='single_matched_final_parent_extension')
            outcomes[mode] = fit_parent(f'parent_3b_final_{mode}_matched_extend512_s17', c)
    if not all(r['acquisition_gate_passed'] for r in outcomes.values()):
        raise RuntimeError('Final acquisition incomplete; preserve all results before formal comparisons')
    for key in ['fact_stream']:
        if outcomes['all'][key] != outcomes['retain_only'][key]:
            raise RuntimeError('Final R exposure differs; no formal comparison permitted')
    a = outcomes['all']['config']
    b = outcomes['retain_only']['config']
    assert a['steps'] == b['steps']
    record = {'status': 'FINAL_PARENTS_READY', 'parents': outcomes,
              'metrics': {mode: last_evaluation(r) for mode, r in outcomes.items()},
              'target_ids': target_ids, 'retain_ids': retain_ids,
              'matched_R_exposure': True, 'phi_frozen_before_training': True,
              'training_plan': 'records/final_parent_training_plan.json',
              'next': 'Frozen formal main grid, selective recovery/reflow, continual organizations and 7B remain required.'}
    preserve_json('records/final_parents_ready.json', record)
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
