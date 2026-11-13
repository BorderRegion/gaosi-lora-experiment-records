"""Dev-frozen schedules, base probes, and four organizations x four methods."""
import argparse
import hashlib
import json
from pathlib import Path
import random

from formal_recovery_queue import execute
from organization_queue import wait_for
from phi_development_queue import preserve_json


POLICY = {
    'version': 1,
    'organizations': ['O1', 'O2', 'O3', 'O4'],
    'methods': ['clean_Adam', 'noise_Adam', 'clean_phi', 'noise_phi'],
    'pilot': 'O1 stock Adam clean/noise, first two dev fact worlds sorted by ID; schedules 32/16 then64/32 then128/64 if previous schedule is insufficient. All formal arms use the same dev-selected schedule.',
    'pilot_adequacy': 'Both doses: mean day new-task candidate>=.85 and strictEM>=.75; mean night new-task candidate/EM no more than .10 below day. At maximum budget proceed with an explicit adequacy-limited warning, no sufficient-training claim.',
    'day_lr': 'Frozen ordinary-capacity LR; never select on formal performance',
    'night_lr': 'Frozen recovery LR; never inflate Adam LR to induce forgetting',
    'rounds': 8, 'new_bindings_per_round': 32, 'replay_capacity': 128,
    'world_order': 'First eight final fact worlds sorted by ID, shuffled by seed17',
    'probe_worlds': 'Last four final fact worlds sorted by ID, disjoint from the eight stream worlds',
    'probes': 'Rounds0/4/8, common extra rank16, four worlds, 1/4/16 steps; original W0 uses the same probes once as a shared reference.',
    'cost': 'Same fixed parent query prompt: two warmups/five measured prefill forwards, adapter enabled/disabled on same underlying base; actual checkpoint and adapter bytes, merge count and cumulative-rank bound.',
    'O4': 'Persistent FP32 base master; initial parent merged before round0, nonzero day adapter perturbed/recovered before merge/reset at each later boundary.',
    'seed': 17, 'replay_seed': 912, 'probe_seed': 37,
    'starting_parent': 'Shared verified final all-parent, after all phi freezing; independent known library from stream/probes',
    'independent_phi_training_seeds': 1,
    'scope': 'Sixteen eight-round pilot trajectories. Two further training seeds/orders for key comparisons remain required.',
}


def valid_freezes(record):
    if record['status'] != 'ALL_PHI_FROZEN':
        raise ValueError('Need all organization phi frozen before continual development/formal use')
    frozen = {}
    for organization in POLICY['organizations']:
        file = record['manifests'][organization]
        f = json.loads(Path(file).read_text())
        if (f['status'] != 'FROZEN' or f['formal_data_used'] or not f['stopping_rule_frozen']
                or f['organization'] != organization or f['training_noise'] != 'mixed'
                or hashlib.sha256(Path(f['phi']).read_bytes()).hexdigest() != f['phi_sha256']):
            raise ValueError(f'Invalid organization freeze: {organization}')
        frozen[organization] = f
    for f in frozen.values():
        if any(f[key] != frozen['O1'][key] for key in
               ['architecture', 'q_medium', 'capacity_lr', 'recovery_lr', 'rank', 'targets', 'scale']):
            raise ValueError('Organization freezes must share the development setting')
    return frozen


def pilot_adequacy(outcomes):
    diagnostic = []
    for outcome in outcomes:
        events = [json.loads(line) for line in
                  (Path(outcome['config']['out']) / 'events.jsonl').read_text().splitlines()]
        day = [event['metrics'] for event in events if event['event'] == 'new_day_evaluation']
        night = [event['metrics'] for event in events if event['event'] == 'new_night_evaluation']
        if len(day) != 2 or len(night) != 2:
            raise ValueError('Continual development pilot must measure both complete rounds')
        mean = lambda rows, key: sum(row[key] for row in rows) / len(rows)
        dc, de = mean(day, 'candidate_accuracy'), mean(day, 'strict_EM')
        nc, ne = mean(night, 'candidate_accuracy'), mean(night, 'strict_EM')
        diagnostic.append({'run': outcome['config']['out'], 'day_candidate': dc, 'day_EM': de,
                           'night_candidate': nc, 'night_EM': ne,
                           'adequate': dc >= .85 and de >= .75 and nc >= dc-.10 and ne >= de-.10})
    return all(item['adequate'] for item in diagnostic), diagnostic


