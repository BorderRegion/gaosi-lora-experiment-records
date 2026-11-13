"""Review archived common-warm H1 logs against L-H2; no tensors or approvals."""
import argparse
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
H2 = 'runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
H1 = 'runs/phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def load_archive(folder, run):
    folder = folder.resolve()
    require(folder.parent == ROOT / 'records', 'Require a preserved local observation')
    manifest_path = folder / 'archive_source_sha256.json'
    manifest = read(manifest_path)
    for name, expected in manifest.items():
        file = (ROOT / name).resolve()
        require(file.is_relative_to(folder) and sha(file) == expected, 'Archive source/hash mismatch: ' + name)
    path = folder / 'snapshot' / run
    files = [path / name for name in ['config.json', 'events.jsonl', 'raw_gradients.jsonl']]
    for file in files:
        require(str(file.relative_to(ROOT)) in manifest, 'Unmanifested ledger input')
    events = [json.loads(line) for line in files[1].read_text().splitlines()]
    gradients = [json.loads(line) for line in files[2].read_text().splitlines()]
    return {'folder': folder, 'run': path, 'manifest': manifest, 'files': files + [manifest_path],
            'config': read(files[0]), 'events': events, 'gradients': gradients}


def check_rows(config, events, gradients, reference_config, reference_events, require_complete=False):
    require(reference_config['architecture'] == 'L' and reference_config['organization'] == 'O1'
            and reference_config['horizon'] == 'EXACT_H2_tail'
            and reference_config['episode_K'] == [8] * 64 + [16] * 128
            and not reference_config.get('initial_phi') and not reference_config.get('start_episode', 0), 'Reference core differs')
    expected = dict(reference_config, out=H1, horizon='H1', start_episode=64,
                    initial_phi=H2 + '/phi_0512.pt', load_initial_meta_optimizer=True,
                    purpose='common_512_warm_alternate_H1')
    require(config == expected, 'H1 differs from declared common-warm continuation')
    starts = [e for e in events if e['event'] == 'episode_start']
    ends = [e for e in events if e['event'] == 'episode_end']
    require(0 < len(ends) <= 128, 'No committed H1 episodes or excess budget')
    require([e['episode'] for e in ends] == list(range(64, 64 + len(ends))), 'Noncontiguous H1 commits')
    require([e['episode'] for e in starts] == list(range(64, 64 + len(starts)))
            and len(starts) in [len(ends), min(128, len(ends) + 1)], 'Noncontiguous H1 starts')
    require([e['episode'] for e in gradients] == [e['episode'] for e in ends], 'Gradient/commit ledger mismatch')
    complete = len(ends) == 128
    if require_complete:
        require(complete, 'H1 completion requires all 128 additional episodes')
    require([e['episode'] for e in reference_events if e['event'] == 'episode_start'] == list(range(192))
            and [e['episode'] for e in reference_events if e['event'] == 'episode_end'] == list(range(192)),
            'Reference H2 has missing or duplicate episode boundaries')
    reference_starts = {e['episode']: e for e in reference_events if e['event'] == 'episode_start'}
    reference_ends = {e['episode']: e for e in reference_events if e['event'] == 'episode_end'}
    require(set(reference_starts) == set(reference_ends) == set(range(192)), 'Reference H2 is not complete')
    inner, reference_inner = defaultdict(list), defaultdict(list)
    for event in events:
        if event['event'] == 'inner':
            require(event['episode'] in {s['episode'] for s in starts}, 'H1 step lacks start')
            inner[event['episode']].append(event)
    for event in reference_events:
        if event['event'] == 'inner':
            reference_inner[event['episode']].append(event)
    checked, durations, norms = [], [], []
    start_keys = ['mode', 'K', 'q_train', 'task_file', 'parent_file', 'retain_file', 'retain_route', 'noise']
    route_keys = ['task_tokens', 'language_tokens', 'keep_tokens', 'support_forwards', 'keep_forwards']
    for start, end, gradient in zip(starts, ends, gradients):
        episode = end['episode']
        other = reference_starts[episode]
        require(all(start[k] == other[k] for k in start_keys), 'Different H1/H2 task, parent, dose or corruption metadata')
        require(start['K'] == config['episode_K'][episode] == 16, 'H1 K differs')
        steps, reference_steps = inner[episode], reference_inner[episode]
        require([s['k'] for s in steps] == list(range(1, 17))
                and [s['k'] for s in reference_steps] == list(range(1, 17)), 'Missing/duplicate inner step')
        require([s['k'] for s in reference_steps if s['exact_window']] == [15, 16], 'Reference is not recorded H2 tail')
        for step, other_step in zip(steps, reference_steps):
            # H1 uses terminal replay, so production exact_window flags stay false.
            require(step['exact_window'] is False, 'H1 unexpectedly declares an exact graph tail')
            require(all(step[k] == other_step[k] for k in route_keys), 'H1/H2 recorded support budget differs')
            require(all(step[k] >= 512 for k in ['task_tokens', 'language_tokens', 'keep_tokens'])
                    and step['keep_forwards'] > 0, 'Insufficient H1 route tokens')
            require(all(math.isfinite(step[k]) for k in ['ld', 'lr', 'step_seconds'])
                    and step['step_seconds'] > 0, 'Invalid H1 inner values')
        require(end['inner_updates'] == sum(config['episode_K'][:episode + 1]), 'Cumulative H1 budget differs')
        require(all(math.isfinite(end[k]) for k in ['query_task', 'query_language', 'query_keep', 'objective'])
                and end['objective'] >= end['query_task'], 'Invalid terminal objective')
        require(gradient['raw_all_finite'] and gradient['clipped_all_finite']
                and gradient['phi_unchanged_during_rollout'] and gradient['update_authorized_by_guard']
                and not gradient.get('error') and math.isfinite(gradient['raw_norm_cpu_fp64'])
                and 0 <= gradient['raw_norm_cpu_fp64'] <= 1e6
                and end['phi_grad_norm'] == gradient['raw_norm_cpu_fp64'], 'Invalid recorded gradient guard')
        seconds = (dt.datetime.fromisoformat(end['utc']) - dt.datetime.fromisoformat(start['utc'])).total_seconds()
        require(seconds > 0, 'Invalid episode duration')
        durations.append(seconds)
        norms.append(gradient['raw_norm_cpu_fp64'])
        checked.extend(steps)
    return {'completed_additional_episodes': len(ends), 'completed_additional_inner_steps': len(checked),
            'common_warm_inner_steps': 512, 'cumulative_inner_steps': 512 + len(checked),
            'full_commonwarm_H1_ledger_present': complete, 'recorded_task_noise_and_route_budgets_match_H2': True,
            'all_H1_exact_window_flags_false_as_expected_for_terminal_replay': True,
            'mean_episode_seconds': statistics.mean(durations), 'maximum_raw_norm': max(norms),
            'minimum_recorded_route_tokens': {k: min(s[k] for s in checked) for k in ['task_tokens', 'language_tokens', 'keep_tokens']}}


