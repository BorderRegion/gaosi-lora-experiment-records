"""Compare pending clean routing to the actual mixed core and specialization.

This is an evidence review and preparation only. It never submits training,
changes production files, or emits a scientific continuation receipt.
"""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    core_name = 'phi_mix_O1_X_H2_core_s17_observed_blocking_v8'
    spec_name = 'phi_mix_O1_X_specialize512_s17_observed_blocking_v8'
    core_capture = ROOT / 'records/pipeline_monitor_20261110_062032_utc'
    spec_capture = ROOT / 'records/pipeline_monitor_20261110_125941_utc'
    inputs = {}

    def captured(capture, relative):
        path = capture / 'snapshot' / relative
        manifest = capture / 'archive_source_sha256.json'
        name = str(path.relative_to(ROOT))
        assert sha(path) == read(manifest)[name]
        inputs[name] = sha(path)
        inputs[str(manifest.relative_to(ROOT))] = sha(manifest)
        return path

    core = read(captured(core_capture, f'configs/{core_name}.json'))
    specialization = read(captured(spec_capture, f'runs/{spec_name}/config.json'))
    core_events = [json.loads(line) for line in captured(
        core_capture, f'runs/{core_name}/events.jsonl').read_text().splitlines()]
    spec_events = [json.loads(line) for line in captured(
        spec_capture, f'runs/{spec_name}/events.jsonl').read_text().splitlines()]
    observed = read(captured(spec_capture,
        f'records/pipeline_observed_blocking_v8/meta_entries/{spec_name}.json'))
    assert sha(ROOT / 'gaosi/meta.py') == observed['pinned_files_sha256']['gaosi/meta.py']
    inputs['gaosi/meta.py'] = sha(ROOT / 'gaosi/meta.py')
    for name in ('organization_queue.py', 'confirmation_queue.py'):
        path = ROOT / 'records/pipeline_observed_blocking_v8/tools' / name
        source = path.read_text()
        assert "episode_K=" in source and "+ [8] * 64, q_cycle=[0.]" in source
        inputs[str(path.relative_to(ROOT))] = sha(path)

    starts = [e for e in core_events if e['event'] == 'episode_start']
    short = [e for e in spec_events if e['event'] == 'episode_start']
    assert [e['episode'] for e in starts] == list(range(192))
    assert [e['episode'] for e in short] == list(range(64))
    assert sum(core['episode_K']) == 2560 and len(core['parents']) == 2
    assert specialization['episode_K'] == [8] * 64
    assert specialization['known_warm_episodes'] == 0
    assert specialization['initial_phi'].endswith('/phi_2560.pt')
    assert specialization['load_initial_meta_optimizer']

    # The 64 observed new episodes in the complete core identify the frozen
    # ordering of all 64 meta worlds without a new remote data read.
    ordered_worlds = {}
    for event in starts:
        episode = event['episode']
        if event['mode'] == 'new':
            index = (episode // 2) % 64
            assert index not in ordered_worlds
            ordered_worlds[index] = event['task_file']
    assert set(ordered_worlds) == set(range(64))
    differences = []
    for offset, actual in enumerate(short):
        absolute = len(starts) + offset
        mode = 'known' if absolute < core['known_warm_episodes'] or absolute % 2 == 0 else 'new'
        parent = core['parent_data'][absolute % len(core['parents'])]
        task = parent if mode == 'known' else ordered_worlds[(absolute // 2) % 64]
        seed = core['seed'] * 100000 + absolute
        intended_seed = actual['noise']['noise_seed']
        assert intended_seed == core['seed'] * 100000 + offset
        assert actual['mode'] == ('known' if offset % 2 == 0 else 'new')
        assert actual['task_file'] == (parent if offset % 2 == 0 else ordered_worlds[(offset // 2) % 64])
        differences.append({'specialization_episode': offset,
                            'pending_clean_absolute_episode': absolute,
                            'mixed_task': actual['task_file'], 'pending_clean_task': task,
                            'task_differs': actual['task_file'] != task,
                            'mode_matches': actual['mode'] == mode,
                            'parent_matches': actual['parent_file'] == parent,
                            'mixed_stream_seed': intended_seed, 'pending_clean_stream_seed': seed})
    assert sum(row['task_differs'] for row in differences) == 32
    assert all(row['mode_matches'] and row['parent_matches'] for row in differences)
    assert all(row['mixed_stream_seed'] != row['pending_clean_stream_seed'] for row in differences)

    clean_core = copy.deepcopy(core)
    clean_core.update(out='runs/phi_clean_O1_X_core2560_paired_s17_v1', q_cycle=[0.],
                      purpose='clean_core_same_non_noise_episode_schedule_as_selected_mixed_core')
    assert not clean_core.get('initial_phi') and not clean_core.get('load_initial_meta_optimizer')
    clean_spec = copy.deepcopy(specialization)
    clean_spec.update(out='runs/phi_clean_O1_X_specialize512_paired_s17_v1', q_cycle=[0.],
                      initial_phi=clean_core['out'] + '/phi_2560.pt',
                      purpose='clean_specialization_same_non_noise_episode_schedule_as_mixed_O1')
    # Compare every config field except the declared treatment and artifact
    # references. No other training option is changed by this prepared plan.
    assert {k: v for k, v in core.items() if k not in ('out', 'q_cycle', 'purpose')} == {
        k: v for k, v in clean_core.items() if k not in ('out', 'q_cycle', 'purpose')}
    assert {k: v for k, v in specialization.items() if k not in ('out', 'q_cycle', 'purpose', 'initial_phi')} == {
        k: v for k, v in clean_spec.items() if k not in ('out', 'q_cycle', 'purpose', 'initial_phi')}
    preserve(HERE / 'prepared_clean_core_config.json', clean_core)
    preserve(HERE / 'prepared_clean_specialization_config.json', clean_spec)
    preserve(HERE / 'review.json', {
        'status': 'PENDING_CLEAN_SCHEDULE_CONFOUND_IDENTIFIED_CORRECTION_PREPARED_NOT_SUBMITTED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source_sha256': sha(Path(__file__)), 'evidence_sha256': inputs,
        'core_inner_steps': 2560, 'specialization_inner_steps': 512,
        'total_budget_unchanged': 3072, 'affected_specialization_tasks': 32,
        'affected_specialization_stream_seeds': 64,
        'differences': differences,
        'prepared_configs': {str(path.relative_to(ROOT)): sha(path) for path in (
            HERE / 'prepared_clean_core_config.json', HERE / 'prepared_clean_specialization_config.json')},
        'next_action': 'Before clean submission, route the pending primary and independent clean controls through separate core and warm-start specialization stages; preserve phi and Adam state, reset episode schedule exactly as mixed O1. Review both actual stages before freezing. Do not launch an additional unpaired clean arm.',
        'execution_note': 'The live organization controller has already imported its queue. Editing its source on disk alone cannot correct its in-memory future schedule. Arrange one explicit, evidence-recorded controller handoff at a completed meta review boundary; do not interrupt an active training leaf.',
        'current_training_modified_or_restarted': False,
        'production_source_modified': False, 'training_submitted': False,
        'scientific_promotion_generated': False, 'method_validated': False,
        'full_goal_complete': False,
        'limitations': ['This is static schedule evidence, not an efficacy result.',
                       'The nominal 3072-step budget was already matched; this issue concerns data and stream-seed pairing needed for strict q_train attribution.',
                       'Prepared configs have not undergone remote input-manifest preparation or actual execution.']})
    print(json.dumps({'status': 'CLEAN_SCHEDULE_CONFOUND_IDENTIFIED',
                      'affected_tasks': 32, 'affected_stream_seeds': 64,
                      'training_submitted': False, 'production_modified': False}))


if __name__ == '__main__':
    main()
