"""Compare preserved L/X input and token ledgers, without evaluating efficacy."""
import datetime as dt
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


root = Path(__file__).resolve().parents[2]
evidence = {str(Path(__file__).relative_to(root)): sha(__file__)}
archives = {'L': 'records/observed_blocking_core_monitor_20261101_070654_utc',
            'X': 'records/pipeline_monitor_20261103_162058_utc'}
inputs = {}
for arch, folder in archives.items():
    manifest_file = root / folder / 'archive_source_sha256.json'
    manifest = json.loads(manifest_file.read_text())
    for name, expected in manifest.items():
        path = (root / name).resolve()
        assert path.is_relative_to(root / folder) and sha(path) == expected
    evidence[str(manifest_file.relative_to(root))] = sha(manifest_file)
    run = root / folder / 'snapshot' / f'runs/phi_mix_O1_{arch}_H2_core_s17_observed_blocking_v8'
    config_file, events_file = run / 'config.json', run / 'events.jsonl'
    for file in [config_file, events_file]:
        assert str(file.relative_to(root)) in manifest
        evidence[str(file.relative_to(root))] = sha(file)
    inputs[arch] = (json.loads(config_file.read_text()),
                    [json.loads(s) for s in events_file.read_text().splitlines()])
lc, le = inputs['L']
xc, xe = inputs['X']
differences = {k: [lc.get(k), xc.get(k)] for k in lc.keys() | xc.keys()
               if lc.get(k) != xc.get(k)}
assert set(differences) == {'out', 'architecture'}
starts = {arch: [e for e in events if e['event'] == 'episode_start']
          for arch, (_, events) in inputs.items()}
inners = {arch: [e for e in events if e['event'] == 'inner']
          for arch, (_, events) in inputs.items()}
assert len(starts['L']) == len(starts['X']) == 192
assert len(inners['L']) == len(inners['X']) == 2560
for left, right in zip(starts['L'], starts['X']):
    for key in ['episode', 'mode', 'K', 'q_train', 'task_file', 'parent_file',
                'retain_file', 'retain_route']:
        assert left[key] == right[key]
    assert {k: v for k, v in left['noise'].items() if k != 'boundary_seconds'} == {
        k: v for k, v in right['noise'].items() if k != 'boundary_seconds'}
for left, right in zip(inners['L'], inners['X']):
    for key in ['episode', 'k', 'task_tokens', 'language_tokens', 'keep_tokens',
                'support_forwards', 'keep_forwards', 'exact_window']:
        assert left[key] == right[key]
output = {'status': 'RECORDED_L_X_CORE_INPUT_AND_ROUTE_BUDGETS_MATCH',
          'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
          'config_differences': differences, 'matched_episodes': 192, 'matched_inner_steps': 2560,
          'noise_metadata_matches_except_measured_boundary_seconds': True,
          'evidence_sha256': evidence, 'method_validated': False,
          'limitations': ['Recorded input and token metadata match; no model derivative is recomputed.',
                          'This does not compare recovery performance or establish convergence.']}
with (Path(__file__).parent / 'L_X_pairing_review.json').open('x') as handle:
    handle.write(json.dumps(output, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: v for k, v in output.items() if k != 'evidence_sha256'}))
