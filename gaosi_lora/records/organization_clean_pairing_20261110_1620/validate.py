"""Simulate primary queue completion with the actual meta result schema."""
import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'),
                str(ROOT / 'records/pipeline_observed_blocking_v8/tools'), str(HERE)]
import organization_queue_corrected_v2 as queue
import phi_core_queue as core_queue
from gaosi.scale import sha256


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


def setup(steps):
    core = json.loads((ROOT / 'records/clean_schedule_pairing_20261110_1407/prepared_clean_core_config.json').read_text())
    core['q_cycle'] = [0., .5, .7]
    core.pop('purpose', None)
    core.pop('out', None)
    parents = []
    for checkpoint, data in zip(core['parents'], core['parent_data']):
        parents.append({'checkpoint': checkpoint, 'config': {'data': data}, 'acquisition_gate_passed': True})
    selected = {'status': 'ARCHITECTURE_SELECTED_PENDING_ORGANIZATION_ADAPTATION',
                'architecture': core['architecture'], 'core_inner_steps': steps,
                'checkpoint': f'runs/selected_core/phi_{steps:04d}.pt'}
    path = Path(selected['checkpoint'])
    path.parent.mkdir(parents=True)
    path.write_text('ISOLATED_NOT_A_TRAINED_CHECKPOINT')
    histories = [{'master': f'runs/meta_history_{i}/master.pt', 'historical_bindings': p['config']['data']}
                 for i, p in enumerate(parents)]
    current = [{'checkpoint': f'runs/meta_current_{i}/latest.pt',
                'config': {'data': parents[1 - i]['config']['data']}} for i in range(2)]
    write(f"configs/phi_mix_O1_{core['architecture']}_H2_core_s17_observed_blocking_v8.json", core)
    write('records/parents_ready.json', parents)
    write('records/phi_architecture_selected.json', selected)
    write('records/calibration_frozen.json', {'status': 'ISOLATED_FIXTURE_ONLY', 'formal_data_used': False})
    write('checks/organization_parents_cpu.json', {'status': 'PASS', 'scope': 'ISOLATED_FIXTURE_ONLY'})
    # Real handoff sees declarations already written by the old controller.
    write('records/organization_training_policy.json', queue.POLICY)
    return selected, core, current, histories


class AtBoundary(Exception):
    pass


def invoke(selected, current, histories, job):
    with patch.object(core_queue, 'run_job', job), patch.object(queue, 'o4_parents', return_value=(current, histories)), \
         patch.object(queue, 'wait_for', return_value=selected), patch.object(sys, 'argv', ['queue_fixture']):
        queue.main()


