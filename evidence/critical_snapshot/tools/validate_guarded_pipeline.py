"""CPU-only audit, routing, adoption and snapshot checks; no experiment credit."""
import ast
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT)]
import torch
from gaosi.runtime import Run, atomic_save
from gaosi.gradients import stable_phi_clip_
from audit_observed_meta import audit
import guarded_pipeline as pipeline
from train_guarded_meta import guarded_train, digest, validate_shape


def main():
    torch.set_num_threads(2)
    report_path = ROOT / 'records/pipeline_observed_blocking_v8/validation.json'
    if report_path.exists():
        raise ValueError('Preserve previous validation')
    snapshot = ROOT / 'records/pipeline_observed_blocking_v8'
    build = json.loads((snapshot / 'build_manifest.json').read_text())
    sys.path.insert(0, str(snapshot / 'tools'))
    modules = {n: importlib.import_module(n + '_queue') for n in build['modules']}
    for name, module in modules.items():
        assert Path(module.__file__).resolve() == snapshot / 'tools' / (name + '_queue.py')
        text = Path(module.__file__).read_text()
        ast.parse(text)
        assert 'recompute_v7' not in text
        for value in [n.value for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]:
            assert not any(n + '_queue_v' in value for n in build['modules'])
            assert '_H2_core_s17/' not in value and '_H2_core_s17.json' not in value
    assert modules['phi_core'].run_job is pipeline.run_job
    tests = ['all_15_independent_controllers_import', 'all_core_paths_use_v8_and_all_meta_leaves_share_guarded_routing']
    fixtures = {name: (ROOT / name).read_bytes() for name in [
        'configs/' + pipeline.ADOPTED + '.json', 'records/calibration_frozen.json', 'records/parents_ready.json']}
    with tempfile.TemporaryDirectory(prefix='gaosi_guarded_pipeline_') as directory:
        root = Path(directory)
        os.chdir(root)
        try:
            for name, data in fixtures.items():
                p = Path(name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            output = root / 'runs/fixture'
            config = {'out': str(output), 'episode_K': [2, 2], 'architecture': 'L', 'organization': 'O1', 'horizon': 'H1',
                      'meta_lr': .0003, 'purpose': 'CPU fixture, no scientific evidence'}
            configfile = root / 'fixture_config.json'
            configfile.write_text(json.dumps(config))
            manifest = {'config': str(configfile), 'pinned_files_sha256': {str(configfile): digest(configfile)},
                        'raw_norm_stop_before_update': 1e6}
            source = ROOT / 'gaosi'
            for p in source.glob('*.py'):
                manifest['pinned_files_sha256'][str(p)] = digest(p)
            # Audit uses relative gaosi names to map the preserved source copy.
            for p in source.glob('*.py'):
                manifest['pinned_files_sha256']['gaosi/' + p.name] = digest(p)
            (root / 'gaosi').symlink_to(source, target_is_directory=True)
            wrapper = ROOT / 'tools/train_guarded_meta.py'
            manifest['pinned_files_sha256']['tools/train_guarded_meta.py'] = digest(wrapper)
            (root / 'tools').symlink_to(ROOT / 'tools', target_is_directory=True)
            torch.manual_seed(18)
            phi = torch.nn.Linear(2, 1)
            optimizer = torch.optim.Adam(phi.parameters(), lr=.0003, foreach=False)

            def rollout(*args, **kwargs):
                return None

            def train(current):
                run = Run(current['out'], current)
                for episode in range(2):
                    active.rollout(None, None, None, phi)
                    for p in phi.parameters():
                        p.grad = torch.full_like(p, 2. + episode)
                    norm = active.stable_phi_clip_(phi.parameters(), 1.)
                    optimizer.step()
                    for k in [1, 2]:
                        run.log('inner', episode=episode, k=k)
                    run.log('episode_end', episode=episode, inner_updates=2 * (episode + 1),
                            phi_grad_norm=float(norm))
                    atomic_save({'config': current, 'next_episode': episode + 1,
                                 'inner_updates': 2 * (episode + 1),
                                 'phi': phi.state_dict(), 'meta_optimizer': optimizer.state_dict()},
                                output / 'latest.pt')
                run.result(status='PASS', episodes=2, inner_updates=4, horizon='H1',
                           checkpoint=str(output / 'latest.pt'), base_unchanged=True)

            active = SimpleNamespace(train=train, rollout=rollout, stable_phi_clip_=stable_phi_clip_)
            guarded_train(active, torch, atomic_save, config, manifest, output)
            shutil.copyfile(wrapper, output / wrapper.name)
            (output / 'observed_training_manifest.json').write_text(json.dumps(manifest))
            (output / 'observed_training_result.json').write_text(json.dumps({
                'status': 'COMPLETE_REQUIRES_REVIEW', 'method_validated': False,
                'downstream_promotion_allowed': False, 'formal_data_used': False}))
            audited = audit(output)
            assert audited['episodes_committed'] == 2 and audited['executed_inner_updates'] == 4
            assert audited['downstream_promotion_allowed'] is False and audited['method_validated'] is False
            tests.append('complete_artifacts_audited_without_scientific_promotion')
            for file, label, mutation in [
                ('gradient_episode_000.pt', 'gradient_bytes_changed', lambda _: b'changed'),
                ('raw_gradients.jsonl', 'ledger_missing_episode', lambda b: b.splitlines(keepends=True)[0]),
                ('observed_training_result.json', 'unfinished_sidecar', lambda _: json.dumps({
                    'status': 'RUNNING', 'method_validated': False, 'downstream_promotion_allowed': False,
                    'formal_data_used': False}).encode()),
                ('source/meta.py', 'preserved_numerical_source_changed', lambda _: b'changed'),
            ]:
                p = output / file
                old = p.read_bytes()
                try:
                    p.write_bytes(mutation(old))
                    try:
                        audit(output)
                    except (ValueError, KeyError, RuntimeError):
                        tests.append(label + '_rejected')
                    else:
                        raise AssertionError('Audit accepted ' + label)
                finally:
                    p.write_bytes(old)
            auditfile = root / 'audit.json'
            auditfile.write_text(json.dumps(audited))
            science = root / 'fixture_scientific_evidence.txt'
            science.write_text('TEST FIXTURE ONLY; never a real scientific review')
            receipt = root / 'receipt.json'
            value = {'status': 'ALLOW_NEXT_PROTOCOL_STAGE_WITH_LIMITATIONS', 'run': str(output),
                     'reviewer': 'CPU test fixture', 'reviewed_at_utc': 'fixture', 'rationale': 'fixture',
                     'limitations': ['CPU fixture only'], 'artifact_audit': str(auditfile),
                     'artifact_audit_sha256': digest(auditfile),
                     'scientific_evidence_sha256': {str(science): digest(science)}}
            receipt.write_text(json.dumps(value))
            pipeline.verify_review(output, receipt)
            tests.append('review_binds_exact_artifact_audit_and_scientific_evidence')
            science.write_text('changed')
            try:
                pipeline.verify_review(output, receipt)
            except ValueError:
                tests.append('changed_scientific_evidence_rejected')
            else:
                raise AssertionError('Changed evidence accepted')
            # CPU simulation of leaf routing; no child command is executed.
            Path('records/logs').mkdir(parents=True, exist_ok=True)
            Path('records/jobs').mkdir(parents=True, exist_ok=True)
            for entry, module in [('meta', 'gaosi.meta'), ('scale', 'gaosi.scale'), ('confirmation', 'gaosi.confirmation')]:
                c = {'device': 'musa', 'out': 'runs/' + entry}
                if entry != 'meta':
                    c[entry + '_action'] = 'meta'
                path = Path('configs') / (entry + '.json')
                path.write_text(json.dumps(c))
                command = ['/usr/bin/python', '-u', '-m', module, '--config', str(path)]
                assert pipeline.meta_entry(command) == entry
                resultfile = Path(c['out']) / 'result.json'
                calls = []

                def child(cmd, **kwargs):
                    calls.append(cmd)
                    resultfile.parent.mkdir(parents=True)
                    resultfile.write_text(json.dumps({'status': 'PASS'}))
                    Path('records/jobs', entry + '.json').write_text(json.dumps({'status': 'PASS'}))
                    return 0

                with patch.object(pipeline, 'prepare') as prepare, \
                     patch.object(pipeline, 'wait_for_review') as review, \
                     patch.object(pipeline, 'preserve_space', return_value=[]), \
                     patch.object(pipeline.subprocess, 'call', side_effect=child):
                    pipeline.run_job(entry, command, resultfile)
                    assert prepare.call_args.args[1] == entry
                    review.assert_called_once_with(resultfile.parent)
                    actual = calls[0]
                    assert '--musa-lock' in actual and actual[actual.index('--musa-lock') + 1] == pipeline.LOCK
                    assert 'MUSA_LAUNCH_BLOCKING=1' in actual and 'tools/train_guarded_meta.py' in actual
                tests.append(entry + '_meta_leaf_uses_guard_original_lock_blocking_and_review')
            core = modules['phi_core']
            adopted_result = {'status': 'PASS', 'episodes': 192, 'inner_updates': 2560,
                              'horizon': 'EXACT_H2_tail', 'checkpoint': f'runs/{pipeline.ADOPTED}/latest.pt'}
            calls = []

            def fake_train(name, settings):
                calls.append((name, settings.copy()))
                return {'status': 'PASS', 'checkpoint': 'runs/' + name + '/latest.pt'}

            with patch.object(core, 'wait_for_adopted_l', return_value=adopted_result), \
                 patch.object(core, 'train', side_effect=fake_train), \
                 patch.object(core, 'run_job', return_value={'status': 'PASS'}), \
                 patch.object(sys, 'argv', ['phi_core_queue']):
                core.main()
            names = [name for name, _ in calls]
            assert names == ['phi_X_real_episode_readiness_s17_observed_blocking_v8',
                             'phi_mix_O1_X_H2_core_s17_observed_blocking_v8',
                             'phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8']
            assert calls[-1][1]['initial_phi'] == f'runs/{pipeline.ADOPTED}/phi_0512.pt'
            assert calls[-1][1]['load_initial_meta_optimizer'] is True and calls[-1][1]['start_episode'] == 64
            tests.append('no_duplicate_L_and_H1_uses_identical_v8_512_phi_and_optimizer')
            tests.append('adopted_L_matches_full_calibrated_template')
            job = Path('records/jobs', pipeline.ADOPTED + '.json')
            ticks = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
            live = {'status': 'RUNNING', 'child_pid': os.getpid(), 'child_proc_start_ticks': ticks}

            class Waiting(Exception):
                pass

            job.write_text(json.dumps(live))
            with patch.object(pipeline.time, 'sleep', side_effect=Waiting):
                try:
                    pipeline.adopt_l()
                except Waiting:
                    tests.append('running_L_waits_on_matching_process_without_launch')
                else:
                    raise AssertionError('Running job adopted before completion')
            for state in [dict(live, status='FAIL'), dict(live, child_proc_start_ticks='0')]:
                job.write_text(json.dumps(state))
                try:
                    pipeline.adopt_l()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Failed or stale L adopted')
            tests.append('failed_or_stale_L_cannot_be_adopted')
            adopted_run = Path('runs') / pipeline.ADOPTED
            adopted_run.mkdir(parents=True)
            (adopted_run / 'result.json').write_text(json.dumps(adopted_result))
            job.write_text(json.dumps(dict(live, status='PASS')))
            with patch.object(pipeline, 'wait_for_review', side_effect=Waiting):
                try:
                    pipeline.adopt_l()
                except Waiting:
                    tests.append('execution_PASS_alone_does_not_adopt_L')
                else:
                    raise AssertionError('PASS adopted without scientific review')
            for owner in ['scale', 'confirmation']:
                c = json.loads(fixtures['configs/' + pipeline.ADOPTED + '.json'])
                c[owner + '_action'] = 'meta'
                try:
                    validate_shape(c, 'meta')
                except ValueError:
                    tests.append(owner + '_entry_cannot_bypass_original_validation')
                else:
                    raise AssertionError('Wrong owner accepted')
        finally:
            os.chdir(ROOT)
    report = {'status': 'PASS', 'scope': 'CPU-only simulated scheduling, saved fixture audit and routing',
              'controller_imports': len(modules), 'tests': tests, 'method_validated': False,
              'training_started_or_restarted': False, 'production_source_modified': False}
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