def review(observation, reference, require_complete=False):
    current, other = load_archive(observation, H1), load_archive(reference, H2)
    result = check_rows(current['config'], current['events'], current['gradients'],
                        other['config'], other['events'], require_complete)
    if require_complete:
        for bundle, run in [(current, H1), (other, H2)]:
            files = [bundle['run'] / name for name in ['result.json', 'observed_training_result.json']]
            files.append(bundle['folder'] / 'snapshot/records/jobs' / (Path(run).name + '.json'))
            for file in files:
                require(str(file.relative_to(ROOT)) in bundle['manifest'], 'Unmanifested terminal evidence')
            endpoint, sidecar, job = map(read, files)
            require(endpoint['status'] == 'PASS' and endpoint['episodes'] == 192
                    and endpoint['inner_updates'] == 2560 and endpoint['horizon'] == bundle['config']['horizon'], 'Incomplete endpoint')
            require(job['status'] == 'PASS' and sidecar['status'] == 'COMPLETE_REQUIRES_REVIEW'
                    and sidecar['method_validated'] is False and sidecar['downstream_promotion_allowed'] is False
                    and sidecar['formal_data_used'] is False, 'Missing completed wrapper/sidecar')
            if run == H1:
                require(sidecar['start_episode'] == 64 and sidecar['executed_inner_updates'] == 2048, 'H1 extra budget differs')
            bundle['files'].extend(files)
    return {'status': 'RECORDED_COMMONWARM_H1_LEDGER_CHECKS_PASS_ONLY',
            'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'source': str(Path(__file__).relative_to(ROOT)), 'source_sha256': sha(__file__),
            'observation': str(observation), 'reference': str(reference), 'require_complete': require_complete,
            'source_files_rehashed': len(current['manifest']) + len(other['manifest']),
            'input_sha256': {str(p.relative_to(ROOT)): sha(p) for b in [current, other] for p in b['files']},
            **result, 'method_validated': False, 'downstream_promotion_allowed': False,
            'limitations': ['Recorded metadata and token counts are compared; batches, graphs and derivatives are not recomputed.',
                            'H1 terminal replay is inferred from pinned code/config; false exact_window flags alone do not establish derivative correctness.',
                            'Common phi and optimizer tensor bytes require complete artifact audit and saved-gradient Adam replay.',
                            'Live snapshot files may reflect adjacent instants; incomplete commits require a later scheduled archive.',
                            'No scientific review receipt or method-efficacy conclusion is created.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--reference', type=Path, default=Path('records/observed_blocking_core_monitor_20261101_070654_utc'))
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    require(args.out.resolve().is_relative_to(ROOT / 'records')
            and not args.out.resolve().is_relative_to(args.observation.resolve())
            and not args.out.resolve().is_relative_to(args.reference.resolve()), 'Keep review outside preserved observations')
    result = review(args.observation, args.reference, args.require_complete)
    with args.out.open('x') as f:
        f.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: result[k] for k in ['status', 'completed_additional_episodes',
          'completed_additional_inner_steps', 'cumulative_inner_steps', 'full_commonwarm_H1_ledger_present']}))


if __name__ == '__main__':
    main()
