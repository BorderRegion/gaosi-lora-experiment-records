"""Check a fixed captured O1 prefix and replay its saved Adam updates on CPU.

This partial review cannot authorize a downstream stage. Mutable training files
are not read: text comes from a fixed observer capture; completed gradients and
pre-episode checkpoints are immutable artifacts bound to that capture's ledger.
"""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics


ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path(__file__).resolve().parent
OBSERVATION = 'records/pipeline_monitor_20261110_011948_utc'
RUN = 'runs/phi_mix_O1_X_specialize512_s17_observed_blocking_v8'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    import torch
    torch.set_num_threads(2)
    output = HERE / 'review.json'
    assert not output.exists()
    capture = ROOT / OBSERVATION
    run = ROOT / RUN
    saved_run = capture / 'snapshot' / RUN
    files = {Path(__file__).resolve()}

    def read(path):
        files.add(path)
        return json.loads(path.read_text())

    def lines(path):
        files.add(path)
        raw = path.read_bytes()
        assert raw.endswith(b'\n')
        return [json.loads(line) for line in raw.splitlines()]

    def finite(value):
        if torch.is_tensor(value):
            assert value.device.type == 'cpu'
            return bool(torch.isfinite(value).all())
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, (tuple, list)):
            return all(finite(v) for v in value)
        return not isinstance(value, float) or math.isfinite(value)

    def load(path):
        files.add(path)
        value = torch.load(path, map_location='cpu', weights_only=False)
        assert finite(value)
        return value

    def equal(left, right):
        assert left.keys() == right.keys()
        assert all(torch.equal(left[k], right[k]) for k in left)

    archive_manifest = read(capture / 'archive_source_sha256.json')
    for name, expected in archive_manifest.items():
        assert sha(ROOT / name) == expected, name
    observation = read(capture / 'result.json')
    observed, = [m for m in observation['meta_runs'] if m['run'] == RUN]
    assert observed['committed_episodes'] == 3 and observed['committed_inner_updates'] == 24
    config = read(saved_run / 'config.json')
    assert config['episode_K'] == [8] * 64 and config['organization'] == 'O1'
    assert config['architecture'] == 'X' and config['horizon'] == 'EXACT_H2_tail'
    assert config['rank'] == 32 and config['targets'] == 'all7'
    assert config['seq'] == config['effective_tokens'] == 512
    assert config['load_initial_meta_optimizer'] and config.get('start_episode', 0) == 0
    assert config['initial_phi'] == 'runs/phi_mix_O1_X_H2_core_s17_observed_blocking_v8/phi_2560.pt'
    manifest_path = ROOT / 'records/pipeline_observed_blocking_v8/meta_entries' / (run.name + '.json')
    manifest = read(manifest_path)
    assert manifest['formal_data_used'] is False and manifest['requires_separate_scientific_review']
    assert manifest['executed_inner_updates'] == 512 and manifest['raw_norm_stop_before_update'] == 1e6
    pins = manifest['pinned_files_sha256']
    assert read(ROOT / manifest['config']) == config
    for name, expected in pins.items():
        assert sha(ROOT / name) == expected, name
        files.add(ROOT / name)
    snapshot_sources = read(saved_run / 'source_sha256.json')
    assert snapshot_sources == {Path(n).name: h for n, h in pins.items() if n.startswith('gaosi/')}
    for name, expected in snapshot_sources.items():
        path = saved_run / 'source' / name
        assert sha(path) == expected
        files.add(path)
    replay_path = ROOT / 'records/pipeline_observer_20261101_1143/replay_saved_adam.py'
    assert sha(replay_path) == '9eedbe61dc0f18e26462ed5ebe658cc491600079afd1a64e6a35b486d8df95a2'
    files.add(replay_path)
    spec = importlib.util.spec_from_file_location('adam_comparison', replay_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    events = lines(saved_run / 'events.jsonl')
    gradients = lines(saved_run / 'raw_gradients.jsonl')
    ends = [e for e in events if e['event'] == 'episode_end']
    starts = [e for e in events if e['event'] == 'episode_start']
    assert [e['episode'] for e in ends] == [g['episode'] for g in gradients] == [0, 1, 2]
    assert [e['episode'] for e in starts] == [0, 1, 2, 3]
    warm = load(ROOT / config['initial_phi'])
    initial_optimizer_steps = {float(s['step']) for s in warm['meta_optimizer']['state'].values()}
    assert initial_optimizer_steps == {192.0}
    rows = []
    durations = []
    for episode, (end, row) in enumerate(zip(ends, gradients)):
        inner = [e for e in events if e['event'] == 'inner' and e['episode'] == episode]
        assert [e['k'] for e in inner] == list(range(1, 9))
        assert [e['k'] for e in inner if e['exact_window']] == [7, 8]
        assert all(e[k] >= 512 for e in inner for k in ('task_tokens', 'language_tokens', 'keep_tokens'))
        assert finite(inner) and finite(end)
        assert end['inner_updates'] == 8 * (episode + 1)
        artifact_path = run / f'gradient_episode_{episode:03d}.pt'
        assert sha(artifact_path) == row['artifact_sha256']
        artifact = load(artifact_path)
        assert artifact['episode'] == episode and artifact['config_sha256'] == pins[manifest['config']]
        assert all(row[k] for k in ('raw_all_finite', 'clipped_all_finite',
                                   'phi_unchanged_during_rollout', 'update_authorized_by_guard'))
        assert not row.get('error')
        equal(artifact['starting_phi'], artifact['ending_phi_before_update'])
        raw, clipped = artifact['raw_gradient'], artifact['clipped_gradient']
        assert raw.keys() == clipped.keys() == artifact['starting_phi'].keys()
        assert all(g.dtype == torch.float32 for g in raw.values())
        norm = math.sqrt(sum(float(g.double().square().sum()) for g in raw.values()))
        assert norm == row['raw_norm_cpu_fp64'] == row['clip_return'] == end['phi_grad_norm']
        assert norm == float(artifact['clip_return']) and norm <= manifest['raw_norm_stop_before_update']
        expected_clip = {n: ((g.double() * min(1., 1. / (norm + 1e-6))).float()
                             if norm + 1e-6 > 1. else g) for n, g in raw.items()}
        equal(clipped, expected_clip)
        before_path = run / f'before_episode_{episode:03d}.pt'
        after_path = run / f'before_episode_{episode + 1:03d}.pt'
        before, after = load(before_path), load(after_path)
        equal(before['phi'], artifact['starting_phi'])
        if episode == 0:
            assert sha(before_path) == pins[config['initial_phi']]
        else:
            assert before['config'] == config and before['next_episode'] == episode
            assert before['inner_updates'] == episode * 8
        assert after['config'] == config and after['next_episode'] == episode + 1
        assert after['inner_updates'] == (episode + 1) * 8
        names = list(artifact['starting_phi'])
        parameters = [torch.nn.Parameter(artifact['starting_phi'][name].clone()) for name in names]
        optimizer = torch.optim.Adam(parameters, lr=config['meta_lr'], foreach=False)
        optimizer.load_state_dict(copy.deepcopy(before['meta_optimizer']))
        assert {float(s['step']) for s in optimizer.state_dict()['state'].values()} == {192. + episode}
        for parameter, name in zip(parameters, names):
            parameter.grad = clipped[name].clone()
        optimizer.step()
        actual = optimizer.state_dict()
        assert actual['param_groups'] == after['meta_optimizer']['param_groups']
        comparisons = [helper.compare(parameter, after['phi'][name]) for name, parameter in zip(names, parameters)]
        for index in actual['state']:
            left, right = actual['state'][index], after['meta_optimizer']['state'][index]
            assert left.keys() == right.keys() and float(right['step']) == 193. + episode
            comparisons += [helper.compare(left[key], right[key]) for key in left]
        assert all(c['finite'] and c['allclose'] for c in comparisons)
        seconds = (dt.datetime.fromisoformat(end['utc']) - dt.datetime.fromisoformat(starts[episode]['utc'])).total_seconds()
        durations.append(seconds)
        rows.append({'episode': episode, 'raw_norm_recomputed': norm,
                     'saved_phi_unchanged_inside_rollout': True, 'clip_bitwise_matches': True,
                     'CPU_Adam_update_allclose': True,
                     'maximum_update_or_optimizer_absolute_error': max(c['max_absolute_error'] for c in comparisons),
                     'episode_wall_seconds': seconds})
    endpoint = load(saved_run / 'latest.pt')
    assert sha(saved_run / 'latest.pt') == observed['checkpoint']['sha256']
    assert sha(saved_run / 'latest.pt') == sha(run / 'before_episode_003.pt')
    assert endpoint['next_episode'] == 3 and endpoint['inner_updates'] == 24 and endpoint['config'] == config
    value = {'status': 'PARTIAL_O1_SPECIALIZATION_SAVED_PREFIX_AND_CPU_ADAM_REPLAY_PASSED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'observation': OBSERVATION, 'run': RUN, 'committed_episodes_checked': 3,
             'committed_inner_steps_checked': 24, 'configured_inner_steps': 512,
             'initial_Adam_optimizer_step': 192, 'ending_Adam_optimizer_step': 195,
             'all_three_saved_updates_replayed_from_corresponding_saved_states': True,
             'comparison_tolerance': {'rtol': 2e-5, 'atol': 1e-8}, 'episodes': rows,
             'mean_episode_wall_seconds': statistics.mean(durations),
             'remaining_61_episodes_same_speed_hours': statistics.mean(durations) * 61 / 3600,
             'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)},
             'method_validated': False, 'downstream_promotion_allowed': False,
             'production_modified_or_restarted': False, 'full_goal_complete': False,
             'limitations': ['Only three committed episodes are audited; no terminal scientific review or permission to promote.',
                            'CPU updates consume saved gradients and restart at each saved state; no independent derivative or accumulated CPU trajectory.',
                            'Same-speed estimate excludes scheduling delays and changing episode costs; it covers only this O1 specialization.',
                            'Finite gradients, parameter changes and training objectives do not establish comparative efficacy.']}
    with output.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'review_sha256': sha(output),
                      'episodes_checked': 3, 'inner_steps_checked': 24,
                      'remaining_61_episodes_same_speed_hours': value['remaining_61_episodes_same_speed_hours']}))


if __name__ == '__main__':
    main()
