"""Check preserved H1 text prefixes against audited H2 endpoints; no training or Torch."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(path, directory):
    manifest = read(path)
    for name, expected in manifest.items():
        actual = (ROOT / name).resolve()
        assert actual.is_relative_to(directory) and sha(actual) == expected, name
    return len(manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sync', action='store_true')
    args = parser.parse_args()
    observation, out = [(ROOT / p).resolve() for p in [args.observation, args.out]]
    assert observation.parent == ROOT / 'records' and observation.name.startswith('horizon_effect_monitor_')
    assert out.is_relative_to(ROOT / 'records') and not out.exists()
    result, review = [read(observation / name) for name in ['result.json', 'review.json']]
    assert not result['alerts'] and not review['alerts']
    assert sha(Path(str(observation) + '.tar.gz')) == review['archive_sha256']
    manifestpath = observation / 'archive_source_sha256.json'
    assert verify_manifest(manifestpath, observation) == review['files_independently_rehashed']
    snapshot = observation / 'snapshot'
    declaredpath = snapshot / 'records/horizon_effect_comparison_20261103_1005/declared_pairs.json'
    declared = {p['H1']['out']: p for p in read(declaredpath)}
    sources = read(snapshot / 'records/pipeline_observed_blocking_v8/build_manifest.json')['numerical_source_sha256']
    expected_sources = {Path(name).name: value for name, value in sources.items()}
    evidence_paths = [observation / 'review.json', manifestpath, declaredpath, Path(__file__)]
    checks = []
    for job in result['jobs']:
        pair = declared[job['run']]
        config = read(snapshot / job['config'])
        assert config == pair['H1']
        assert config['initial_adapter'] == pair['H2']['out'] + '/initial.pt'
        baseline_name = Path(pair['H2']['out']).name
        baseline = ROOT / 'records/development_completion_20261104_2358' / (baseline_name + '_evidence')
        auditpath = baseline / 'audit.json'
        audit = read(auditpath)
        assert audit['job_status'] == 'PASS' and audit['checkpoint_step'] == 64
        baseline_manifest_path = baseline / 'manifest.json'
        baseline_manifest = read(baseline_manifest_path)
        verify_manifest_files = baseline_manifest['files']
        for name, expected in verify_manifest_files.items():
            path = (ROOT / name).resolve()
            assert path.is_relative_to(baseline) and sha(path) == expected, name
        assert read(baseline / 'snapshot' / pair['H2']['out'] / 'config.json') == pair['H2']
        evidence_paths.extend([auditpath, baseline_manifest_path])
        run = snapshot / job['run']
        if not (run / 'config.json').exists():
            assert job['job']['status'] == 'RUNNING' and job['identity']['matches']
            assert job['recorded_recovery_steps'] == 0 and job['evaluation_steps'] == []
            checks.append({'run': job['run'], 'state': 'NO_SAVED_RUN_CONFIG_YET',
                           'paired_prefix_available': False})
            continue
        assert read(run / 'config.json') == config
        assert read(run / 'source_sha256.json') == expected_sources
        assert read(baseline / 'snapshot' / pair['H2']['out'] / 'source_sha256.json') == expected_sources
        for name, expected in expected_sources.items():
            assert sha(run / 'source' / name) == expected
        eventpath = run / 'events.jsonl'
        raw = eventpath.read_bytes() if eventpath.exists() else b''
        partial = bool(raw and not raw.endswith(b'\n'))
        assert partial == job['trailing_partial_event_line_excluded']
        lines = raw.splitlines()[:-1] if partial else raw.splitlines()
        events = [json.loads(line) for line in lines]
        h2path = baseline / 'snapshot' / pair['H2']['out'] / 'events.jsonl'
        h2events = [json.loads(line) for line in h2path.read_text().splitlines()]
        h2steps = [e for e in h2events if e['event'] == 'recover']
        updates = [e for e in events if e['event'] == 'recover']
        assert len(updates) == job['recorded_recovery_steps'] <= 64
        assert [e['step'] for e in updates] == list(range(1, len(updates) + 1))
        evaluations = [e for e in events if e['event'] == 'evaluation']
        assert [e['step'] for e in evaluations] == job['evaluation_steps']
        assert job['evaluation_steps'] == [0, 8, 16, 32, 64][:len(evaluations)]
        clean = [e for e in events if e['event'] == 'clean_evaluation']
        assert len(clean) <= 1
        if clean:
            assert clean[0]['metrics'] == audit['clean']
        if evaluations:
            assert evaluations[0]['metrics'] == audit['initial']
        for e1, e2 in zip(updates, h2steps):
            for key in ['step', 'route_tokens', 'coverage_slot', 'coverage', 'compute_counters']:
                assert e1[key] == e2[key], (job['run'], e1['step'], key)
            assert all(v >= config['effective_tokens'] for v in e1['route_tokens'].values())
        checks.append({'run': job['run'], 'job_status': job['job']['status'],
                       'recorded_steps': len(updates), 'evaluation_steps': job['evaluation_steps'],
                       'source_files_verified': len(expected_sources),
                       'declared_initial_adapter_path_matched': True,
                       'clean_evaluation_matches_H2': bool(clean),
                       'initial_evaluation_matches_H2': bool(evaluations),
                       'per_step_token_coverage_and_recorded_compute_counters_match_H2_prefix': True,
                       'minimum_effective_tokens': job['minimum_effective_tokens'],
                       'terminal_streams_or_initial_tensor_equality_independently_audited': False})
    report = {'status': 'PRESERVED_H1_H2_PREFIX_TEXT_PAIRING_CHECKED_NO_EFFICACY_CLAIM',
              'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'observation': str(observation.relative_to(ROOT)), 'checks': checks,
              'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in evidence_paths},
              'limitations': ['Saved text prefixes only; no checkpoint, stream-state tensor or derivative audit.',
                              'Equal recorded tokens/counters/coverage do not independently prove identical sampled examples or in-memory streams.',
                              'Recorded counters omit direct QA calls and are not full-model FLOPs.',
                              'No method selection, efficacy inference or training modification.'],
              'method_validated': False, 'full_goal_complete': False,
              'training_modified_or_restarted': False}
    with out.open('x') as handle:
        handle.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    if args.sync:
        spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path in [Path(__file__), out]:
            print(json.dumps(module.sync_preserved(path.resolve())))
    print(json.dumps({'status': report['status'], 'checks': checks}))


if __name__ == '__main__':
    main()
