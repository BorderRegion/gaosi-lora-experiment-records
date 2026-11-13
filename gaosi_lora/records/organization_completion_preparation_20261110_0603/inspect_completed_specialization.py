"""Inspect a completed organization specialization after its audit and Adam replay.

Uses saved CPU tensors and immutable logs; never trains, approves, or measures
comparative effectiveness. Each organization still needs a scientific judgment.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path.cwd()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    import torch
    torch.set_num_threads(2)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--replay', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    assert not args.out.exists()
    audit, replay = read(args.audit), read(args.replay)
    assert audit['status'] == 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
    assert replay['status'] == 'CPU_SAVED_ADAM_UPDATES_REPLAYED_REQUIRES_REVIEW'
    assert replay['audit_sha256'] == sha(args.audit)
    assert Path(replay['audit']).resolve() == args.audit.resolve()
    assert replay['all_finite'] and replay['allclose']
    assert replay['all_starting_phi_equal'] and replay['all_optimizer_param_groups_equal']
    assert replay['comparison_tolerance'] == {'rtol': 2e-5, 'atol': 1e-8}
    run, config = Path(audit['run']), audit['config']
    assert Path(replay['run']).resolve() == run.resolve()
    assert not args.out.resolve().is_relative_to(run.resolve())
    assert config == read(run / 'config.json')
    assert config['purpose'] == 'equal_512_update_organization_specialization'
    assert config['organization'] in ('O1', 'O2', 'O3', 'O4')
    assert config['architecture'] == 'X' and config['rank'] == 32 and config['targets'] == 'all7'
    assert config['episode_K'] == [8] * 64 and config.get('start_episode', 0) == 0
    assert config['horizon'] == 'EXACT_H2_tail'
    assert config['seq'] == config['effective_tokens'] == 512
    assert config['known_warm_episodes'] == 0 and config['q_cycle'] == [0., .5, .7]
    assert config['retain_fact_support'] and config['load_initial_meta_optimizer']
    assert config['seed'] == 17
    assert audit['episodes_committed'] == replay['episodes_checked'] == 64
    assert audit['executed_inner_updates'] == audit['cumulative_inner_updates'] == 512
    assert replay['initial_optimizer_step'] == 192.
    evidence = {str(path): sha(path) for path in (args.audit, args.replay, Path(__file__))}
    for name, expected in audit['evidence_sha256'].items():
        assert sha(name) == expected, name
        evidence[name] = expected

    def remember(path):
        path = Path(path)
        digest = sha(path)
        if str(path) in audit['evidence_sha256']:
            assert digest == audit['evidence_sha256'][str(path)]
        evidence[str(path)] = digest
        return path

    plan = read(remember(ROOT / 'records/organization_training_plan.json'))
    assert plan['status'] == 'ORGANIZATION_TRAINING_PLAN' and plan['formal_data_used'] is False
    assert plan['architecture'] == config['architecture']
    assert plan['policy']['specialization_episode_K'] == config['episode_K']
    assert plan['core_inner_steps'] == 2560 and plan['additional_inner_steps_each'] == 512
    assert plan['total_O1_mix_clean_inner_steps'] == 3072
    assert config['initial_phi'] == plan['common_phi']
    assert sha(remember(config['initial_phi'])) == plan['common_phi_sha256']
    if config['organization'] == 'O4':
        assert config['parents'] == plan['O4_parents']
    manifest = read(remember(run / 'observed_training_manifest.json'))
    assert manifest['formal_data_used'] is False and manifest['requires_separate_scientific_review']
    assert manifest['raw_norm_stop_before_update'] == 1e6
    worlds = read(remember(config['world_manifest']))['worlds']
    worlds = [world for world in worlds if world['split'] == 'meta']
    assert worlds
    events = [json.loads(line) for line in remember(run / 'events.jsonl').read_text().splitlines()]
    starts = [event for event in events if event['event'] == 'episode_start']
    ends = [event for event in events if event['event'] == 'episode_end']
    assert [event['episode'] for event in starts] == [event['episode'] for event in ends] == list(range(64))
    all_inner = [event for event in events if event['event'] == 'inner']
    assert len(all_inner) == 512
    groups, durations = Counter(), []
    for episode, (start, end) in enumerate(zip(starts, ends)):
        parent_index = episode % len(config['parents'])
        mode = 'known' if episode % 2 == 0 else 'new'
        parent_file = config['parent_data'][parent_index]
        task_file = parent_file if mode == 'known' else str(Path('data/synthetic') / worlds[(episode // 2) % len(worlds)]['file'])
        retain_file = config['historical_data'][parent_index] if config['organization'] == 'O4' else parent_file
        q = config['q_cycle'][episode % len(config['q_cycle'])]
        assert start['mode'] == mode and start['K'] == 8 and start['q_train'] == q
        assert start['parent_file'] == parent_file and start['task_file'] == task_file
        assert start['retain_file'] == retain_file and start['retain_route'] == 'half_language_half_known_facts'
        assert start['noise']['q'] == q and start['noise']['scheme'] == 'subspace'
        assert start['noise']['noise_seed'] == config['seed'] * 100000 + episode
        for path in (parent_file, task_file, retain_file):
            assert path in manifest['pinned_files_sha256'], path
        inner = [event for event in all_inner if event['episode'] == episode]
        assert [event['k'] for event in inner] == list(range(1, 9))
        assert [event['k'] for event in inner if event['exact_window']] == [7, 8]
        assert all(event[key] >= 512 for event in inner for key in ('task_tokens', 'language_tokens', 'keep_tokens'))
        assert all(math.isfinite(event[key]) for event in inner for key in ('ld', 'lr', 'step_seconds'))
        assert all(math.isfinite(end[key]) for key in ('query_task', 'query_language', 'query_keep', 'objective'))
        assert end['inner_updates'] == (episode + 1) * 8
        seconds = (dt.datetime.fromisoformat(end['utc']) - dt.datetime.fromisoformat(start['utc'])).total_seconds()
        assert seconds > 0
        durations.append(seconds)
        groups[(mode, q, parent_file, retain_file)] += 1
    if config['organization'] == 'O4':
        history = [event for event in events if event['event'] == 'historical_base']
        assert [event['episode'] for event in history] == list(range(64))
        assert all(event['master_file'] == config['historical_bases'][event['episode'] % len(config['parents'])]
                   and event['merges'] == 1 for event in history)

    def load(path):
        return torch.load(remember(path), map_location='cpu', weights_only=False)

    def equal_tree(left, right):
        if torch.is_tensor(left):
            assert torch.is_tensor(right) and left.shape == right.shape and left.dtype == right.dtype
            assert torch.equal(left, right) and bool(torch.isfinite(left).all())
        elif isinstance(left, dict):
            assert left.keys() == right.keys()
            for key in left:
                equal_tree(left[key], right[key])
        elif isinstance(left, (list, tuple)):
            assert type(left) is type(right) and len(left) == len(right)
            for a, b in zip(left, right):
                equal_tree(a, b)
        else:
            assert left == right

    warm, final = load(config['initial_phi']), load(run / 'latest.pt')
    snapshot = load(run / 'phi_0512.pt')
    assert warm['inner_updates'] == 2560 and warm['next_episode'] == 192
    assert final['inner_updates'] == snapshot['inner_updates'] == 512
    assert final['next_episode'] == snapshot['next_episode'] == 64
    assert final['config'] == snapshot['config'] == config
    equal_tree(final['phi'], snapshot['phi'])
    equal_tree(final['meta_optimizer'], snapshot['meta_optimizer'])
    assert {float(state['step']) for state in warm['meta_optimizer']['state'].values()} == {192.}
    assert {float(state['step']) for state in final['meta_optimizer']['state'].values()} == {256.}
    assert warm['phi'].keys() == final['phi'].keys()
    count = sum(value.numel() for value in final['phi'].values())
    assert count == 83795
    change = math.sqrt(sum(float((final['phi'][key].double() - value.double()).square().sum())
                           for key, value in warm['phi'].items()))
    value = {'status': 'COMPLETED_ORGANIZATION_SPECIALIZATION_LEDGER_AND_WARM_ENDPOINT_TENSORS_INSPECTED',
             'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'run': str(run),
             'organization': config['organization'], 'episodes_checked': 64, 'inner_steps_checked': 512,
             'core_plus_specialization_steps': 3072, 'phi_parameters': count,
             'initial_optimizer_step': 192, 'final_optimizer_step': 256,
             'latest_and_512_snapshot_phi_and_optimizer_equal': True,
             'phi_change_l2_from_common_core': change,
             'minimum_recorded_effective_tokens': {key: min(event[key] for event in all_inner)
                                                   for key in ('task_tokens', 'language_tokens', 'keep_tokens')},
             'episode_mean_seconds': statistics.mean(durations),
             'recorded_route_groups': [{'mode': key[0], 'q': key[1], 'parent_file': key[2],
                                        'retain_file': key[3], 'count': count}
                                       for key, count in sorted(groups.items())],
             'evidence_sha256': evidence, 'method_validated': False, 'downstream_promotion_allowed': False,
             'training_modified_or_restarted': False, 'full_goal_complete': False,
             'limitations': ['Log flags and saved tensors do not independently recompute or validate MUSA derivatives.',
                            'CPU Adam replay consumes saved gradients from each actual saved starting state.',
                            'The frozen two-parent schedule couples known/new mode to parent parity; route counts are descriptive, not a factorial causal comparison.',
                            'Finite tensors and parameter motion do not prove efficacy, convergence, or superiority to Adam.',
                            'No scientific assessment or downstream promotion receipt is generated.']}
    with args.out.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({key: value[key] for key in ('status', 'run', 'organization', 'episodes_checked',
                                                'inner_steps_checked', 'phi_parameters', 'initial_optimizer_step',
                                                'final_optimizer_step', 'phi_change_l2_from_common_core')}))


if __name__ == '__main__':
    main()
