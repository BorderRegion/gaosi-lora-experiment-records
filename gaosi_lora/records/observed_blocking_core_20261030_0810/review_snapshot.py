"""Review an existing local scheduled snapshot, optionally sync only the review.

Never polls, starts or modifies remote training. Tensor finiteness is reported
from the scheduled CPU checkpoint inspection; the local reviewer checks hashes
and ledgers, not derivatives or full tensor contents.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
RUN = 'phi_mix_O1_L_H2_core_s17_observed_blocking_v8'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def review_snapshot(observation):
    state = read_json(RECORD / 'observer_state.json')
    proc = Path('/proc') / str(state['pid'])
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[19] == state['proc_start_ticks'] == '59350385'
    assert str(RECORD / 'observe.py').encode() in (proc / 'cmdline').read_bytes()
    known = [row for row in read_rows(RECORD / 'observations.jsonl')
             if row['observation'] == observation]
    assert len(known) == 1
    observed = known[0]
    folder = ROOT / observation
    assert folder.parent == ROOT / 'records'
    assert digest(Path(str(folder) + '.tar.gz')) == observed['archive_sha256']
    manifest = read_json(folder / 'archive_source_sha256.json')
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(folder.resolve())
        assert digest(path) == expected, name
    result = read_json(folder / 'result.json')
    assert not observed['alerts']
    assert result['job']['status'] == observed['job_status'] == 'RUNNING'
    assert result['identity']['pid'] == 650716
    assert result['identity']['start_ticks'] == '62167024'
    assert result['identity']['matches']
    assert result['identity']['launch_blocking_environment'] == ['MUSA_LAUNCH_BLOCKING=1']
    assert not result['gradient_guard_errors']
    run = folder / 'snapshot/runs' / RUN
    config = read_json(run / 'config.json')
    assert config['episode_K'] == [8] * 64 + [16] * 128
    gradients = read_rows(run / 'raw_gradients.jsonl')
    events = read_rows(run / 'events.jsonl')
    ends = [row for row in events if row['event'] == 'episode_end']
    starts = {row['episode']: row for row in events if row['event'] == 'episode_start'}
    count = result['committed_episodes']
    assert [row['episode'] for row in gradients] == list(range(count))
    assert [row['episode'] for row in ends] == list(range(count))
    available_gradients = 0
    for row, end in zip(gradients, ends):
        assert all(row[key] for key in ['raw_all_finite', 'phi_unchanged_during_rollout',
                                       'clipped_all_finite', 'update_authorized_by_guard'])
        assert not row.get('error')
        assert math.isfinite(row['raw_norm_cpu_fp64'])
        assert row['raw_norm_cpu_fp64'] == end['phi_grad_norm']
        assert end['inner_updates'] == sum(config['episode_K'][:end['episode'] + 1])
        inner = [x for x in events if x['event'] == 'inner' and x['episode'] == row['episode']]
        assert [x['k'] for x in inner] == list(range(1, config['episode_K'][row['episode']] + 1))
        path = run / f"gradient_episode_{row['episode']:03d}.pt"
        if path.exists():
            assert digest(path) == row['artifact_sha256']
            available_gradients += 1
    checkpoint = result['checkpoint']
    assert all(checkpoint[key] for key in ['phi_finite', 'optimizer_finite', 'config_equal'])
    assert checkpoint['next_episode'] == count
    assert checkpoint['inner_updates'] == result['committed_inner_updates'] == sum(config['episode_K'][:count])
    assert digest(run / 'latest.pt') == checkpoint['copied_artifact_sha256']
    durations = [(dt.datetime.fromisoformat(row['utc'])
                  - dt.datetime.fromisoformat(starts[row['episode']]['utc'])).total_seconds()
                 for row in ends if row['episode'] >= 64]
    mean = sum(durations) / len(durations) if durations else None
    return {'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'previous_goal_turn': 'verified_wait_and_new_training_evidence',
            'current_goal_turn': 'verified_wait_and_new_training_evidence',
            'reviewer_source': str(Path(__file__).relative_to(ROOT)),
            'reviewer_source_sha256': digest(Path(__file__)),
            'observer_live': True, 'observer_pid': state['pid'],
            'observer_start_ticks': fields[19], 'observation': observed,
            'archive_independently_verified': True, 'independently_rehashed_files': len(manifest),
            'committed_episodes': count, 'committed_inner_updates': checkpoint['inner_updates'],
            'current_L_inner_update_fraction': checkpoint['inner_updates'] / 2560,
            'all_raw_gradient_ledger_rows_checked': count,
            'all_raw_gradients_recorded_finite': True,
            'available_gradient_artifact_hashes_checked': available_gradients,
            'max_raw_norm': max(row['raw_norm_cpu_fp64'] for row in gradients),
            'guard_errors': result['gradient_guard_errors'], 'checkpoint': checkpoint,
            'production_identity': result['identity'],
            'K16_timing': {'completed_episodes': len(durations), 'mean_seconds_per_episode': mean,
                           'remaining_episodes': 192 - count,
                           'rough_remaining_hours_at_same_mean': (192 - count) * mean / 3600 if mean else None,
                           'limitation': 'Timing varies by episode mode; not a full protocol ETA.'},
            'next_due_at_utc': state['due_at_utc'], 'method_validated': False,
            'downstream_promotion_allowed': False, 'production_modified_or_restarted': False,
            'additional_training_telemetry_polled': False,
            'limitations': ['Checkpoint tensor finiteness comes from the scheduled CPU inspection.',
                            'Local checks verify hashes and ledgers, not H2 derivatives.',
                            'Active files may reflect adjacent moments; this is not an atomic run snapshot.'],
            'full_goal': 'incomplete; current L and subsequent full protocol execution/review remain'}


def sync_review(path):
    payload = {'path': str(path.relative_to(ROOT)), 'sha256': digest(path),
               'data': base64.b64encode(path.read_bytes()).decode()}
    code = """from pathlib import Path
import base64,hashlib,json,sys
data=json.load(sys.stdin)
target=Path('/root/autodl-tmp/gaosi_lora')/data['path']
content=base64.b64decode(data['data'])
assert hashlib.sha256(content).hexdigest()==data['sha256']
with target.open('xb') as handle:handle.write(content)
assert hashlib.sha256(target.read_bytes()).hexdigest()==data['sha256']
print(json.dumps({'status':'REVIEW_SYNCED_VERIFIED','path':data['path'],'sha256':data['sha256']}))
"""
    ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
           '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
    reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(code)],
                           input=json.dumps(payload).encode(), capture_output=True, timeout=60)
    if reply.returncode:
        raise RuntimeError(reply.stderr.decode(errors='replace'))
    receipt = json.loads(reply.stdout)
    assert receipt['sha256'] == payload['sha256']
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', required=True)
    parser.add_argument('--review-path', type=Path, required=True)
    parser.add_argument('--sync', action='store_true')
    args = parser.parse_args()
    path = (ROOT / args.review_path).resolve()
    assert path.parent == ROOT / 'records' and not path.exists()
    review = review_snapshot(args.observation)
    with path.open('x') as handle:
        handle.write(json.dumps(review, indent=2, allow_nan=False) + '\n')
    if args.sync:
        print(json.dumps(sync_review(path)))
    print(json.dumps({key: review[key] for key in ['committed_episodes', 'committed_inner_updates',
                                                  'independently_rehashed_files', 'K16_timing',
                                                  'next_due_at_utc']}))


if __name__ == '__main__':
    main()
