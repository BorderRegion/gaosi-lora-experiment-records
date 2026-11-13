"""Full O1 L/X schedules gated by valid parents, calibrated q and real H2."""
import copy
import argparse
import json
from pathlib import Path
import subprocess
import time

from storage import preserve_space


def run_job(name, command, resultfile):
    resultfile = Path(resultfile)
    jobfile = Path('records/jobs') / (name + '.json')
    if resultfile.exists():
        result = json.loads(resultfile.read_text())
        if result['status'] != 'PASS':
            raise RuntimeError(f'Preserved unsuccessful prerequisite {name}')
        return result
    if jobfile.exists():
        raise RuntimeError(f'Incomplete prior job {name}; inspect before resume')
    for event in preserve_space():
        print(json.dumps({'storage': event}), flush=True)
    with Path(f'records/logs/{name}.log').open('ab', buffering=0) as log:
        code = subprocess.call(['/usr/bin/python', '-u', 'tools/job.py', '--name', name,
                                '--', *command], stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL)
    if code:
        raise RuntimeError(f'{name} failed {code}; preserve trace and inspect')
    result = json.loads(resultfile.read_text())
    if result['status'] != 'PASS':
        raise RuntimeError(f'{name} returned unsuccessful result')
    return result


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



def wait_for_adopted_l():
    """Adopt one exact running job; never launch or resume it here."""
    import hashlib
    name = 'phi_mix_O1_L_H2_core_s17_recompute_v7'
    configfile = Path('configs') / (name + '.json')
    expected_sha = 'f70511613b15835c3eae3ea0fa86e937bb51bf3559301c600a4f9206fca414bc'
    resultfile = Path('runs') / name / 'result.json'
    while True:
        if hashlib.sha256(configfile.read_bytes()).hexdigest() != expected_sha:
            raise RuntimeError('Adopted L configuration changed')
        state = json.loads((Path('records/jobs') / (name + '.json')).read_text())
        if state['status'] == 'PASS':
            result = json.loads(resultfile.read_text())
            config = json.loads(configfile.read_text())
            if (result['status'] != 'PASS' or result['episodes'] != 192
                    or result['inner_updates'] != 2560
                    or result['horizon'] != 'EXACT_H2_tail'
                    or result['checkpoint'] != f'runs/{name}/latest.pt'
                    or json.loads((Path('runs') / name / 'config.json').read_text()) != config):
                raise RuntimeError('Adopted L result does not match the full v7 schedule')
            import torch
            checkpoint = torch.load(result['checkpoint'], map_location='cpu', weights_only=False)
            def finite(value):
                if torch.is_tensor(value):
                    return bool(torch.isfinite(value).all())
                if isinstance(value, dict):
                    return all(finite(v) for v in value.values())
                if isinstance(value, (tuple, list)):
                    return all(finite(v) for v in value)
                return True
            if (checkpoint['config'] != config or checkpoint['next_episode'] != 192
                    or checkpoint['inner_updates'] != 2560
                    or not finite(checkpoint['phi']) or not finite(checkpoint['meta_optimizer'])):
                raise RuntimeError('Adopted L final checkpoint is incomplete or nonfinite')
            for steps in [512, 2048, 2560]:
                if not (Path('runs') / name / f'phi_{steps:04d}.pt').is_file():
                    raise RuntimeError('Adopted L is missing a required learning-curve snapshot')
            print(json.dumps({'event': 'adopted_existing_L_complete', 'job': name,
                              'credited_inner_updates': 2560}), flush=True)
            return result
        if state['status'] != 'RUNNING':
            raise RuntimeError('Adopted L failed; preserve all evidence and inspect')
        proc = Path(f"/proc/{state.get('child_pid')}/stat")
        fields = proc.read_text().split() if proc.exists() else []
        if not fields or fields[2] == 'Z' or fields[21] != state.get('child_proc_start_ticks'):
            raise RuntimeError('Adopted L has no matching live process')
        time.sleep(30)


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
        name = f'profile_3b_{architecture}_H2_three_routes_learned_parent_recompute_v7'
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
            train(f'phi_{architecture}_real_episode_readiness_s17_recompute_v7', smoke)
        core = copy.deepcopy(template)
        core.update(architecture=architecture, purpose='full_protocol_O1_phi_mix_2560_fresh_phi_after_recompute_v7')
        if architecture == 'L':
            expected = dict(core, out='runs/phi_mix_O1_L_H2_core_s17_recompute_v7')
            actual = json.loads(Path('configs/phi_mix_O1_L_H2_core_s17_recompute_v7.json').read_text())
            if actual != expected:
                raise RuntimeError('Adopted L differs from the calibrated core template')
            endpoints[architecture] = adopted_l
        else:
            endpoints[architecture] = train(f'phi_mix_O1_{architecture}_H2_core_s17_recompute_v7', core)
    # Exactly common phi and meta-optimizer state at 512 inner updates. All
    # subsequent episode indexes, data seeds and q cycling match the H2 core.
    alternate = copy.deepcopy(template)
    alternate.update(architecture='L', horizon='H1', start_episode=64,
                     initial_phi='runs/phi_mix_O1_L_H2_core_s17_recompute_v7/phi_0512.pt',
                     load_initial_meta_optimizer=True, purpose='common_512_warm_alternate_H1')
    alternate_result = train('phi_mix_O1_L_H1_commonwarm_s17_recompute_v7', alternate)
    manifest = {'status': 'CORE_TRAINING_COMPLETE_PENDING_DEV_SELECTION', 'core_endpoints': endpoints,
                'alternate_horizon': alternate_result, 'main_inner_steps_each': 2560,
                'common_warm_inner_steps': 512, 'alternate_additional_steps': 2048,
                'formal_phi_frozen': False,
                'next': 'Full dev recovery/new-world evaluation, architecture freeze, four organization specialization and matched phi_clean before final parent knowledge.'}
    Path('records/phi_core_ready.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
