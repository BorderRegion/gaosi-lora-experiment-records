"""Review the preserved failed postprocessor and first H1 endpoint; no training."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OBS = ROOT / 'records/horizon_effect_monitor_20261106_122545_utc'
PAIR = ROOT / 'records/horizon_effect_comparison_20261103_1005'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    with path.open('x') as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False) + '\n')


def main():
    review_path = OBS / 'review_with_alerts.json'
    assessment_path = HERE / 'first_h1_endpoint_assessment.json'
    assert not review_path.exists() and not assessment_path.exists()
    assert not (OBS / 'review.json').exists(), 'Keep the failed v1 sampling outcome visible'
    archive = Path(str(OBS) + '.tar.gz')
    assert sha(archive) == '701c6cbf344dafd5cbc89d6716f95f274ae2e5922e2b1c978dc38b56a4a7964e'
    manifest_path = OBS / 'archive_source_sha256.json'
    manifest = read(manifest_path)
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(OBS) and sha(path) == expected, name
    with tarfile.open(archive, 'r:gz') as handle:
        names = []
        for member in handle.getmembers():
            target = (ROOT / member.name).resolve()
            assert member.isfile() and target.is_relative_to(OBS)
            assert handle.extractfile(member).read() == target.read_bytes()
            names.append(member.name)
        assert len(names) == len(set(names))
        assert set(names) == set(manifest) | {str(manifest_path.relative_to(ROOT))}
    result = read(OBS / 'result.json')
    anchor = ROOT / result['anchor']
    assert result['anchor_result_sha256'] == sha(anchor / 'result.json')
    assert result['gpu_lock_inode'] == 31148027670
    expected_alert = 'phi_horizon_effect_comparison_s17_v1: unsuccessful or incomplete endpoint'
    assert result['alerts'] == [expected_alert]
    snapshot = OBS / 'snapshot'
    failed_job = snapshot / 'records/jobs/phi_horizon_effect_comparison_s17_v1.json'
    failed_log = snapshot / 'records/logs/phi_horizon_effect_comparison_s17_v1.log'
    assert read(failed_job) == result['comparison_controller']
    assert read(failed_job)['status'] == 'FAIL' and read(failed_job)['exit_code'] == 1
    assert sha(failed_job) == 'cc90e2361c9cb7e9edae60a9d0e9d5727a10c7db9f524748471ce2381633fae6'
    assert sha(failed_log) == '0358e94ab9f9fdbdec2d2b48f2ece088842da6f495277a5d1412f0ad54227232'
    assert 'ValueError: Parent/noise differs' in failed_log.read_text()
    evidence = [manifest_path, OBS / 'result.json', archive, failed_job, failed_log,
                HERE / 'preparation.json', HERE / 'remote_validation.json',
                HERE / 'launch_continuation_v2.json', HERE / 'launch_verification_v2.json',
                PAIR / 'runtime_deployment_v2.json',
                PAIR / 'phi_horizon_L_H1_2560_t0_clean_s17_v1.json',
                PAIR / 'phi_horizon_L_H1_2560_t0_clean_s17_v1_paired_review_v2.json',
                ROOT / 'records/development_completion_20261104_2358/phi_dev_L_2560_t0_clean_s17.json',
                Path(__file__)]
    prep, validation, launch = [read(HERE / name) for name in
        ['preparation.json', 'remote_validation.json', 'launch_verification_v2.json']]
    assert prep['failed_predecessor'] == read(failed_job)
    assert prep['actual_different_noise_fields'] == ['boundary_seconds']
    assert validation['status'] == 'REAL_CPU_H1_H2_PAIR_AND_STRICT_GATE_VALIDATION_PASS'
    assert validation['all64_per_step_tokens_and_terminal_saved_stream_states_equal']
    assert validation['pair_review_sha256'] == sha(evidence[-3])
    assert launch['status'] == 'CONTINUATION_V2_LIVE_WITH_NEXT_DECLARED_H1_QUEUED_ORIGINAL_STATE_UNCHANGED'
    assert launch['first_completed_H1_not_retried']
    assert launch['original15_identity_counts'] == {'RUNNING': 14, 'PASS': 1}
    assert launch['device_lock_inode'] == 31148027670
    assert launch['numerical_source_files_sha256_verified'] == 25
    h1, h2, pair = [read(path) for path in [evidence[-4], evidence[-2], evidence[-3]]]
    for audit in [h1, h2]:
        assert audit['status'] == 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
        assert audit['job_status'] == 'PASS' and audit['completed_recovery_steps'] == 64
        assert audit['checkpoint_parameters_and_inner_state_finite']
        assert audit['q_apply'] == 0.0 and audit['source_files_verified'] == 25
        assert audit['clean'] == audit['initial']
        assert audit['clean']['task'] == audit['clean']['keep']
        assert audit['final']['task'] == audit['final']['keep']
    assert h1['clean'] == h2['clean'] and h1['initial'] == h2['initial']
    assert pair['dose'] == 'clean' and pair['group'] == 'known'
    assert pair['curves'][-1]['step'] == 64
    assert pair['curves'][-1]['H1'] == h1['final']
    assert pair['curves'][-1]['H2'] == h2['final']
    assert len(h1['prediction_reviews']) == 12
    assert sum(row['rows'] for row in h1['prediction_reviews']) == 6144
    evidence_sha = {str(p.relative_to(ROOT)): sha(p) for p in evidence}
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    review = {
        'status': 'PRESERVED_ALERT_REVIEWED_POSTPROCESSOR_TIMER_MISMATCH_WITH_VALIDATED_V2_CONTINUATION',
        'reviewed_at_utc': now, 'observation': str(OBS.relative_to(ROOT)),
        'archive_sha256': sha(archive), 'files_independently_rehashed': len(manifest),
        'archive_members_compared_byte_for_byte': len(names),
        'alerts': result['alerts'], 'alerts_removed_or_original_review_forged': False,
        'pass_jobs_at_original_observation': result['pass_jobs'],
        'running_jobs_at_original_observation': result['running_jobs'],
        'failed_predecessor': result['comparison_controller'],
        'root_cause': prep['diagnosis'], 'real_cpu_validation': validation['status'],
        'subsequent_continuation_identity': launch,
        'evidence_sha256': evidence_sha, 'numerical_recovery_repeated': False,
        'production_numerical_sources_modified': False, 'original_controllers_restarted': False,
        'method_validated': False, 'full_goal_complete': False,
        'limitations': ['The archived observation remains an actual FAIL alert. Later validation and launch do not change its historical status.',
                        'Launch readback is a later identity check, not a new scheduled training progress sample.']}
    assessment = {
        'status': 'FIRST_CLEAN_H1_H2_ENDPOINT_DESCRIPTIVE_COMPARISON_NO_EFFICACY_PROMOTION',
        'recorded_at_utc': now, 'H1': h1['final'], 'H2': h2['final'],
        'clean_parent': h1['clean'], 'recovery_steps': 64,
        'H1_minus_H2': {
            'candidate_correct_out_of_512': round((h1['final']['task']['candidate_accuracy'] - h2['final']['task']['candidate_accuracy']) * 512),
            'strict_correct_out_of_512': round((h1['final']['task']['strict_EM'] - h2['final']['task']['strict_EM']) * 512),
            'task_nll': h1['final']['task']['nll'] - h2['final']['task']['nll'],
            'language_nll': h1['final']['language']['nll'] - h2['final']['language']['nll']},
        'H1_prediction_files_audited': 12, 'H1_saved_prediction_rows_reaggregated': 6144,
        'H1_wrapper_seconds_including_lock_wait': validation['measured_H1_wrapper_seconds_including_lock_wait'],
        'H1_last_event_process_seconds': validation['measured_H1_last_event_process_seconds'],
        'task_and_keep_are_same_512_queries': True, 'injected_noise': 0.0,
        'interpretation': 'One extra H1 candidate answer; equal strict answers; H1 task NLL slightly higher and language NLL lower. Mixed single-cell differences do not establish a horizon or method advantage.',
        'evidence_sha256': evidence_sha,
        'method_validated': False, 'scientific_promotion_allowed': False, 'full_goal_complete': False,
        'limitations': ['Single task, dose and seed; no noise recovery claim from this zero-noise cell.',
                        'CPU saved-tensor audit and saved-prediction aggregation only; no independent forward, tokenizer, derivative or per-token language recomputation.',
                        'Wrapper wall includes lock waiting; it is not independent GPU cost.']}
    write(review_path, review)
    write(assessment_path, assessment)
    spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for path in [Path(__file__), review_path, assessment_path]:
        print(json.dumps(sync.sync_preserved(path)))
    print(json.dumps({'status': review['status'], 'alerts_preserved': review['alerts'],
                      'files_rehashed': len(manifest), 'H1_minus_H2': assessment['H1_minus_H2']}))


if __name__ == '__main__':
    main()
