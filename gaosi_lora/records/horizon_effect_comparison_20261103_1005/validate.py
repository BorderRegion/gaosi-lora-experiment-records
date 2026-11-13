"""Small adversarial fixtures for pairing/budget gates; no training or scores."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('horizon_comparison', Path(__file__).with_name('compare.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def rejected(function):
    try:
        function()
    except (ValueError, FileNotFoundError):
        return True
    raise AssertionError('Invalid fixture accepted')


def main():
    checks = []
    c2 = {'architecture': 'L', 'horizon': 'EXACT_H2_tail', 'episode_K': [8] * 64 + [16] * 128,
          'out': m.H2, 'purpose': 'full', 'seed': 17}
    c1 = dict(c2, out=m.H1, horizon='H1', start_episode=64, initial_phi=m.H2 + '/phi_0512.pt',
              load_initial_meta_optimizer=True, purpose='common_512_warm_alternate_H1')
    r2 = {'status': 'PASS', 'episodes': 192, 'inner_updates': 2560, 'horizon': 'EXACT_H2_tail'}
    r1 = dict(r2, horizon='H1')
    m.validate_endpoint_metadata(c2, c1, r2, r1)
    checks.append('accept_exact_common_warm_endpoint_metadata')
    for label, bad in [('reject_mismatched_3584_budget', dict(r2, inner_updates=3584)),
                       ('reject_unfinished_endpoint', dict(r2, episodes=191))]:
        assert rejected(lambda: m.validate_endpoint_metadata(c2, c1, bad, r1))
        checks.append(label)
    for field, value in [('seed', 29), ('load_initial_meta_optimizer', False), ('start_episode', 0)]:
        assert rejected(lambda: m.validate_endpoint_metadata(c2, dict(c1, **{field: value}), r2, r1))
        checks.append('reject_changed_' + field)
    original = Path.cwd()
    old_here, old_root, old_reviews = m.HERE, m.ROOT, m.REVIEWS
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.chdir(folder)
            m.ROOT = Path(folder)
            m.HERE = Path(folder) / 'records/comparison'
            m.HERE.mkdir(parents=True)
            m.REVIEWS = Path('records/reviews')
            lock = Path(folder) / 'gpu.lock'
            lock.touch()
            inputs = {'policy': {'fixture': True}, 'parent': 'runs/dev_parent/latest.pt',
                      'retain_data': 'data/dev_parent.jsonl', 'recovery_lr': .0003, 'q_medium': .7,
                      'tasks': [{'group': 'known', 'data': 'data/dev_parent.jsonl'}],
                      'source_sha256': {'gaosi/recover.py': 'unused'}, 'inputs_sha256': {},
                      'device_lock': {'path': str(lock), 'inode': lock.stat().st_ino}}
            write(m.HERE / 'policy.json', inputs['policy'])
            declared = m.pairs(inputs)
            write(m.HERE / 'declared_pairs.json', declared)
            waiting = m.run(inputs, check_only=True)
            assert waiting['status'].startswith('WAITING')
            assert not Path('configs').exists() and not Path('records/jobs').exists()
            checks.append('missing_checkpoint_or_review_never_creates_job')
            require_lock = copy.deepcopy(inputs)
            require_lock['device_lock']['inode'] += 1
            assert rejected(lambda: m.verify_inputs(require_lock))
            checks.append('reject_replaced_gpu_lock')
            task_config = declared[0]['H2']
            out = Path(task_config['out'])
            source_text = '# fixture recovery source\n'
            source = out / 'source/recover.py'
            source.parent.mkdir(parents=True)
            source.write_text(source_text)
            inputs['source_sha256']['gaosi/recover.py'] = m.sha(source)
            write(out / 'source_sha256.json', {'recover.py': m.sha(source)})
            write(task_config['phi'], {'fixture': 'phi'})
            write(task_config['freeze_manifest'], {'status': 'PHI_DEVELOPMENT_PLAN',
                  'allowed_phis': {task_config['phi']: m.sha(task_config['phi'])}})
            metric = {'task': {'candidate_accuracy': .5, 'strict_EM': .25},
                      'language': {'nll': 2.}, 'keep': {'nll': 1.}}
            result = {'status': 'PASS', 'config': task_config, 'base_unchanged': True, 'phi_frozen': True,
                      'coverage': 1, 'pool': 1, 'keep_coverage': {'covered': 1, 'pool': 1}, 'final': metric}
            write(out / 'config.json', task_config)
            write(out / 'result.json', result)
            write('records/jobs/' + out.name + '.json', {'status': 'PASS'})
            events = [{'event': 'recover', 'step': step} for step in range(1, 65)]
            events += [{'event': 'evaluation', 'step': step, 'metrics': metric} for step in [0, 8, 16, 32, 64]]
            (out / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
            m.validate_result(task_config, inputs)
            checks.append('accept_complete_recovery_ledger_fixture')
            for label, config in [('reject_changed_recovery_K', dict(task_config, K=32)),
                                  ('reject_changed_data_seed', dict(task_config, data_seed=29))]:
                assert rejected(lambda: m.validate_result(config, inputs))
                checks.append(label)
            write(out / 'result.json', dict(result, coverage=0))
            assert rejected(lambda: m.validate_result(task_config, inputs))
            checks.append('reject_incomplete_data_coverage')
            write(out / 'result.json', result)
            source.write_text(source_text + '# changed\n')
            assert rejected(lambda: m.validate_result(task_config, inputs))
            checks.append('reject_changed_saved_numerical_source')
            source.write_text(source_text)
            write(task_config['phi'], {'fixture': 'changed phi'})
            assert rejected(lambda: m.validate_result(task_config, inputs))
            checks.append('reject_changed_phi_bytes')
        finally:
            os.chdir(original)
            m.HERE, m.ROOT, m.REVIEWS = old_here, old_root, old_reviews
    result = {'status': 'PASS', 'scope': 'stdlib_adversarial_fixture_validation_only',
              'checks': checks, 'training_started_or_modified': False, 'method_validated': False}
    m.preserve(Path(__file__).with_name('validation.json'), result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