def select_worlds(manifest):
    worlds = sorted([world for world in manifest['worlds'] if world['split'] == 'final' and world['kind'] == 'fact'],
                    key=lambda world: world['world_id'])
    if len(worlds) != 16:
        raise ValueError('Continual protocol expects sixteen available final fact worlds')
    stream = worlds[:8]
    random.Random(POLICY['seed']).shuffle(stream)
    probes = worlds[-4:]
    assert not {world['world_id'] for world in stream} & {world['world_id'] for world in probes}
    return ([str(Path('data/synthetic') / world['file']) for world in stream],
            [str(Path('data/synthetic') / world['file']) for world in probes])


def validate_stream_data(parent_data, worlds, probes):
    seen=set()
    counts={}
    for index,file in enumerate([parent_data,*worlds,*probes]):
        data=[json.loads(line) for line in Path(file).read_text().splitlines() if line.strip()]
        support={row['fact_id'] for row in data if row['role']=='support'}
        query={row['fact_id'] for row in data if row['role']=='query' and row['sample_id'].endswith(':0')}
        expected=512 if index==0 else 32
        if len(support)!=expected or support!=query or support & seen:
            raise ValueError('Stream/probe/parent bindings must be disjoint, fully queried, and have fixed sizes')
        seen.update(support)
        counts[file]=len(support)
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--organization-job', default='organization_queue_recompute_v7')
    parser.add_argument('--final-parent-job', default='final_parent_queue_recompute_v7')
    parser.add_argument('--reflow-job', default='reflow_queue_recompute_v7')
    args = parser.parse_args()
    preserve_json('records/continual_policy_v1.json', POLICY)
    if json.loads(Path('checks/continual_v2_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Continual runtime checks must pass before scheduling real trajectories')
    if json.loads(Path('checks/continual_schedule_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Continual schedule and base-reference checks must pass')
    if json.loads(Path('checks/continual_data_isolation_cpu.json').read_text())['status'] != 'PASS':
        raise RuntimeError('Stream/probe/parent binding isolation checks must pass')
    all_frozen = wait_for('records/all_phi_frozen.json', args.organization_job)
    frozen = valid_freezes(all_frozen)
    f = frozen['O1']
    parent = next(result for result in json.loads(Path('records/parents_ready.json').read_text())
                  if result['config']['data'] == 'data/synthetic/dev_parent.jsonl')
    manifest = json.loads(Path(f['world_manifest']).read_text())
    dev_worlds = sorted([world for world in manifest['worlds'] if world['split']=='dev' and world['kind']=='fact'],
                        key=lambda world:world['world_id'])[:2]
    if len(dev_worlds) != 2:
        raise ValueError('Need two predefined dev fact worlds')
    template = {'out':'', 'phase':'development', 'freeze_manifest':all_frozen['manifests']['O1'],
        'parent':parent['checkpoint'], 'parent_data':parent['config']['data'],
        'worlds':[str(Path('data/synthetic')/world['file']) for world in dev_worlds], 'rounds':2,
        'seed':POLICY['seed'], 'replay_seed':POLICY['replay_seed'], 'probe_seed':POLICY['probe_seed'],
        'organization':'O1', 'architecture':f['architecture'], 'night_method':'standard_adam', 'q_apply':0.,
        'day_steps':32, 'night_steps':16, 'day_lr':f['capacity_lr'], 'night_lr':f['recovery_lr'],
        'seq':512, 'effective_tokens':512, 'device':'musa', 'probe_worlds':[],
        'language_eval':'data/language/dev.jsonl', 'language_eval_tokens':4096, 'measure_inference':False}
    development_plan = {'status':'CONTINUAL_DEVELOPMENT_PLAN', 'policy':POLICY,
                        'template':template, 'schedule_candidates':[[32,16],[64,32],[128,64]],
                        'formal_data_used':False}
    preserve_json('records/continual_development_plan_v1.json', development_plan)
    trials = []
    adequate = False
    for day_steps, night_steps in development_plan['schedule_candidates']:
        outcomes = []
        for label, q in [('clean',0.),('medium',f['q_medium'])]:
            config = dict(template, day_steps=day_steps, night_steps=night_steps, q_apply=q)
            name=f'continual_dev_O1_Adam_{label}_d{day_steps}_n{night_steps}_s17_v1'
            outcomes.append(execute(name,config,'gaosi.continual'))
        adequate, metrics = pilot_adequacy(outcomes)
        trials.append({'day_steps':day_steps, 'night_steps':night_steps, 'adequate':adequate, 'metrics':metrics})
        if adequate:
            break
    budgets = {'status':'CONTINUAL_BUDGET_FROZEN', 'day_steps':day_steps, 'night_steps':night_steps,
               'day_lr':f['capacity_lr'], 'night_lr':f['recovery_lr'], 'development_trials':trials,
               'development_adequate':adequate, 'formal_data_used':False,
               'adequacy_limited_at_maximum_budget':not adequate, 'policy':POLICY}
    preserve_json('records/continual_budget_frozen_v1.json',budgets)
    ready = wait_for('records/final_parents_ready.json',args.final_parent_job)
    # Complete the paired recovery/relearning block before the sixteen long
    # trajectories, without imposing a success condition on any method.
    reflow = wait_for('records/reflow_ready_v1.json',args.reflow_job)
    if ready['status']!='FINAL_PARENTS_READY' or reflow['status']!='REFLOW_AND_ENTITY_DIAGNOSTICS_COMPLETE':
        raise ValueError('Incomplete preceding formal comparisons')
    worlds, probes = select_worlds(manifest)
    parent = ready['parents']['all']
    if not parent['acquisition_gate_passed'] or not parent['base_unchanged']:
        raise ValueError('Continual starting parent must have acquired its independent known library')
    source_files = [parent['config']['data'], *worlds, *probes,
                    'data/language/inner.jsonl','data/language/final.jsonl']
    counts=validate_stream_data(parent['config']['data'],worlds,probes)
    plan = {'status':'FROZEN_CONTINUAL_SCHEDULE','policy':POLICY,'phi_frozen_before_selection':True,
            'worlds':worlds, 'probe_worlds':probes, 'parent':parent['checkpoint'],
            'parent_data':parent['config']['data'], 'day_steps':day_steps, 'night_steps':night_steps,
            'day_lr':f['capacity_lr'], 'night_lr':f['recovery_lr'],
            'seed':POLICY['seed'],'replay_seed':POLICY['replay_seed'],'probe_seed':POLICY['probe_seed'],
            'seq':512,'effective_tokens':512,'language_eval':'data/language/final.jsonl',
            'language_eval_tokens':4096,'measure_inference':True,'inference_repetitions':5,
            'data_sha256':{file:hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in source_files},
            'binding_counts':counts,'stream_probe_parent_bindings_disjoint':True,
            'budget_freeze':'records/continual_budget_frozen_v1.json', 'development_adequate':adequate}
    preserve_json('records/continual_schedule_frozen_v1.json',plan)
    base_config={'freeze_manifest':all_frozen['manifests']['O1'],
                 'schedule_manifest':'records/continual_schedule_frozen_v1.json','parent':parent['checkpoint'],
                 'probe_worlds':probes,'seq':512,'effective_tokens':512,'device':'musa','inference_repetitions':5}
    reference=execute('continual_base_rank16_reference_s17_v1',base_config,'gaosi.capacity_reference')
    Path('records/continual_completed').mkdir(parents=True,exist_ok=True)
    outcomes=[]
    for organization in POLICY['organizations']:
        for label in POLICY['methods']:
            c=dict(template,phase='formal',freeze_manifest=all_frozen['manifests'][organization],
                   schedule_manifest='records/continual_schedule_frozen_v1.json',parent=parent['checkpoint'],
                   parent_data=parent['config']['data'],worlds=worlds,probe_worlds=probes,rounds=8,
                   day_steps=day_steps,night_steps=night_steps,organization=organization,
                   night_method='phi' if label.endswith('phi') else 'standard_adam',
                   q_apply=f['q_medium'] if label.startswith('noise') else 0.,
                   language_eval='data/language/final.jsonl',measure_inference=True,inference_repetitions=5)
            if c['night_method']=='phi':
                c['phi']=frozen[organization]['phi']
            name=f'continual_{organization}_{label}_8round_s17_v1'
            result=execute(name,c,'gaosi.continual')
            entry={'organization':organization,'method':label,'run':result['config']['out'],
                   'completed_rounds':result['completed_rounds'],'result':result}
            outcomes.append(entry)
            preserve_json(f'records/continual_completed/{name}.json',entry)
    preserve_json('records/continual_pilot_ready_v1.json',{'status':'SIXTEEN_EIGHT_ROUND_PILOTS_COMPLETE',
                  'plan':plan,'base_reference':reference,'outcomes':outcomes,
                  'independent_phi_training_seeds':1,'independent_orders':1,
                  'remaining':['Two independent training seeds/orders for key contrasts','7B phi transfer/local training',
                               'base-noise boundary','conditional real-text task']})


if __name__=='__main__':
    main()
