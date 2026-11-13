"""Equal O1--O4 specialization plus budget-matched O1 phi_clean.

O4 construction costs are separate from the 512 phi updates. This queue freezes
phi only after all required training; final knowledge is never used here.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from phi_core_queue import run_job, train
from phi_development_queue import preserve_json


POLICY = {
    'version': 1,
    'data_scope': 'meta parents and meta new worlds only',
    'organizations': ['O1', 'O2', 'O3', 'O4'],
    'specialization_episode_K': [8] * 64,
    'specialization_known_warm_episodes': 0,
    'initialization': 'same development-selected phi and same meta-optimizer state',
    'O4': 'Merge meta-A history then fit meta-B current, and the reciprocal construction; distinct data sets, persistent FP32 historical masters, ordinary parent fitting charged separately.',
    'parent_retention': 'task + .25*(.5 language + .5 historical facts); complete binding coverage and initial historical candidate>=.85/EM>=.75; final historical NLL at most initial+.5.',
    'parent_budget': 'at most six epochs, then at most one true continuation to 512 total updates',
    'phi_clean': 'O1 from scratch, all q_train=0, same core plus 64x8 specialization schedule as selected mixed phi',
    'final_data_used': False,
}


def wait_for(artifact, job_name):
    artifact = Path(artifact)
    while not artifact.exists():
        job = Path('records/jobs') / (job_name + '.json')
        if job.exists():
            state = json.loads(job.read_text())
            if state['status'] != 'RUNNING':
                raise RuntimeError(f'{job_name} ended without {artifact}')
            proc = Path(f"/proc/{state.get('child_pid')}/stat")
            fields = proc.read_text().split() if proc.exists() else []
            if not fields or fields[2] == 'Z' or fields[21] != state.get('child_proc_start_ticks'):
                raise RuntimeError(f'{job_name} has no matching live process')
        time.sleep(30)
    return json.loads(artifact.read_text())


def fit_parent(name, config):
    c = copy.deepcopy(config)
    c['out'] = 'runs/' + name
    file = Path('configs') / (name + '.json')
    preserve_json(file, c)
    result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.train', '--config', str(file)],
                     Path(c['out']) / 'result.json')
    if result['config'] != c:
        raise ValueError('Preserved parent configuration differs')
    return result


def o4_parents(parents):
    output = []
    histories = []
    for index, historical in enumerate(parents):
        current = parents[1 - index]
        label = 'ab'[index]
        name = f'o4_historical_{label}_s17'
        master = f'runs/{name}/master.pt'
        history = run_job(name, ['/usr/bin/python', '-u', 'tools/build_o4_history.py',
                                '--parent', historical['checkpoint'], '--out', master], master + '.json')
        assert history['historical_bindings'] == historical['config']['data']
        c = copy.deepcopy(current['config'])
        c.pop('continue_from', None)
        c.update(organization='O4', historical_base=master, retain_data=historical['config']['data'],
                 steps=256, eval_steps=[16, 32, 64, 128, 256], max_epochs=6,
                 eval_limit=512, stop_on_acquisition=True, purpose='independent_current_adapter_on_merged_history')
        name = f'o4_current_{"ba"[index]}_on_history_{label}_s17'
        result = fit_parent(name, c)
        if not result['acquisition_gate_passed']:
            c.update(continue_from=result['checkpoint'], max_epochs=0, steps=512,
                     eval_steps=[128, 256, 512], purpose='single_O4_current_parent_acquisition_extension')
            result = fit_parent(name + '_extend512', c)
        initial = result['initial_keep']
        if (not result['acquisition_gate_passed'] or initial['candidate_accuracy'] < .85
                or initial['strict_EM'] < .75):
            raise RuntimeError('O4 requires both acquired current facts and a measurable historical parent')
        output.append(result)
        histories.append(history)
    record = {'status': 'PASS', 'parents': output, 'histories': histories,
              'construction_cost_is_additional': True, 'formal_data_used': False}
    preserve_json('records/o4_parents_ready.json', record)
    return output, histories


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--development-job', default='phi_development_queue_observed_blocking_v8')
    args = p.parse_args()
    preserve_json('records/organization_training_policy.json', POLICY)
    check = json.loads(Path('checks/organization_parents_cpu.json').read_text())
    if check['status'] != 'PASS':
        raise RuntimeError('Organization parent integration checks must pass first')
    selected = wait_for('records/phi_architecture_selected.json', args.development_job)
    assert selected['status'] == 'ARCHITECTURE_SELECTED_PENDING_ORGANIZATION_ADAPTATION'
    calibration = json.loads(Path('records/calibration_frozen.json').read_text())
    parents = sorted([r for r in json.loads(Path('records/parents_ready.json').read_text())
                      if 'meta_parent_' in r['config']['data']], key=lambda r: r['config']['data'])
    assert len(parents) == 2 and all(r['acquisition_gate_passed'] for r in parents)
    architecture = selected['architecture']
    core = json.loads(Path(f'configs/phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8.json').read_text())
    core_schedule = core['episode_K'][:]
    if selected['core_inner_steps'] == 3584:
        core_schedule += [16] * 64
    assert sum(core_schedule) == selected['core_inner_steps']
    current_parents, histories = o4_parents(parents)
    plan = {'status': 'ORGANIZATION_TRAINING_PLAN', 'policy': POLICY,
            'architecture': architecture, 'common_phi': selected['checkpoint'],
            'common_phi_sha256': hashlib.sha256(Path(selected['checkpoint']).read_bytes()).hexdigest(),
            'core_inner_steps': selected['core_inner_steps'], 'additional_inner_steps_each': 512,
            'total_O1_mix_clean_inner_steps': selected['core_inner_steps'] + 512,
            'O4_parents': [r['checkpoint'] for r in current_parents],
            'formal_data_used': False}
    preserve_json('records/organization_training_plan.json', plan)
    endpoints = {}
    for organization in POLICY['organizations']:
        config = copy.deepcopy(core)
        config.update(organization=organization, episode_K=[8] * 64, known_warm_episodes=0,
                      initial_phi=selected['checkpoint'], load_initial_meta_optimizer=True,
                      purpose='equal_512_update_organization_specialization')
        if organization == 'O4':
            config.update(parents=[r['checkpoint'] for r in current_parents],
                          parent_data=[r['config']['data'] for r in current_parents],
                          historical_bases=[r['master'] for r in histories],
                          historical_data=[r['historical_bindings'] for r in histories])
        endpoints[organization] = train(f'phi_mix_{organization}_{architecture}_specialize512_s17_observed_blocking_v8', config)
    clean = copy.deepcopy(core)
    clean.update(episode_K=core_schedule + [8] * 64, q_cycle=[0.],
                 purpose='O1_clean_matched_full_core_plus_specialization_budget')
    clean_result = train(f'phi_clean_O1_{architecture}_matched_s17_observed_blocking_v8', clean)
    assert clean_result['inner_updates'] == plan['total_O1_mix_clean_inner_steps']
    manifests = {}
    for key, result in list(endpoints.items()) + [('O1_clean', clean_result)]:
        checkpoint = result['checkpoint']
        frozen = dict(calibration, status='FROZEN', architecture=architecture,
                      organization='O1' if key == 'O1_clean' else key,
                      training_noise='clean' if key == 'O1_clean' else 'mixed',
                      phi=checkpoint, phi_sha256=hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
                      stopping_rule_frozen=True, formal_data_used=False,
                      core_inner_steps=selected['core_inner_steps'], specialization_inner_steps=512,
                      total_training_inner_steps=plan['total_O1_mix_clean_inner_steps'],
                      horizon=core['horizon'], organization_plan='records/organization_training_plan.json')
        file = f'records/phi_frozen_{key}.json'
        preserve_json(file, frozen)
        manifests[key] = file
    summary = {'status': 'ALL_PHI_FROZEN', 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'architecture': architecture, 'manifests': manifests, 'plan': plan,
               'formal_parent_training_permitted_after_this_record': True,
               'next': 'Final all/retain-only parents; formal main/selective/continual/7B comparisons and independent confirmations remain incomplete.'}
    preserve_json('records/all_phi_frozen.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
