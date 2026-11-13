"""Isolated routing/entry/guard fixtures. No model or training is executed."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REL = str(HERE.relative_to(ROOT))
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / 'tools')]
import confirmation_queue_corrected as queue
import confirmation_runner_corrected as runner
import guarded_pipeline_corrected as pipeline
import train_guarded_confirmation_paired as adapter

try:
    import torch
    CHECKPOINT_IO = 'real_torch_save_load_CPU_only_no_tensor_training'

    def checkpoint_save(value, path):
        torch.save(value, path)
except ImportError:
    CHECKPOINT_IO = 'JSON_checkpoint_standin_no_torch_or_tensor_execution'
    torch = types.ModuleType('torch')
    torch.load = lambda path, **kwargs: json.loads(Path(path).read_text())
    sys.modules['torch'] = torch

    def checkpoint_save(value, path):
        Path(path).write_text(json.dumps(value))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
    return str(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reject(call, fragment):
    try:
        call()
    except (ValueError, RuntimeError) as error:
        assert fragment in str(error), (fragment, str(error))
    else:
        raise AssertionError('Invalid confirmation accepted: ' + fragment)


def setup(steps):
    config = json.loads((ROOT / 'records/clean_schedule_pairing_20261110_1407/prepared_clean_core_config.json').read_text())
    parents, data = [], []
    for index in range(2):
        file = write(f'data/synthetic/meta_parent_{index}.jsonl', {})
        Path(file).write_text(''.join(json.dumps({'world_id': f'meta_parent_{index}', 'role': role}) + '\n'
                                    for role in ['support', 'query']))
        parent = write(f'runs/parent_{index}/latest.pt', {'fixture_only': True})
        write(Path(parent).parent / 'config.json', {'fixture_only': True})
        write(Path(parent).parent / 'result.json', {'status': 'PASS', 'fixture_only': True})
        parents.append(parent)
        data.append(file)
    worlds = []
    for index in range(64):
        name = f'meta_world_{index:02d}'
        file = Path('data/synthetic') / (name + '.jsonl')
        file.write_text(''.join(json.dumps({'world_id': name, 'role': role}) + '\n'
                                for role in ['support', 'query']))
        worlds.append({'world_id': name, 'split': 'meta', 'file': file.name})
    manifest = write('data/synthetic/manifest.json', {'worlds': worlds})
    for part in ['inner', 'meta_query']:
        write(f'data/language/{part}.jsonl', {'text': 'CPU fixture only'})
    selected = {'architecture': config['architecture'], 'core_inner_steps': steps}
    config.update(parents=parents, parent_data=data, world_manifest=manifest,
                  q_cycle=[0., .5, .7], calibration_manifest='records/calibration_frozen.json')
    reference = f"configs/phi_mix_O1_{config['architecture']}_H2_core_s17_observed_blocking_v8.json"
    write(reference, config)
    write('records/phi_architecture_selected.json', selected)
    write('records/calibration_frozen.json', {'fixture_only': True})
    write('records/confirmation_policy_v1.json', queue.POLICY)
    currents, histories = [], []
    for index in range(2):
        master = write(f'runs/history_{index}/master.pt', {'fixture_only': True})
        write(master + '.json', {'fixture_only': True})
        current = write(f'runs/o4_current_{index}/latest.pt', {'fixture_only': True})
        currents.append({'checkpoint': current, 'config': {'data': data[1 - index]}})
        histories.append({'master': master, 'historical_bindings': data[index]})
    write('records/o4_parents_ready.json', {'status': 'PASS', 'formal_data_used': False,
                                          'parents': currents, 'histories': histories})
    original = {'world_manifest': manifest, 'organization_plan': 'SIMULATED_PRIMARY_PLAN'}
    return selected, original


def check_mutation(base, changes, fragment, checkpoint_change=None):
    config = copy.deepcopy(base)
    config.update(changes)
    entry_path = Path(config['confirmation_entry_manifest'])
    original = entry_path.read_bytes()
    entry = json.loads(original)
    checkpoint = None
    if checkpoint_change:
        checkpoint = Path(config['initial_phi'])
        saved_bytes = checkpoint.read_bytes()
        value = torch.load(checkpoint, map_location='cpu', weights_only=False)
        checkpoint_change(value)
        checkpoint_save(value, checkpoint)
        entry['artifact_sha256'][str(checkpoint)] = digest(checkpoint)
    entry['config'] = config
    entry_path.write_text(json.dumps(entry))
    try:
        reject(lambda: runner.check_config(config), fragment)
    finally:
        entry_path.write_bytes(original)
        if checkpoint:
            checkpoint.write_bytes(saved_bytes)


def route(config):
    worlds = json.loads(Path(config['world_manifest']).read_text())['worlds']
    worlds = [w for w in worlds if w['split'] == 'meta']
    result = []
    for episode, K in enumerate(config['episode_K']):
        p = episode % len(config['parents'])
        known = episode < config['known_warm_episodes'] or episode % 2 == 0
        task = config['parent_data'][p] if known else 'data/synthetic/' + worlds[(episode // 2) % len(worlds)]['file']
        result.append((episode, K, config['parents'][p], task, config['parent_data'][p],
                       config['seed'] * 100000 + episode))
    return result


def simulation(steps, second):
    selected, original = setup(steps)
    calls = []

    def simulated_job(name, command, resultfile):
        config_path = command[command.index('--config') + 1]
        config = json.loads(Path(config_path).read_text())
        assert pipeline.meta_entry(command) == 'confirmation'
        runner.check_config(config)
        manifest_path = f'records/meta_fixture/{name}.json'
        adapter.original.prepare(config_path, 'confirmation', manifest_path)
        assert adapter.original.run(manifest_path, validate_only=True)['status'] == 'VALIDATED_NOT_EXECUTED'
        pinned = json.loads(Path(manifest_path).read_text())['pinned_files_sha256']
        assert all(REL + '/' + p in pinned for p in ['confirmation_runner_corrected.py',
                   'confirmation_queue_corrected.py', 'guarded_pipeline_corrected.py',
                   'train_guarded_confirmation_paired.py'])
        calls.append(config)
        out = Path(config['out'])
        out.mkdir(parents=True)
        write(out / 'config.json', config)
        checkpoint = str(out / 'latest.pt')
        updates = sum(config['episode_K'])
        previous = 0
        if config.get('initial_phi'):
            source = torch.load(config['initial_phi'], map_location='cpu', weights_only=False)
            previous = float(source['meta_optimizer']['state']['fixture_parameter']['step'])
        checkpoint_save({'config': config, 'phi': {'fixture_only': True}, 'inner_updates': updates,
                         'next_episode': len(config['episode_K']), 'meta_optimizer': {
                             'state': {'fixture_parameter': {'step': previous + len(config['episode_K'])}},
                             'param_groups': [{'fixture_only': True}]}}, checkpoint)
        result = {'status': 'PASS', 'checkpoint': checkpoint, 'inner_updates': updates,
                  'scope': 'SIMULATED_ROUTING_ONLY_NOT_TRAINING'}
        write(resultfile, result)
        return result

    with patch.object(queue, 'run_job', simulated_job):
        for seed in [29, 43]:
            queue.train_seed(seed, selected, original, second)
    assert len(calls) == 10
    for seed in [29, 43]:
        own = [c for c in calls if c['confirmation_seed'] == seed]
        by_stage = {(c['confirmation_stage'], c['organization']): c for c in own}
        mix = by_stage['mixed_core_from_zero', 'O1']
        clean = by_stage['clean_core_from_zero', 'O1']
        mix_spec = by_stage['organization_specialization', 'O1']
        clean_spec = by_stage['clean_organization_specialization', 'O1']
        assert route(mix) == route(clean)
        assert route(mix_spec) == route(clean_spec)
        excluded = {'out', 'q_cycle', 'purpose', 'confirmation_stage', 'confirmation_entry_manifest', 'initial_phi'}
        for left, right in [(mix, clean), (mix_spec, clean_spec)]:
            assert {k: v for k, v in left.items() if k not in excluded} == {
                k: v for k, v in right.items() if k not in excluded}
        assert sum(clean['episode_K']) + sum(clean_spec['episode_K']) == steps + 512
        assert not clean.get('initial_phi') and not clean.get('load_initial_meta_optimizer')
        assert clean_spec['initial_phi'] == clean['out'] + '/latest.pt'
        assert clean_spec['load_initial_meta_optimizer'] and not clean_spec.get('start_episode')
        final = torch.load(clean_spec['out'] + '/latest.pt', map_location='cpu', weights_only=False)
        assert final['meta_optimizer']['state']['fixture_parameter']['step'] == len(clean['episode_K']) + 64
        check_mutation(clean, {'confirmation_stage': 'clean_full_from_zero',
                       'episode_K': clean['episode_K'] + [8] * 64}, 'concatenated clean control')
        check_mutation(clean, {'initial_phi': mix['out'] + '/latest.pt'}, 'fresh meta optimizer')
        check_mutation(clean, {'q_cycle': [.5]}, 'noise schedule changed')
        check_mutation(clean_spec, {'load_initial_meta_optimizer': False}, 'own completed mixed/clean core')
        check_mutation(clean_spec, {'start_episode': len(clean['episode_K'])}, 'restart episode zero')
        check_mutation(clean_spec, {'known_warm_episodes': 64}, 'schedule changed')
        check_mutation(clean_spec, {'initial_phi': mix['out'] + '/latest.pt'}, 'own completed mixed/clean core')
        check_mutation(clean_spec, {}, 'complete core Adam state',
                       lambda p: p['meta_optimizer']['state']['fixture_parameter'].update(step=1))
        check_mutation(clean_spec, {}, 'complete core Adam state',
                       lambda p: p['meta_optimizer'].update(state={}))
        check_mutation(clean_spec, {}, 'own completed mixed/clean core',
                       lambda p: p['config'].update(confirmation_seed=17))
        check_mutation(clean_spec, {}, 'own completed mixed/clean core',
                       lambda p: p.update(next_episode=1))
    return {'steps': steps, 'second_organization': second, 'seeds': [29, 43],
            'simulated_meta_entries': len(calls), 'clean_specialization_streams_paired': 128,
            'invalid_mutations_rejected': 22, 'model_or_training_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    output = (ROOT / args.out).resolve()
    assert output.is_relative_to(HERE) and not output.exists()
    runs = []
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='gaosi_confirmation_pairing_') as temporary:
        for steps, second in [(2560, 'O2'), (3584, 'O4')]:
            fixture = Path(temporary) / str(steps)
            fixture.mkdir()
            os.chdir(fixture)
            sources = set(adapter.pinned_source_files())
            for name in sources:
                source, destination = ROOT / name, fixture / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            try:
                with patch.object(adapter.original, 'ROOT', fixture):
                    runs.append(simulation(steps, second))
            finally:
                os.chdir(original_cwd)
    result = {'status': 'PASS', 'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope': 'isolated configuration, sample-routing and evidence-guard regression fixtures',
              'checkpoint_io': CHECKPOINT_IO, 'runs': runs,
              'source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in HERE.glob('*.py')},
              'production_source_modified': False, 'training_submitted': False,
              'numerical_derivatives_or_efficacy_validated': False, 'full_goal_complete': False}
    with output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
