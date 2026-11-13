"""Record the agent's evidence-reviewed, limited H1 continuation judgment; no training restart."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
RUN = 'runs/phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8'
OBS = ROOT / 'records/pipeline_monitor_20261104_205526_utc'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    with path.open('x') as handle:
        json.dump(data, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    audit_path = RECORD / 'artifact_audit.json'
    audit = read(audit_path)
    replay = read(RECORD / 'adam_replay.json')
    tensor = read(RECORD / 'tensor_review.json')
    ledger_path = ROOT / 'records/H1_complete_inner_review_20261104_2056.json'
    ledger = read(ledger_path)
    result = read(OBS / 'snapshot' / RUN / 'result.json')
    job_path = OBS / 'snapshot/records/jobs' / (Path(RUN).name + '.json')
    job = read(job_path)
    observed = read(OBS / 'result.json')
    assert audit['run'] == RUN and audit['status'] == 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
    assert audit['start_episode'] == 64 and audit['episodes_committed'] == 128
    assert audit['executed_inner_updates'] == 2048 and audit['cumulative_inner_updates'] == 2560
    assert [r['episode'] for r in audit['episodes']] == list(range(64, 192))
    assert all(r['committed'] and 0 < r['raw_norm_cpu_fp64'] < 1 for r in audit['episodes'])
    assert audit['maximum_raw_norm'] == max(r['raw_norm_cpu_fp64'] for r in audit['episodes'])
    assert replay['audit_sha256'] == sha(audit_path) and replay['episodes_checked'] == 128
    assert replay['initial_optimizer_step'] == 64 and replay['audited_evidence_independently_rehashed'] == len(audit['evidence_sha256'])
    assert all(replay[k] for k in ['all_starting_phi_equal', 'all_optimizer_param_groups_equal',
                                  'all_finite', 'allclose', 'optimizer_states_all_equal'])
    assert [r['episode'] for r in replay['rows']] == list(range(64, 192))
    assert all(r['allclose'] and r['all_finite'] and all(v['all_equal'] for v in r['optimizer'].values()) for r in replay['rows'])
    assert tensor['episodes_checked'] == 128 and tensor['phi_parameters'] == 78850
    assert tensor['warm_phi_and_optimizer_preserved_exactly'] and tensor['first_rollout_phi_equal_common_warm']
    assert tensor['initial_optimizer_step'] == 64 and tensor['final_optimizer_step'] == 192
    assert tensor['every_parameter_tensor_has_nonzero_saved_gradient'] and tensor['latest_phi_and_optimizer_equal_2560_snapshot']
    assert ledger['require_complete'] and ledger['full_commonwarm_H1_ledger_present']
    assert ledger['recorded_task_noise_and_route_budgets_match_H2']
    assert ledger['completed_additional_inner_steps'] == 2048 and ledger['cumulative_inner_steps'] == 2560
    assert result['status'] == job['status'] == 'PASS' and job['exit_code'] == 0
    assert result['base_unchanged'] and result['episodes'] == 192 and result['inner_updates'] == 2560
    assert not observed['alerts'] and observed['pending_scientific_reviews'] == [RUN]
    final_sha = audit['evidence_sha256'][RUN + '/latest.pt']
    assert final_sha == observed['meta_runs'][0]['checkpoint']['sha256'] == 'f3154c49d0d7213829551a5447224757c4b0bd95987b3923aea92a000dd503e8'
    controllers = read(RECORD / 'controller_identity_before_review.json')
    assert len(controllers['controllers']) == 15 and controllers['gpu_lock_inode'] == 31148027670
    assert all(not d['method_validated'] for d in [audit, replay, tensor, ledger])
    evidence_paths = [audit_path, RECORD / 'adam_replay.json', RECORD / 'tensor_review.json',
        RECORD / 'inspect_saved_H1_v2.py', RECORD / 'inspection_attempt_1.json',
        RECORD / 'controller_identity_before_review.json', RECORD / 'fetch_receipt.json',
        ledger_path, ROOT / 'records/H1_ledger_review_20261103_1652/review_commonwarm_ledger.py',
        ROOT / 'tools/audit_observed_meta.py', ROOT / 'records/pipeline_observer_20261101_1143/replay_saved_adam.py',
        ROOT / 'records/pipeline_review_20261104_2055.json', OBS / 'result.json',
        OBS / 'archive_source_sha256.json', Path(str(OBS) + '.tar.gz'), job_path,
        ROOT / 'records/pipeline_observed_blocking_v8/reviews/phi_mix_O1_L_H2_core_s17_observed_blocking_v8.json',
        ROOT / 'records/pipeline_observed_blocking_v8/reviews/phi_mix_O1_X_H2_core_s17_observed_blocking_v8.json',
        ROOT / 'records/H1_H2_training_diagnostic_20261104_1310/review.json',
        ROOT / 'records/horizon_effect_comparison_20261103_1005/inputs.json', Path(__file__)]
    evidence = {str(p.relative_to(ROOT)): sha(p) for p in evidence_paths}
    # Existing saved audits bind these same input bytes; do not reclassify numerical outcomes.
    for name, expected in ledger['input_sha256'].items():
        assert sha(ROOT / name) == expected
    for p in [OBS / 'snapshot' / RUN / 'result.json', OBS / 'snapshot' / RUN / 'config.json',
              OBS / 'snapshot' / RUN / 'events.jsonl', OBS / 'snapshot' / RUN / 'raw_gradients.jsonl']:
        actual_name = str(p.relative_to(OBS / 'snapshot'))
        assert audit['evidence_sha256'][actual_name] == sha(p)
    limitations = [
        'Old v7 nonfinite and enormous gradients remain invalid and their root cause unresolved; blocking is a candidate mitigation, not a root-cause proof.',
        'H2 differentiates only its final two steps after a fixed earlier boundary; it is not a full K-step derivative.',
        'CPU tiny derivative checks and real BF16 coarse-scale differences have limited scope; saved-gradient replay does not independently recompute model derivatives.',
        'Each CPU Adam update starts at its saved state, not an accumulated independent CPU training trajectory. Phi agrees within the recorded tolerance, not exact byte equality.',
        'Common warm phi and optimizer, matched task/noise metadata and token budgets do not imply identical BF16 forward values or isolate a pure gradient-horizon causal effect.',
        'Partial training objective diagnostics were mixed and do not establish held-out efficacy, convergence, superiority to Adam, less forgetting, generalization or independent q_train benefit.',
        'Only phi seed17 has these endpoints. Frozen development comparisons and independent seeds29/43 remain required; full protocol is incomplete.',
        'Every later meta run requires its own completed artifact audit, optimizer replay and agent scientific review before continuation.']
    rationale = ('Complete common-warm H1 execution, byte-identical preserved warm checkpoint including meta-Adam, finite saved gradients, '
        '128 matched episode/token ledgers and per-update CPU Adam agreement support continuing the existing controller into planned development comparisons. '
        'This is a limited experimental continuation judgment with explicit numerical and scientific limitations; it is not method validation.')
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    assessment = {
        'status': 'COMMONWARM_H1_EVIDENCE_REVIEW_SUPPORTS_LIMITED_PROTOCOL_CONTINUATION',
        'reviewed_at_utc': timestamp, 'run': RUN,
        'previous_goal_turn': 'progress_H1_127_and_normal_observer_handoff',
        'current_goal_turn': 'progress_H1_complete_artifact_replay_tensor_ledger_and_agent_scientific_review',
        'completion': {'job_status': 'PASS', 'job_exit_code': 0,
            'finished_at_utc': job['finished_at_utc'], 'wrapper_wall_hours_excluding_shared_warm': job['wall_seconds'] / 3600,
            'additional_episodes': 128, 'additional_inner_updates': 2048,
            'shared_warm_inner_updates': 512, 'cumulative_inner_updates': 2560,
            'final_checkpoint_sha256': final_sha, 'base_unchanged': True},
        'artifact_findings': {'audited_files': len(audit['evidence_sha256']), 'maximum_raw_norm': audit['maximum_raw_norm'],
            'gradient_clipping_triggered': False, 'phi_parameters': 78850,
            'common_warm_full_checkpoint_sha256': tensor['warm_checkpoint_full_bytes_sha256'],
            'warm_phi_and_optimizer_preserved_exactly': True, 'optimizer_steps': [64, 192],
            'recorded_task_noise_and_route_budgets_match_H2': True,
            'minimum_recorded_route_tokens': ledger['minimum_recorded_route_tokens'],
            'latest_phi_and_optimizer_equal_2560_snapshot': True},
        'optimizer_findings': {k: replay[k] for k in ['episodes_checked', 'initial_optimizer_step',
            'all_starting_phi_equal', 'all_optimizer_param_groups_equal', 'all_finite', 'allclose',
            'maximum_phi_absolute_error', 'maximum_phi_relative_l2_error', 'optimizer_states_all_equal', 'comparison_tolerance']},
        'judgment': rationale, 'limitations': limitations,
        'next_stages': ['Existing core controller emits phi_core_ready only after verifying the receipt, then existing development controller proceeds.',
            'Run frozen H1/H2 endpoint comparison only after the ten existing H2 development trajectories are complete; retain common initial adapter and budgets.',
            'Continue development selection/conditional matched extension, four-organization adaptation, matched clean, formal/continual/7B/statistics and independent phi seeds29/43.'],
        'method_validated': False, 'full_protocol_complete': False, 'scientific_evidence_sha256': evidence}
    assessment_path = RECORD / 'scientific_assessment.json'
    write(assessment_path, assessment)
    evidence[str(assessment_path.relative_to(ROOT))] = sha(assessment_path)
    receipt = {'status': 'ALLOW_NEXT_PROTOCOL_STAGE_WITH_LIMITATIONS', 'run': RUN,
        'reviewer': 'Codex /root common-warm H1 completion evidence review',
        'reviewed_at_utc': timestamp, 'rationale': rationale,
        'artifact_audit': str(audit_path.relative_to(ROOT)), 'artifact_audit_sha256': sha(audit_path),
        'scientific_evidence_sha256': evidence, 'limitations': limitations,
        'method_validated': False,
        'scope': 'Allow existing core/development controllers and predeclared guarded endpoint comparisons to continue; no method/backend validation claim.'}
    receipt_path = ROOT / 'records/pipeline_observed_blocking_v8/reviews' / (Path(RUN).name + '.json')
    write(receipt_path, receipt)
    spec = importlib.util.spec_from_file_location('sync', ROOT / 'records/observed_blocking_core_20261030_0810/review_snapshot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Publish only after all evidence referenced by the manually reviewed judgment is present remotely.
    sync_paths = [Path(__file__), RECORD / 'fetch_completion_evidence.py', RECORD / 'fetch_receipt.json', ledger_path, assessment_path]
    receipts = [module.sync_review(p.resolve()) for p in sync_paths]
    receipts.append(module.sync_review(receipt_path))
    write(RECORD / 'publication_receipt.json', {'status': 'H1_EVIDENCE_AND_LIMITED_SCIENTIFIC_RECEIPT_SYNCED',
          'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'receipts': receipts,
          'training_restarted': False, 'method_validated': False, 'full_goal_complete': False})
    print(json.dumps({'status': 'H1_COMPLETE_AND_REVIEWED_FOR_LIMITED_PROTOCOL_CONTINUATION',
          'receipt': str(receipt_path.relative_to(ROOT)), 'receipt_sha256': sha(receipt_path),
          'audited_files': len(audit['evidence_sha256']), 'new_inner_steps': 2048, 'cumulative_inner_steps': 2560,
          'method_validated': False, 'full_goal_complete': False}))


if __name__ == '__main__':
    main()