def run_case(steps, corrupt=None):
    selected, core, current, histories = setup(steps)
    requests, created, reused = [], [], []
    partial = True

    def fake_job(name, command, resultfile):
        nonlocal partial
        assert command[:4] == ['/usr/bin/python', '-u', '-m', 'gaosi.meta']
        config = json.loads(Path(command[-1]).read_text())
        resultfile = Path(resultfile)
        requests.append(name)
        if partial and config['organization'] == 'O3':
            raise AtBoundary('ISOLATED_SIMULATED_O2_COMPLETION_REVIEW_BOUNDARY')
        if resultfile.exists():
            reused.append(name)
            return json.loads(resultfile.read_text())
        out = Path(config['out'])
        assert not out.exists()
        out.mkdir(parents=True)
        saved = copy.deepcopy(config)
        if corrupt == 'config' and 'matched_' in name:
            saved['seed'] += 1
        write(out / 'config.json', saved)
        (out / 'latest.pt').write_text('SIMULATED_NO_TRAINING')
        result = {'status': 'PASS', 'episodes': len(config['episode_K']),
                  'inner_updates': sum(config['episode_K']), 'base_unchanged': config['organization'] != 'O4',
                  'checkpoint': str(out / ('different.pt' if corrupt == 'checkpoint' and 'matched_' in name else 'latest.pt')),
                  'horizon': config['horizon'], 'compute_counters': {},
                  'note': 'ISOLATED_SIMULATED_RESULT_NO_MODEL_OR_TRAINING'}
        assert 'config' not in result  # Match production gaosi.meta/Run.result.
        write(resultfile, result)
        created.append((name, config))
        return result

    try:
        invoke(selected, current, histories, fake_job)
    except AtBoundary:
        pass
    else:
        raise AssertionError('Missing simulated boundary')
    assert len(created) == 2 and not Path('records/all_phi_frozen.json').exists()
    preserved = {str(p): sha256(p) for _, config in created for p in Path(config['out']).iterdir()}
    partial = False
    if corrupt:
        try:
            invoke(selected, current, histories, fake_job)
        except ValueError as error:
            assert 'Actual clean result/config/checkpoint differs' in str(error), str(error)
        else:
            raise AssertionError('Mismatched actual clean evidence was accepted')
        assert not Path('records/all_phi_frozen.json').exists()
        return {'core_steps': steps, 'corruption_rejected': corrupt,
                'scope': 'ISOLATED_SIMULATION_NO_TRAINING'}
    invoke(selected, current, histories, fake_job)
    assert all(sha256(p) == expected for p, expected in preserved.items())
    assert len(created) == 6 and len(set(name for name, _ in created)) == 6 and len(reused) == 2
    configs = [c for _, c in created]
    clean_core, clean_spec = configs[-2:]
    assert sum(clean_core['episode_K']) == steps
    assert sum(clean_spec['episode_K']) == 512
    assert clean_core['q_cycle'] == clean_spec['q_cycle'] == [0.]
    assert not clean_core.get('initial_phi') and not clean_core.get('load_initial_meta_optimizer')
    assert clean_spec['initial_phi'] == clean_core['out'] + '/latest.pt'
    assert clean_spec['load_initial_meta_optimizer'] and clean_spec['known_warm_episodes'] == 0
    assert not clean_spec.get('start_episode')
    mixed_spec = configs[0]
    exclusions = {'out', 'q_cycle', 'initial_phi', 'purpose'}
    assert {k: v for k, v in mixed_spec.items() if k not in exclusions} == {
        k: v for k, v in clean_spec.items() if k not in exclusions}
    pairing = json.loads(Path('records/clean_training_stage_pairing_v1.json').read_text())
    assert pairing['core'] == clean_core['out'] and pairing['specialization'] == clean_spec['out']
    assert pairing['total_training_inner_steps'] == steps + 512
    frozen = json.loads(Path('records/all_phi_frozen.json').read_text())
    assert set(frozen['manifests']) == {'O1', 'O2', 'O3', 'O4', 'O1_clean'}
    clean_freeze = json.loads(Path(frozen['manifests']['O1_clean']).read_text())
    assert clean_freeze['phi'] == clean_spec['out'] + '/latest.pt'
    assert clean_freeze['total_training_inner_steps'] == steps + 512
    return {'core_steps': steps, 'simulated_new_stages': len(created),
            'completed_stage_results_reused': len(reused), 'preserved_completed_files': len(preserved),
            'all_five_phi_freezes_from_simulated_outputs': True,
            'production_meta_result_schema_without_config': True,
            'model_or_training_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    output = (ROOT / args.out).resolve()
    assert output.is_relative_to(HERE) and not output.exists()
    cwd, cases = Path.cwd(), []
    with tempfile.TemporaryDirectory(prefix='gaosi_primary_clean_pairing_') as temporary:
        for steps, corrupt in [(2560, None), (3584, None), (2560, 'config'), (2560, 'checkpoint')]:
            folder = Path(temporary) / f'{steps}_{corrupt}'
            folder.mkdir()
            os.chdir(folder)
            try:
                cases.append(run_case(steps, corrupt))
            finally:
                os.chdir(cwd)
    original = ROOT / 'records/clean_schedule_pairing_20261110_1407/organization_queue_corrected.py'
    result = {'status': 'PASS', 'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope': 'isolated primary queue fixtures using production meta result schema and actual config sidecars',
              'cases': cases, 'source_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in HERE.glob('*.py')},
              'previous_candidate_preserved_sha256': sha256(original),
              'production_sources_sha256': {p: sha256(ROOT / p) for p in
                  ['gaosi/meta.py', 'gaosi/runtime.py', 'tools/train_guarded_meta.py',
                   'tools/guarded_pipeline.py', 'records/pipeline_observed_blocking_v8/tools/organization_queue.py']},
              'training_or_controller_modified': False,
              'numerical_derivatives_or_efficacy_validated': False, 'full_goal_complete': False}
    with output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
