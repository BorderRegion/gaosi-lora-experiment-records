"""Preserve additive postprocessing fixes; no numerical sources or existing files edited."""
import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'records/horizon_effect_comparison_20261103_1005'
RECORD = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write_new(path, text):
    ast.parse(text)
    with path.open('x') as handle:
        handle.write(text)


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def main():
    assert sha(HERE / 'compare.py') == '0db6c3d8b589957f99d3ae21fb08c2504d199c5838f63124ffa2f56221b9d775'
    assert sha(HERE / 'audit_horizon_v1.py') == 'd375dfe293eebcf98059f8f7e47a28d319a3fcb107eec0399bca761bbc3a8a7f'
    sys.path.insert(0, str(HERE))
    from noise_metadata_v2 import noise_pair
    observation = ROOT / 'records/horizon_effect_monitor_20261106_122545_utc'
    result = read(observation / 'result.json')
    assert result['comparison_controller']['status'] == 'FAIL' and result['pass_jobs'] == 1
    h1path = observation / 'snapshot/runs/phi_horizon_L_H1_2560_t0_clean_s17_v1/result.json'
    h2path = ROOT / 'records/development_completion_20261104_2358/phi_dev_L_2560_t0_clean_s17_evidence/snapshot/runs/phi_dev_L_2560_t0_clean_s17/result.json'
    h1, h2 = read(h1path), read(h2path)
    assert h1['base_digest'] == h2['base_digest'] and h1['initial'] == h2['initial'] and h1['clean'] == h2['clean']
    diagnosis = noise_pair(h1['noise'], h2['noise'])
    rejected = []
    for key in h1['noise']:
        if key == 'boundary_seconds':
            continue
        changed = copy.deepcopy(h1['noise'])
        changed[key] = {'deliberate_invalid_mutation': True}
        try:
            noise_pair(changed, h2['noise'])
        except AssertionError:
            rejected.append(key)
        else:
            raise AssertionError(key)
    for label, changed in [
        ('missing_clock', {k:v for k,v in h1['noise'].items() if k != 'boundary_seconds'}),
        ('nonfinite_clock', dict(h1['noise'], boundary_seconds=float('nan'))),
        ('negative_clock', dict(h1['noise'], boundary_seconds=-1)),
        ('bool_clock', dict(h1['noise'], boundary_seconds=True))]:
        try:
            noise_pair(changed, h2['noise'])
        except AssertionError:
            rejected.append(label)
        else:
            raise AssertionError(label)
    assert len(rejected) == 13
    comparison = (HERE / 'compare.py').read_text()
    comparison = replace_once(comparison, 'import argparse\n', 'import argparse\nfrom noise_metadata_v2 import noise_pair\n')
    comparison = replace_once(comparison,
        "    require(r2['base_digest'] == r1['base_digest'] and r2['noise'] == r1['noise'], 'Parent/noise differs')",
        "    require(r2['base_digest'] == r1['base_digest'], 'Parent differs')\n    noise_timing_review = noise_pair(r2['noise'], r1['noise'])")
    comparison = replace_once(comparison, '    evidence[initial] = sha(initial)\n',
        "    evidence[initial] = sha(initial)\n    evidence[str(HERE / 'noise_metadata_v2.py')] = sha(HERE / 'noise_metadata_v2.py')\n    evidence[str(Path(__file__).resolve())] = sha(Path(__file__).resolve())\n")
    comparison = replace_once(comparison, "            'initial_adapter_tensor_sha256': tensor_digest(initial_state['adapter']),",
        "            'noise_timing_review': noise_timing_review,\n            'initial_adapter_tensor_sha256': tensor_digest(initial_state['adapter']),")
    comparison = comparison.replace("'_paired_review.json'", "'_paired_review_v2.json'")
    comparison = comparison.replace("'comparison.json'", "'comparison_v2.json'")
    comparison = replace_once(comparison,
        "                              'This supplement never alters the existing architecture or formal phi selection.'],",
        "                              'This supplement never alters the existing architecture or formal phi selection.',\n                              'V2 explicitly excludes only the boundary_seconds wall-clock timer from exact noise metadata matching; all other fields, streams, tokens and initial/clean metrics remain strict.'],")
    comparepath = HERE / 'compare_noise_timing_v2.py'
    write_new(comparepath, comparison)

    audit = (HERE / 'audit_horizon_v1.py').read_text()
    audit = replace_once(audit, 'import argparse\n', 'import argparse\nfrom noise_metadata_v2 import noise_pair\n')
    audit = replace_once(audit,
        "    assert result['noise'] == reference['noise'] and result['base_digest'] == reference['base_digest']",
        "    assert result['base_digest'] == reference['base_digest']\n    paired_noise_review = noise_pair(result['noise'], reference['noise'])")
    audit = replace_once(audit,
        "    assert finite(initial['adapter']) and initial['corruption'] == result['noise']",
        "    assert finite(initial['adapter'])\n    shared_initial_noise_review = noise_pair(initial['corruption'], result['noise'])")
    audit = replace_once(audit, "    evidence.add(Path(__file__).resolve())\n",
        "    evidence.add(Path(__file__).resolve())\n    evidence.add(Path(__file__).resolve().with_name('noise_metadata_v2.py'))\n")
    audit = replace_once(audit, "        'mean_recovery_step_seconds': statistics.mean(e['step_seconds'] for e in updates),",
        "        'mean_recovery_step_seconds': statistics.mean(e['step_seconds'] for e in updates),\n        'noise_timing_reviews': {'paired_results': paired_noise_review, 'shared_initial': shared_initial_noise_review},")
    auditpath = HERE / 'audit_horizon_v2.py'
    write_new(auditpath, audit)
    fetch = (HERE / 'fetch_horizon_audit_v1.py').read_text()
    fetchpath = HERE / 'fetch_horizon_audit_v2.py'
    write_new(fetchpath, fetch)
    evidence_paths = [h1path, h2path, HERE / 'compare.py', HERE / 'audit_horizon_v1.py',
                      HERE / 'noise_metadata_v2.py', comparepath, auditpath, fetchpath,
                      observation / 'result.json', Path(__file__)]
    record = {
        'status': 'V2_POSTPROCESSING_NOISE_TIMER_CORRECTION_PREPARED_AND_JSON_MUTATION_CHECKED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'failed_predecessor': result['comparison_controller'],
        'completed_numerical_job': result['jobs'][0]['job'],
        'diagnosis': diagnosis,
        'actual_different_noise_fields': ['boundary_seconds'],
        'validation': {'actual_pair_accepted': True, 'mutations_rejected': rejected},
        'correction': 'Only nonnegative finite boundary_seconds timing is excluded from exact matching. Parent, every other noise field, terminal streams, per-step tokens and initial/clean metrics remain strict. All old evidence and code retained; numerical recovery is not repeated.',
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in evidence_paths},
        'real_torch_validation_pending': True, 'continuation_started': False,
        'production_numerical_sources_modified': False, 'original_controllers_restarted': False,
        'method_validated': False, 'full_goal_complete': False}
    out = RECORD / 'preparation.json'
    with out.open('x') as handle:
        handle.write(json.dumps(record, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location('sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [HERE / 'noise_metadata_v2.py', comparepath, auditpath, fetchpath, Path(__file__), out]:
        print(json.dumps(module.sync_preserved(path.resolve())))


if __name__ == '__main__':
    main()
