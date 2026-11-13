"""Publish the agent's scoped O1 continuation judgment, bound to saved evidence."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN = 'runs/phi_mix_O1_X_specialize512_s17_observed_blocking_v8'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def preserve(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def main():
    audit = read(HERE / 'artifact_audit.json')
    replay = read(HERE / 'adam_replay.json')
    tensors = read(HERE / 'tensor_review.json')
    completed = read(HERE / 'execution_completed.json')
    verified = read(HERE / 'pre_scientific_review_verification.json')
    for group in (completed['outputs_sha256'], verified['source_pins']):
        for name, expected in group.items():
            assert sha(ROOT / name) == expected, name
    assert audit['run'] == replay['run'] == tensors['run'] == RUN
    assert audit['episodes_committed'] == replay['episodes_checked'] == tensors['episodes_checked'] == 64
    assert audit['executed_inner_updates'] == tensors['inner_steps_checked'] == 512
    assert audit['maximum_raw_norm'] < 1.0
    assert replay['all_finite'] and replay['allclose'] and replay['optimizer_states_all_equal']
    assert replay['all_starting_phi_equal'] and replay['all_optimizer_param_groups_equal']
    assert len(replay['rows']) == 64 and all(row['all_finite'] and row['allclose'] for row in replay['rows'])
    assert tensors['initial_optimizer_step'] == 192 and tensors['final_optimizer_step'] == 256
    assert tensors['latest_and_512_snapshot_phi_and_optimizer_equal']
    assert min(tensors['minimum_recorded_effective_tokens'].values()) >= 512
    assert verified['verified_evidence_counts'] == {
        str((HERE / 'artifact_audit.json').relative_to(ROOT)): 267,
        str((HERE / 'tensor_review.json').relative_to(ROOT)): 271,
        'records/horizon_uniform_numerics_20261109_2330/initial_saved_tensor_review.json': 26,
    }
    limitations = [
        'This judgment permits the next existing protocol stages only; O1 effectiveness and superiority to Adam have not been evaluated after specialization.',
        'The completed seed17 development grid does not establish overall phi superiority. The language and isolated rule-retention signals are descriptive development observations.',
        'CPU Adam replay consumes saved gradients and resets from each saved starting state. It does not independently recompute MUSA derivatives or an accumulated CPU trajectory.',
        'The production H2 derivative covers the final two steps with the earlier boundary fixed. The original mixed-path horizon failure remains a failure.',
        'The uniform-path H1/H2 gate passed for a newly declared common numerical trajectory. Full H4/H8 comparisons remain pending and cannot validate the production trajectory retroactively.',
        'The frozen schedule couples known/new mode to parent parity; its route counts cannot establish a factorial causal effect.',
        'Finite tensors, parameter movement and normal gradient scale do not establish efficacy or convergence. Old v7 failures and unresolved backend limitations remain recorded.',
        'Preserve the original device lock, blocking environment, source/input pins, gradient guards, budgets and stopping rules. Each later completed meta run requires its own artifact audit, optimizer replay and scientific judgment.',
    ]
    scientific_files = [HERE / name for name in [
        'artifact_audit.json', 'adam_replay.json', 'tensor_review.json',
        'execution_completed.json', 'pre_scientific_review_verification.json',
        'run_cpu_review.py', 'publish_review.py',
    ]] + [ROOT / name for name in [
        'records/organization_completion_preparation_20261110_0603/inspect_completed_specialization.py',
        'records/horizon_uniform_numerics_20261109_2330/initial_saved_tensor_review.json',
        'records/development_signal_review_20261110_0742/descriptive_comparison.json',
        'records/pipeline_observed_blocking_v8/reviews/phi_mix_O1_X_H2_core_s17_observed_blocking_v8.json',
    ]]
    assessment = {
        'status': 'O1_SPECIALIZATION_REVIEWED_FOR_SCOPED_PROTOCOL_CONTINUATION',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'reviewer': 'Codex /root', 'run': RUN,
        'judgment': 'Complete guarded execution, saved finite gradients, independently replayed optimizer updates, inherited warm optimizer state and the complete 512-step input ledger support continuing the existing organization protocol under its original guards.',
        'observations': {
            'committed_episodes': 64, 'specialization_inner_steps': 512,
            'core_plus_specialization_inner_steps': 3072,
            'maximum_raw_gradient_norm': audit['maximum_raw_norm'],
            'maximum_cpu_phi_absolute_error': replay['maximum_phi_absolute_error'],
            'optimizer_state_replay_bitwise_equal': True,
            'optimizer_step_initial': 192, 'optimizer_step_final': 256,
            'phi_l2_change_from_core': tensors['phi_change_l2_from_common_core'],
        },
        'evidence_sha256': {str(path.relative_to(ROOT)): sha(path) for path in scientific_files},
        'limitations': limitations, 'comparative_effectiveness_established': False,
        'derivatives_independently_recomputed': False, 'method_validated': False,
        'training_modified_or_restarted': False, 'full_goal_complete': False,
    }
    assessment_path = HERE / 'scientific_assessment.json'
    preserve(assessment_path, assessment)
    scientific_files.append(assessment_path)
    receipt = {
        'status': 'ALLOW_NEXT_PROTOCOL_STAGE_WITH_LIMITATIONS',
        'run': RUN, 'reviewer': assessment['reviewer'],
        'reviewed_at_utc': assessment['reviewed_at_utc'],
        'rationale': assessment['judgment'],
        'artifact_audit': str((HERE / 'artifact_audit.json').relative_to(ROOT)),
        'artifact_audit_sha256': sha(HERE / 'artifact_audit.json'),
        'scientific_evidence_sha256': {str(path.relative_to(ROOT)): sha(path) for path in scientific_files},
        'limitations': limitations,
        'scope': 'Continue the existing organization controller into O2 and remaining declared experiments with separate review gates; no method or backend validation claim.',
        'method_validated': False, 'full_goal_complete': False,
    }
    # Prepare and verify every dependency before exposing the receipt to the controller.
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    for path in scientific_files:
        transport.sync(path)
    code = '''import json,sys,os
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');os.chdir(r);sys.path.insert(0,str(r/'tools'))
from guarded_pipeline import verify_review
p=json.load(sys.stdin);target=Path(p['path']);assert not target.exists()
temp=target.with_suffix('.prepared.json');assert not temp.exists()
with temp.open('x') as f:json.dump(p['receipt'],f,indent=2);f.write('\\n')
verify_review(p['receipt']['run'],temp)
os.link(temp,target)
verify_review(p['receipt']['run'],target)
import hashlib
print(json.dumps({'status':'O1_SCIENTIFIC_CONTINUATION_RECEIPT_PUBLISHED_AND_VERIFIED','path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'training_restarted':False}))
'''
    relative = 'records/pipeline_observed_blocking_v8/reviews/' + Path(RUN).name + '.json'
    preserve(HERE / 'publication_intent.json', {'path': relative, 'receipt': receipt, 'single_submission': True})
    # A lost reply requires read-only inspection; never retry publication.
    result = json.loads(transport.remote(code, {'path': relative, 'receipt': receipt}, attempts=1, timeout=55))
    preserve(HERE / 'publication_verification.json', result)
    data = transport.remote('''import sys,json
from pathlib import Path
p=json.load(sys.stdin);sys.stdout.buffer.write((Path('/root/autodl-tmp/gaosi_lora')/p['path']).read_bytes())
''', {'path': relative}, attempts=2)
    assert hashlib.sha256(data).hexdigest() == result['sha256']
    with (ROOT / relative).open('xb') as handle:
        handle.write(data)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
