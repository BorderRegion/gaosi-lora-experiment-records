"""Prepare additive monitoring for the distinct validated continuation controller."""
import ast
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'records/horizon_effect_comparison_20261103_1005'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    original = HERE / 'collect_runtime_v1.py'
    sample = HERE / 'sample_runtime_v1.py'
    assert sha(original) == '26bc221f710dda22304a5292f8a9ce55ef0a589f9fb5a2e04bc568b1c21d5e11'
    assert sha(sample) == 'cc1f7046944ce49a27bf7e6dacb82c7c428171dca35a7478b01746e5891101e2'
    text = original.read_text()
    text = text.replace("'comparison.json'", "'comparison_v2.json'")
    text = text.replace("CONTROLLER = 'phi_horizon_effect_comparison_s17_v1'", "CONTROLLER = 'phi_horizon_effect_comparison_s17_noise_timing_v2'")
    text = text.replace("    launch = json.loads((HERE / 'launch_20261106_0800.json').read_text())", "    repair = Path('records/horizon_noise_timing_repair_20261106_1230')\n    launch = json.loads((repair / 'launch_continuation_v2.json').read_text())\n    predecessor_path = Path('records/jobs/phi_horizon_effect_comparison_s17_v1.json')\n    assert sha(predecessor_path) == launch['predecessor_job_sha256']\n    predecessor = json.loads(predecessor_path.read_text())\n    assert predecessor == launch['predecessor_preserved'] and predecessor['status'] == 'FAIL'\n    files.update([predecessor_path, Path('records/logs/phi_horizon_effect_comparison_s17_v1.log'),\n                  repair / 'launch_continuation_v2.json', repair / 'remote_validation.json',\n                  repair / 'preparation.json', HERE / 'noise_metadata_v2.py'])")
    text = text.replace("HERE / 'compare.py'", "HERE / 'compare_noise_timing_v2.py'")
    text = text.replace("'phi_horizon_*_paired_review.json'", "'phi_horizon_*_paired_review_v2.json'")
    text = text.replace("        'comparison_controller': controller, 'comparison_controller_identity': controller_identity,", "        'comparison_controller': controller, 'comparison_controller_identity': controller_identity,\n        'acknowledged_predecessor_failure': predecessor,\n        'predecessor_failure_review': str(repair / 'remote_validation.json'),")
    ast.parse(text)
    collector = HERE / 'collect_runtime_v2.py'
    with collector.open('x') as handle:
        handle.write(text)
    fetch = sample.read_text().replace('collect_runtime_v1.py', 'collect_runtime_v2.py').replace('runtime_deployment_v1.json', 'runtime_deployment_v2.json')
    ast.parse(fetch)
    sampler = HERE / 'sample_runtime_v2.py'
    with sampler.open('x') as handle:
        handle.write(fetch)
    record = {'status': 'ADDITIVE_CONTINUATION_V2_MONITOR_PREPARED_OLD_FAILURE_RETAINED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'files_sha256': {p.name: sha(p) for p in [collector, sampler]},
              'predecessor_sources_sha256': {p.name: sha(p) for p in [original, sample]},
              'controller': 'phi_horizon_effect_comparison_s17_noise_timing_v2',
              'predecessor_FAILURE_requires_exact_preserved_job_sha256': True,
              'same_scheduled_cadence': True, 'original_observer_modified': False,
              'training_modified_or_restarted': False, 'method_validated': False,
              'full_goal_complete': False}
    manifest = HERE / 'runtime_deployment_v2.json'
    with manifest.open('x') as handle:
        handle.write(json.dumps(record, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location('sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [collector, sampler, manifest, Path(__file__)]:
        print(json.dumps(module.sync_preserved(path.resolve())))


if __name__ == '__main__':
    main()
