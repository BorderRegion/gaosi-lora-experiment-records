"""Prepare a separate, guarded confirmation continuation; never submit jobs."""
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REL = str(HERE.relative_to(ROOT))
MODULE = REL.replace('/', '.') + '.confirmation_runner_corrected'


def replace_once(source, before, after):
    assert source.count(before) == 1, before
    return source.replace(before, after)


def save(name, source, original=None):
    with (HERE / name).open('x') as handle:
        handle.write(source)
    compile(source, str(HERE / name), 'exec')
    if original:
        with (HERE / (name + '.diff')).open('x') as handle:
            handle.write(''.join(difflib.unified_diff(
                (ROOT / original).read_text().splitlines(True), source.splitlines(True),
                fromfile=original, tofile=REL + '/' + name)))


def main():
    original = 'gaosi/confirmation.py'
    runner = (ROOT / original).read_text()
    runner = replace_once(runner, 'from .scale import sha256', 'from gaosi.scale import sha256')
    for module in ['meta', 'recover', 'reflow', 'continual']:
        runner = replace_once(runner, 'from .' + module + ' import ', 'from gaosi.' + module + ' import ')
    start = runner.index("        stage = config['confirmation_stage']")
    end = runner.index("        paired_keys = ['parents', 'parent_data']", start)
    runner = runner[:start] + '''        stage = config['confirmation_stage']
        fresh = stage in ['mixed_core_from_zero', 'clean_core_from_zero']
        specializing = stage in ['organization_specialization', 'clean_organization_specialization']
        clean = stage in ['clean_core_from_zero', 'clean_organization_specialization']
        if fresh:
            if config.get('initial_phi') or config.get('start_episode') or config.get('load_initial_meta_optimizer'):
                raise ValueError('An independent phi must start from zero output and fresh meta optimizer')
        elif specializing:
            import torch
            parent = torch.load(config['initial_phi'], map_location='cpu', weights_only=False)
            parent_config = parent['config']
            parent_stage = 'clean_core_from_zero' if clean else 'mixed_core_from_zero'
            if (parent_config['confirmation_seed'] != seed or parent_config['seed'] != seed
                    or parent_config['confirmation_stage'] != parent_stage
                    or parent_config['organization'] != 'O1'
                    or parent_config['episode_K'] != schedule
                    or parent_config['known_warm_episodes'] != 64
                    or parent_config['q_cycle'] != ([0.] if clean else reference['q_cycle'])
                    or parent_config.get('initial_phi') or parent_config.get('start_episode')
                    or parent['inner_updates'] != selected['core_inner_steps']
                    or parent['next_episode'] != len(schedule)
                    or not config['load_initial_meta_optimizer'] or config.get('start_episode', 0) != 0):
                raise ValueError('Specialization must inherit its own completed mixed/clean core and restart episode zero')
            for key in ['world_manifest', 'meta_reference_config', 'architecture', 'meta_lr',
                        'inner_lr', 'seq', 'effective_tokens', 'retain_weight', 'penalty', 'horizon']:
                if parent_config[key] != config[key]:
                    raise ValueError('Specialization changed its own core setting: ' + key)
            states = parent['meta_optimizer']['state']
            if not states or any(float(value['step']) != len(schedule) for value in states.values()):
                raise ValueError('Specialization must inherit the complete core Adam state')
        else:
            raise ValueError('Unknown independent meta stage; concatenated clean control is not paired')
        expected = [8] * 64 if specializing else schedule
        if config['episode_K'] != expected or config['known_warm_episodes'] != (0 if specializing else 64):
            raise ValueError('Independent core/clean/specialization schedule changed')
        if config['q_cycle'] != ([0.] if clean else reference['q_cycle']):
            raise ValueError('Independent training noise schedule changed')
        if stage != 'organization_specialization' and config['organization'] != 'O1':
            raise ValueError('Independent cores and clean rules are trained in O1')
''' + runner[end:]
    save('confirmation_runner_corrected.py', runner, original)

    adapter = '''"""Use the existing evidence guard with the versioned paired-stage validator."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(HERE))
import train_guarded_meta as original
from confirmation_runner_corrected import check_config

_original_pins = original.pinned_source_files
_original_check = original.check_original_entry


def pinned_source_files():
    return _original_pins() | {
        str((HERE / name).relative_to(ROOT)) for name in [
            'confirmation_runner_corrected.py', 'confirmation_queue_corrected.py',
            'guarded_pipeline_corrected.py', 'train_guarded_confirmation_paired.py']}


def check_entry(config, entry):
    if entry == 'confirmation':
        check_config(config)
    else:
        _original_check(config, entry)


original.pinned_source_files = pinned_source_files
original.check_original_entry = check_entry
digest, prepare, LOCK = original.digest, original.prepare, original.LOCK

if __name__ == '__main__':
    original.main()
'''
    save('train_guarded_confirmation_paired.py', adapter)
    original = 'tools/guarded_pipeline.py'
    guard = (ROOT / original).read_text()
    guard = replace_once(guard, 'from train_guarded_meta import digest, prepare, LOCK',
                         'from train_guarded_confirmation_paired import digest, prepare, LOCK')
    guard = replace_once(guard, "module == 'gaosi.confirmation'", "module in ['gaosi.confirmation', '" + MODULE + "']")
    guard = replace_once(guard, "'tools/train_guarded_meta.py'", repr(REL + '/train_guarded_confirmation_paired.py'))
    save('guarded_pipeline_corrected.py', guard, original)

    original = 'records/pipeline_observed_blocking_v8/tools/confirmation_queue.py'
    queue = (ROOT / original).read_text()
    queue = replace_once(queue, "sys.path.insert(0, str(Path.cwd()))", """sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / 'tools'))
sys.path.insert(0, str(Path.cwd() / 'records/pipeline_observed_blocking_v8/tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))""")
    queue = replace_once(queue, 'from phi_core_queue import run_job', 'from guarded_pipeline_corrected import run_job')
    queue = replace_once(queue, "'-m', 'gaosi.confirmation', '--config', path", "'-m', ('" + MODULE + "' if action == 'meta' else 'gaosi.confirmation'), '--config', path")
    start = queue.index('    clean = copy.deepcopy(core)\n')
    end = queue.index("    preserve_json(f'records/confirmation_phi_frozen", start)
    queue = queue[:start] + '''    clean_core = copy.deepcopy(core)
    clean_core.update(q_cycle=[0.], confirmation_stage='clean_core_from_zero',
                      purpose='independent_clean_core_matched_non_noise_schedule')
    clean_core_result = entry(f'confirm_phi_clean_O1_{architecture}_core_paired_s{seed}_observed_blocking_v8',
                              clean_core, 'meta', seed, upstream + clean_core['parents'], data, [])
    if clean_core_result['inner_updates'] != selected['core_inner_steps']:
        raise ValueError('Independent clean core budget differs from mixed core')
    clean = copy.deepcopy(clean_core)
    clean.update(episode_K=[8] * 64, known_warm_episodes=0,
                 initial_phi=clean_core_result['checkpoint'], load_initial_meta_optimizer=True,
                 confirmation_stage='clean_organization_specialization',
                 purpose='independent_clean_specialization_matched_non_noise_schedule')
    clean_result = entry(f'confirm_phi_clean_O1_{architecture}_matched_s{seed}_observed_blocking_v8', clean, 'meta', seed,
                         upstream + clean['parents'] + [clean_core_result['checkpoint']], data, [])
    if clean_result['inner_updates'] != 512:
        raise ValueError('Independent clean specialization budget differs from mixed specialization')
    preserve_json(f'records/confirmation_clean_stage_pairing_s{seed}_v1.json', {
        'status': 'TWO_ACTUAL_CLEAN_STAGES_COMPLETED_AND_REVIEWED', 'seed': seed,
        'core_checkpoint': clean_core_result['checkpoint'],
        'specialization_checkpoint': clean_result['checkpoint'],
        'core_inner_steps': clean_core_result['inner_updates'],
        'specialization_inner_steps': clean_result['inner_updates'],
        'total_inner_steps': clean_core_result['inner_updates'] + clean_result['inner_updates'],
        'optimizer_inherited': True, 'specialization_episode_index_restarts_at': 0,
        'formal_data_used': False, 'method_validated': False})
    freezes['O1_clean'] = make_freeze(seed, 'O1', 'clean', clean_result['checkpoint'], original, selected)
''' + queue[end:]
    save('confirmation_queue_corrected.py', queue, original)
    sources = [ROOT / p for p in ['gaosi/confirmation.py', 'tools/guarded_pipeline.py',
               'tools/train_guarded_meta.py', original, 'gaosi/meta.py']]
    prepared = [p for p in HERE.iterdir() if p.is_file()]
    report = {'status': 'VERSIONED_CONFIRMATION_CLEAN_PAIRING_PREPARED_NOT_SUBMITTED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'original_source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              'prepared_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in prepared},
              'production_source_modified': False, 'training_submitted': False,
              'requires_before_activation': ['CPU regression validation of corrected routing and failure cases',
                  'Remote source rehash and explicit evidence-recorded controller handoff before independent training',
                  'Actual independent audits of every clean core and specialization before freezing'],
              'method_validated': False, 'full_goal_complete': False}
    with (HERE / 'preparation.json').open('x') as handle:
        json.dump(report, handle, indent=2)
        handle.write('\n')
    print(json.dumps({'status': report['status'], 'prepared_files': len(prepared), 'training_submitted': False}))


if __name__ == '__main__':
    main()
