"""CPU simulation of launch preflight; never creates real experiment jobs."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import start_observed_v8_pipeline as launcher
from train_guarded_meta import digest


def main():
    snapshot = ROOT / launcher.SNAPSHOT
    output = snapshot / 'launch_validation.json'
    if output.exists():
        raise ValueError('Preserve the previous launch validation')
    build = json.loads((snapshot / 'build_manifest.json').read_text())
    files = set(build['helpers_sha256']) | set(build['numerical_source_sha256'])
    for filename in build['sources']:
        files.add('tools/' + filename)
        files.add(str(launcher.SNAPSHOT / 'tools' / filename))
    files.update([str(launcher.SNAPSHOT / 'build_manifest.json'),
                  str(launcher.SNAPSHOT / 'validation.json'),
                  'configs/' + launcher.ADOPTED + '.json',
                  'records/pipeline_recompute_v7/build_manifest.json'])
    inputs = {name: (ROOT / name).read_bytes() for name in files}
    old = json.loads(inputs['records/pipeline_recompute_v7/build_manifest.json'])
    tests = []
    with tempfile.TemporaryDirectory(prefix='gaosi_v8_launch_') as temporary:
        os.chdir(temporary)
        try:
            for name, data in inputs.items():
                p = Path(name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            Path('records/jobs').mkdir(exist_ok=True)
            for job in old['jobs']:
                Path('records/jobs', job['name'] + '.json').write_text(json.dumps({'status': 'FAIL'}))
            adopted = Path('records/jobs', launcher.ADOPTED + '.json')
            adopted.write_text(json.dumps({'status': 'RUNNING'}))
            with patch.object(launcher, 'verify_review') as review:
                try:
                    launcher.preflight()
                except RuntimeError as error:
                    assert 'finish and be reviewed' in str(error)
                else:
                    raise AssertionError('Running L was allowed through preflight')
                review.assert_not_called()
            tests.append('running_L_stops_launch_before_review_or_subprocess')
            adopted.write_text(json.dumps({'status': 'PASS'}))
            with patch.object(launcher, 'verify_review', side_effect=ValueError('no review receipt')):
                try:
                    launcher.preflight()
                except ValueError as error:
                    assert str(error) == 'no review receipt'
                else:
                    raise AssertionError('Completed but unreviewed L allowed')
            tests.append('PASS_L_requires_exact_completed_evidence_review')
            with patch.object(launcher, 'verify_review', return_value={'fixture_only': True}):
                assert launcher.preflight()['jobs'] == build['jobs']
                tests.append('reviewed_clean_state_passes_preflight_without_starting')
                bad = Path('gaosi/meta.py')
                original = bad.read_bytes()
                bad.write_bytes(b'changed')
                try:
                    launcher.preflight()
                except RuntimeError as error:
                    assert 'source changed' in str(error)
                    tests.append('changed_numerical_source_rejected')
                else:
                    raise AssertionError('Changed source accepted')
                finally:
                    bad.write_bytes(original)
                for path in [Path('records/phi_core_ready.json'),
                             Path('records/jobs', build['jobs'][0]['name'] + '.json'),
                             launcher.SNAPSHOT / 'launch.json']:
                    path.write_text('{}')
                    try:
                        launcher.preflight()
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('Duplicate or downstream artifact accepted')
                    finally:
                        path.unlink()
                tests.append('old_downstream_results_existing_jobs_and_previous_launch_rejected')
                first = Path('records/jobs', old['jobs'][0]['name'] + '.json')
                first.write_text(json.dumps({'status': 'RUNNING'}))
                try:
                    launcher.preflight()
                except RuntimeError as error:
                    assert 'old controller' in str(error)
                else:
                    raise AssertionError('Old running controller ignored')
                tests.append('old_running_controller_blocks_new_pipeline')
        finally:
            os.chdir(ROOT)
    result = {'status': 'PASS', 'scope': 'CPU-only temporary launch-preflight fixture',
              'tests': tests, 'real_training_started_or_modified': False,
              'tested_source_sha256': {str(p.relative_to(ROOT)): digest(p) for p in [
                  ROOT / 'tools/start_observed_v8_pipeline.py', Path(__file__).resolve(),
                  ROOT / 'tools/guarded_pipeline.py']}}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
