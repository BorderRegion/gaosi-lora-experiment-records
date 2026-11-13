"""Full O1 L/X schedules gated by valid parents, calibrated q and real H2."""
import copy
import argparse
import json
from pathlib import Path
import subprocess
import time

from storage import preserve_space


from guarded_pipeline import run_job


def train(name, config):
    config = copy.deepcopy(config)
    config['out'] = 'runs/' + name
    file = Path('configs') / (name + '.json')
    if file.exists() and json.loads(file.read_text()) != config:
        raise ValueError(f'Existing config differs for {name}')
    file.write_text(json.dumps(config, indent=2))
    result = run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.meta', '--config', str(file)],
                     Path(config['out']) / 'result.json')
    return result



from guarded_pipeline import adopt_l as wait_for_adopted_l


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--calibration-job', default='calibration_queue_v3')
    args = p.parse_args()
    adopted_l = wait_for_adopted_l()
    while not Path('records/calibration_frozen.json').exists():
        job = Path('records/jobs') / (args.calibration_job + '.json')
        if job.exists() and json.loads(job.read_text())['status'] != 'RUNNING':
            raise RuntimeError('Calibration ended without frozen measurable doses')
        time.sleep(30)
    calibration = json.loads(Path('records/calibration_frozen.json').read_text())
    assert calibration['ordinary_recovery_validated'] and not calibration['formal_data_used']
    parents = json.loads(Path('records/parents_ready.json').read_text())
    meta = [p for p in parents if 'meta_parent_' in p['config']['data']]
    assert len(meta) == 2 and all(p['acquisition_gate_passed'] for p in meta)
    dev = next(p for p in parents if p['config']['data'].endswith('dev_parent.jsonl'))
    for architecture in ['X']:
        name = f'profile_3b_{architecture}_H2_three_routes_learned_parent_observed_blocking_v8'
        run_job(name, ['/usr/bin/python', '-u', 'tools/profile_transition.py',
                       '--checkpoint', dev['checkpoint'], '--out', f'checks/{name}.json',
                       '--seq', '512', '--effective-tokens', '512', '--architecture', architecture,
                       '--horizon', 'EXACT_H2_tail', '--retain-facts'], f'checks/{name}.json')
    parent_config = meta[0]['config']
    template = {'out': '', 'model': parent_config['model'], 'base_revision': parent_config['base_revision'],
                'rank': calibration['rank'], 'targets': calibration['targets'], 'scale': calibration['scale'],
                'organization': 'O1', 'device': 'musa', 'architecture': 'L', 'seed': 17,
                'parents': [p['checkpoint'] for p in meta],
                'parent_data': [p['config']['data'] for p in meta],
                'episode_K': [8] * 64 + [16] * 128, 'known_warm_episodes': 64,
                'q_cycle': [0., calibration['q_light'], calibration['q_medium']],
                'meta_lr': .0003, 'inner_lr': calibration['recovery_lr'],
                'seq': 512, 'effective_tokens': 512, 'retain_weight': .25,
                'retain_fact_support': True, 'penalty': 1., 'chunk': 262144,
                'horizon': 'EXACT_H2_tail', 'world_manifest': calibration['world_manifest'],
                'selection_data': 'development_only', 'calibration_manifest': 'records/calibration_frozen.json'}
    endpoints = {}
    for architecture in ['L', 'X']:
        # Two real episodes first exercise optimizer updates, nonzero heads,
        # reset and resume; their budgets are explicit extra readiness cost.
        smoke = copy.deepcopy(template)
        smoke.update(architecture=architecture, episode_K=[2, 2], known_warm_episodes=2,
                     purpose='real_end_to_end_optimizer_readiness_not_core_budget')
        if architecture != 'L':
            train(f'phi_{architecture}_real_episode_readiness_s17_observed_blocking_v8', smoke)
        core = copy.deepcopy(template)
        core.update(architecture=architecture, purpose='full_protocol_O1_phi_mix_2560_fresh_phi_candidate_blocking_mitigation_review_required')
        if architecture == 'L':
            expected = dict(core, out='runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8')
            actual = json.loads(Path('configs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8.json').read_text())
            if actual != expected:
                raise RuntimeError('Adopted L differs from the calibrated core template')
            endpoints[architecture] = adopted_l
        else:
            endpoints[architecture] = train(f'phi_mix_O1_{architecture}_H2_core_s17_observed_blocking_v8', core)
    # Exactly common phi and meta-optimizer state at 512 inner updates. All
    # subsequent episode indexes, data seeds and q cycling match the H2 core.
    alternate = copy.deepcopy(template)
    alternate.update(architecture='L', horizon='H1', start_episode=64,
                     initial_phi='runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8/phi_0512.pt',
                     load_initial_meta_optimizer=True, purpose='common_512_warm_alternate_H1')
    alternate_result = train('phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8', alternate)
    manifest = {'status': 'CORE_TRAINING_COMPLETE_PENDING_DEV_SELECTION', 'core_endpoints': endpoints,
                'alternate_horizon': alternate_result, 'main_inner_steps_each': 2560,
                'common_warm_inner_steps': 512, 'alternate_additional_steps': 2048,
                'formal_phi_frozen': False,
                'next': 'Full dev recovery/new-world evaluation, architecture freeze, four organization specialization and matched phi_clean before final parent knowledge.'}
    Path('records/phi_core_ready.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
