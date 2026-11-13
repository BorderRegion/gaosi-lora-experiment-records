"""Synthetic H1-shaped fixtures derived from archived L; no real H1 claim."""
import copy
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('h1_ledger', Path(__file__).with_name('review_commonwarm_ledger.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    reference = m.load_archive(Path('records/observed_blocking_core_monitor_20261101_070654_utc'), m.H2)
    config = dict(reference['config'], out=m.H1, horizon='H1', start_episode=64,
                  initial_phi=m.H2 + '/phi_0512.pt', load_initial_meta_optimizer=True,
                  purpose='common_512_warm_alternate_H1')
    events = [copy.deepcopy(e) for e in reference['events'] if e.get('episode', -1) >= 64]
    for event in events:
        if event['event'] == 'inner':
            event['exact_window'] = False
    gradients = [copy.deepcopy(e) for e in reference['gradients'] if e['episode'] >= 64]
    def check(c=config, es=events, gs=gradients, complete=True):
        return m.check_rows(c, es, gs, reference['config'], reference['events'], complete)
    result = check()
    assert result['completed_additional_episodes'] == 128
    assert result['completed_additional_inner_steps'] == 2048
    assert result['cumulative_inner_steps'] == 2560
    checks = ['synthetic_H1_shape_accepts_128_additional_episodes_and_2048_steps']
    def rejected(label, function):
        try:
            function()
        except ValueError:
            checks.append(label)
            return
        raise AssertionError('Invalid fixture accepted: ' + label)
    observed = Path('records/pipeline_monitor_20261103_165102_utc/snapshot/runs/phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8/events.jsonl')
    actual_start = next(json.loads(s) for s in observed.read_text().splitlines()
                        if json.loads(s)['event'] == 'episode_start')
    changed = copy.deepcopy(events)
    first_start = next(e for e in changed if e['event'] == 'episode_start')
    assert actual_start['episode'] == first_start['episode'] == 64
    assert first_start['noise']['boundary_seconds'] != actual_start['noise']['boundary_seconds']
    assert {k:v for k,v in first_start['noise'].items() if k != 'boundary_seconds'} == {
        k:v for k,v in actual_start['noise'].items() if k != 'boundary_seconds'}
    first_start['noise'] = copy.deepcopy(actual_start['noise'])
    assert check(es=changed)['completed_additional_episodes'] == 128
    checks.append('accept_measured_boundary_duration_difference_from_actual_H1_first_start')
    for label, key, value in [
        ('reject_changed_noise_seed', 'noise_seed', 123),
        ('reject_changed_noise_damage', 'composed_relative_damage', .123),
        ('reject_nonfinite_boundary_duration', 'boundary_seconds', float('nan')),
        ('reject_negative_boundary_duration', 'boundary_seconds', -1),
    ]:
        changed = copy.deepcopy(events)
        next(e for e in changed if e['event'] == 'episode_start')['noise'][key] = value
        rejected(label, lambda es=changed: check(es=es))
    changed = copy.deepcopy(events)
    next(e for e in changed if e['event'] == 'episode_start')['noise'].pop('boundary_seconds')
    rejected('reject_missing_noise_metadata_field', lambda: check(es=changed))
    rejected('reject_incomplete_H1_completion', lambda: check(es=[e for e in events if e.get('episode') != 191], gs=gradients[:-1]))
    rejected('reject_changed_seed', lambda: check(c=dict(config, seed=29)))
    rejected('reject_missing_shared_Adam_state_declaration', lambda: check(c=dict(config, load_initial_meta_optimizer=False)))
    rejected('reject_missing_gradient_row', lambda: check(gs=gradients[1:]))
    for label, event_type, key, value in [
        ('reject_changed_task', 'episode_start', 'task_file', 'data/synthetic/different_meta.jsonl'),
        ('reject_changed_dose', 'episode_start', 'q_train', .123),
        ('reject_insufficient_or_unmatched_route_tokens', 'inner', 'task_tokens', 511),
        ('reject_unexpected_H2_exact_flag', 'inner', 'exact_window', True),
        ('reject_changed_cumulative_budget', 'episode_end', 'inner_updates', 3584),
        ('reject_nonfinite_objective', 'episode_end', 'objective', float('nan')),
    ]:
        changed = copy.deepcopy(events)
        next(e for e in changed if e['event'] == event_type)[key] = value
        rejected(label, lambda es=changed: check(es=es))
    changed = copy.deepcopy(gradients)
    changed[0]['update_authorized_by_guard'] = False
    rejected('reject_unauthorized_gradient_update', lambda: check(gs=changed))
    partial = check(es=[e for e in events if e.get('episode', -1) < 70], gs=gradients[:6], complete=False)
    assert partial['completed_additional_episodes'] == 6 and not partial['full_commonwarm_H1_ledger_present']
    checks.append('partial_H1_explicitly_remains_incomplete')
    result = {'status': 'PASS', 'fixture': 'Archived L-H2 logs transformed to synthetic H1 metadata/flags; no actual H1 execution or correctness evidence.',
              'checks': checks, 'reference_archive': str(reference['folder'].relative_to(m.ROOT)),
              'reference_manifest_sha256': m.sha(reference['files'][-1]),
              'actual_first_start_source': str(observed), 'actual_first_start_sha256': m.sha(observed),
              'source_sha256': m.sha(Path(__file__).with_name('review_commonwarm_ledger.py')),
              'training_modified_or_started': False, 'method_validated': False}
    with Path(__file__).with_name('validation.json').open('x') as f:
        f.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
