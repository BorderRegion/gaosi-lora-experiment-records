"""CPU-only scheduler checks; simulated completion is never experiment evidence."""
import ast
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main():
    root = Path.cwd()
    snapshot = root / 'records/pipeline_recompute_v7'
    manifest = json.loads((snapshot / 'build_manifest.json').read_text())
    sys.path[:0] = [str(snapshot / 'tools'), str(root / 'tools'), str(root)]
    modules = {name: importlib.import_module(name + '_queue') for name in manifest['modules']}
    for name, module in modules.items():
        assert Path(module.__file__).resolve() == snapshot / 'tools' / (name + '_queue.py')
        strings = [node.value for node in ast.walk(ast.parse(Path(module.__file__).read_text()))
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for value in strings:
            assert not any(name + '_queue_v' in value for name in manifest['modules'])
            assert '_H2_core_s17/' not in value and '_H2_core_s17.json' not in value
    core = modules['phi_core']
    configpath = Path('configs') / (manifest['adopted_L_job'] + '.json')
    fixtures = {str(file): (root / file).read_bytes() for file in [
        configpath, Path('records/calibration_frozen.json'), Path('records/parents_ready.json')]}
    tests = []
    with tempfile.TemporaryDirectory(prefix='gaosi_scheduler_check_') as temporary:
        os.chdir(temporary)
        try:
            for file, data in fixtures.items():
                Path(file).parent.mkdir(parents=True, exist_ok=True)
                Path(file).write_bytes(data)
            name = manifest['adopted_L_job']
            run = Path('runs') / name
            run.mkdir(parents=True)
            job = Path('records/jobs') / (name + '.json')
            job.parent.mkdir(parents=True)
            ticks = Path(f'/proc/{os.getpid()}/stat').read_text().split()[21]
            live = {'status': 'RUNNING', 'child_pid': os.getpid(), 'child_proc_start_ticks': ticks}
            job.write_text(json.dumps(live))
            class WaitObserved(Exception):
                pass
            with patch.object(core.time, 'sleep', side_effect=WaitObserved):
                try:
                    core.wait_for_adopted_l()
                    raise AssertionError('Running adoption did not wait')
                except WaitObserved:
                    tests.append('running_exact_job_waits_without_launch')
            for state, label in [(dict(live, status='FAIL'), 'failed_job_rejected'),
                                 (dict(live, child_proc_start_ticks='0'), 'stale_pid_rejected')]:
                job.write_text(json.dumps(state))
                try:
                    core.wait_for_adopted_l()
                    raise AssertionError(label)
                except RuntimeError:
                    tests.append(label)
            configpath.write_text('{}')
            try:
                core.wait_for_adopted_l()
                raise AssertionError('changed_config_rejected')
            except RuntimeError:
                tests.append('changed_config_rejected')
            configpath.write_bytes(fixtures[str(configpath)])
            config = json.loads(configpath.read_text())
            (run / 'config.json').write_text(json.dumps(config))
            result = {'status': 'PASS', 'episodes': 192, 'inner_updates': 2560,
                      'checkpoint': str(run / 'latest.pt'), 'horizon': 'EXACT_H2_tail'}
            (run / 'result.json').write_text(json.dumps(result))
            job.write_text(json.dumps(dict(live, status='PASS')))
            import torch
            checkpoint = {'config': config, 'next_episode': 192, 'inner_updates': 2560,
                          'phi': {'weight': torch.tensor([1.])},
                          'meta_optimizer': {'state': {'moment': torch.tensor([0.])}}}
            for steps in [512, 2048, 2560]:
                (run / f'phi_{steps:04d}.pt').touch()
            torch.save(checkpoint, run / 'latest.pt')
            assert core.wait_for_adopted_l() == result
            tests.append('matching_finite_complete_result_adopted')
            checkpoint['phi']['weight'].fill_(float('inf'))
            torch.save(checkpoint, run / 'latest.pt')
            try:
                core.wait_for_adopted_l()
                raise AssertionError('nonfinite_checkpoint_rejected')
            except RuntimeError:
                tests.append('nonfinite_checkpoint_rejected')
            calls = []
            def train(name, config):
                calls.append(('train', name, config.copy()))
                return {'status': 'PASS', 'checkpoint': f'runs/{name}/latest.pt'}
            def profile(name, command, output):
                calls.append(('profile', name, command))
                return {'status': 'PASS'}
            with patch.object(core, 'wait_for_adopted_l', return_value=result), \
                 patch.object(core, 'train', side_effect=train), \
                 patch.object(core, 'run_job', side_effect=profile), \
                 patch.object(sys, 'argv', ['phi_core_queue']):
                core.main()
            train_names = [name for kind, name, _ in calls if kind == 'train']
            assert train_names == ['phi_X_real_episode_readiness_s17_recompute_v7',
                                   'phi_mix_O1_X_H2_core_s17_recompute_v7',
                                   'phi_mix_O1_L_H1_commonwarm_s17_recompute_v7']
            assert calls[-1][2]['initial_phi'] == f'runs/{name}/phi_0512.pt'
            generated = json.loads(Path('records/phi_core_ready.json').read_text())
            assert generated['core_endpoints']['L'] == result
            tests.extend(['calibrated_core_template_exactly_matches_adopted_L',
                          'no_duplicate_L_profile_readiness_or_core', 'H1_uses_v7_common_512'])
        finally:
            os.chdir(root)
    report = {'status': 'PASS', 'scope': 'CPU-only simulated scheduler behavior; no training/effect evidence',
              'controller_imports': len(modules), 'tests': tests}
    target = snapshot / 'validation.json'
    if target.exists():
        assert json.loads(target.read_text()) == report
    else:
        target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
