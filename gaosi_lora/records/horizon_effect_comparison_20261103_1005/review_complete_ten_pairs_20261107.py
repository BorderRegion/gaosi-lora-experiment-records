"""Recompute the frozen descriptive horizon aggregate from preserved evidence."""
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shlex
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MONITOR = ROOT / 'records/horizon_effect_monitor_20261107_185554_utc'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(metrics, clean):
    result = .5 * (metrics['task']['candidate_accuracy'] + metrics['task']['strict_EM'])
    for route in ['keep', 'language']:
        result -= max(0., metrics[route]['nll'] - clean[route]['nll'] - .1)
    assert math.isfinite(result)
    return result


def main():
    output = HERE / 'complete_ten_pairs_descriptive_assessment_20261107.json'
    assert not output.exists()
    saved = MONITOR / 'snapshot/records/horizon_effect_comparison_20261103_1005'
    source_manifest = read(MONITOR / 'archive_source_sha256.json')
    paths = [saved / n for n in ['comparison_v2.json', 'declared_pairs.json', 'policy.json', 'inputs.json']]
    evidence = {}
    for path in paths:
        assert sha(path) == source_manifest[str(path.relative_to(ROOT))]
        evidence[str(path.relative_to(ROOT))] = sha(path)
    comparison, declared, policy, inputs = [read(p) for p in paths]
    assert comparison['status'] == 'COMMON_WARM_HORIZON_DEV_COMPARISON_COMPLETE_REQUIRES_INTERPRETATION'
    assert len(comparison['pairs']) == len(declared) == 10
    assert comparison['policy'] == policy == inputs['policy']
    assert not comparison['method_validated'] and not comparison['formal_data_used']
    assert policy['data_seed'] == 17 and policy['noise_seed'] == 1729 and policy['K'] == 64
    expected_steps = [0, 8, 16, 32, 64]
    rows = []
    remote_expected = {
        str((HERE / 'comparison_v2.json').relative_to(ROOT)): sha(paths[0]),
        str((HERE / 'declared_pairs.json').relative_to(ROOT)): sha(paths[1]),
        str((HERE / 'policy.json').relative_to(ROOT)): sha(paths[2]),
        str((HERE / 'inputs.json').relative_to(ROOT)): sha(paths[3]),
    }
    for pair, config in zip(comparison['pairs'], declared):
        assert pair['group'] == config['group'] and pair['dose'] == config['dose']
        assert pair['runs'] == [config[h]['out'] for h in ['H2', 'H1']]
        assert [c['step'] for c in pair['curves']] == expected_steps
        pairpath = saved / (Path(config['H1']['out']).name + '_paired_review_v2.json')
        assert read(pairpath) == pair
        assert sha(pairpath) == source_manifest[str(pairpath.relative_to(ROOT))]
        evidence[str(pairpath.relative_to(ROOT))] = sha(pairpath)
        remote_expected[str((HERE / pairpath.name).relative_to(ROOT))] = sha(pairpath)
        audits = {}
        for horizon in ['H1', 'H2']:
            directory = HERE if horizon == 'H1' else ROOT / 'records/development_completion_20261104_2358'
            name = Path(config[horizon]['out']).name
            receiptpath = directory / ('fetch_receipt_' + name + '.json')
            receipt = read(receiptpath)
            auditpath = ROOT / receipt['audit']
            audit = audits[horizon] = read(auditpath)
            assert sha(auditpath) == receipt['audit_sha256']
            assert audit['run'] == config[horizon]['out'] and audit['job_status'] == 'PASS'
            assert audit['completed_recovery_steps'] == audit['checkpoint_step'] == 64
            assert audit['checkpoint_parameters_and_inner_state_finite'] and audit['source_files_verified'] == 25
            evidence[str(receiptpath.relative_to(ROOT))] = sha(receiptpath)
            evidence[str(auditpath.relative_to(ROOT))] = sha(auditpath)
            remote_expected[str(auditpath.relative_to(ROOT))] = sha(auditpath)
            assert pair['curves'][-1][horizon] == audit['final']
            for curve in pair['curves']:
                assert score(curve[horizon], audit['clean']) == curve[horizon + '_score']
        assert audits['H1']['clean'] == audits['H2']['clean']
        assert audits['H1']['initial'] == audits['H2']['initial']
        assert pair['noise_timing_review']['all_non_timing_fields_exactly_equal']
        assert pair['noise_timing_review']['excluded_fields'] == ['boundary_seconds']
        for curve in pair['curves']:
            assert curve['H2_minus_H1'] == curve['H2_score'] - curve['H1_score']
        end = pair['curves'][-1]
        rows.append({
            'task_index': len(rows) // 2, 'group': pair['group'], 'dose': pair['dose'],
            'H2_minus_H1_score': end['H2_minus_H1'],
            'counts': {h: {route: {
                'candidate_correct': round(end[h][route]['candidate_accuracy'] * end[h][route]['n']),
                'strict_EM_correct': round(end[h][route]['strict_EM'] * end[h][route]['n']),
                'n': end[h][route]['n']} for route in ['task', 'keep']} for h in ['H1', 'H2']},
            'H2_minus_H1_nll': {route: end['H2'][route]['nll'] - end['H1'][route]['nll']
                                for route in ['task', 'keep', 'language']},
        })
    aggregates = []
    for index, step in enumerate(expected_steps):
        means = {}
        for group in ['known', 'fact', 'rule']:
            doses = {dose: statistics.mean(pair['curves'][index]['H2_minus_H1']
                     for pair in comparison['pairs'] if pair['group'] == group and pair['dose'] == dose)
                     for dose in ['clean', 'medium']}
            means[group] = statistics.mean(doses.values())
        aggregate = {'step': step, 'group_H2_minus_H1': means,
                     'equal_group_H2_minus_H1': statistics.mean(means.values())}
        reported = comparison['aggregates'][index]
        assert aggregate['step'] == reported['step']
        assert aggregate['group_H2_minus_H1'] == reported['group_H2_minus_H1']
        assert math.isclose(aggregate['equal_group_H2_minus_H1'], reported['equal_group_H2_minus_H1'], abs_tol=1e-15)
        aggregates.append(aggregate)
    code = '''import hashlib,json,sys
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora')
expected=json.load(sys.stdin)
for name,wanted in expected.items():
 p=(root/name).resolve()
 assert p.is_relative_to(root)
 assert hashlib.sha256(p.read_bytes()).hexdigest()==wanted,name
print(json.dumps({'status':'REMOTE_COMPARISON_PAIR_AND_AUDIT_JSON_SHA256_REVERIFIED','files':len(expected)}))
'''
    ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
    reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(code)], input=json.dumps(remote_expected),
                           text=True, capture_output=True, check=True, timeout=60)
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    result = {
        'status': 'ALL_TEN_COMMON_WARM_HORIZON_PAIRS_AUDITED_AND_FROZEN_SCORE_AGGREGATES_RECOMPUTED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'L H1/H2 common 512-step warm state and matched cumulative 2560 updates, seed17; development only.',
        'completed_H1_saved_tensor_audits': 10, 'paired_reused_H2_saved_tensor_audits': 10,
        'per_pair_final_summary': rows, 'recomputed_aggregates': aggregates,
        'final_score_pair_directions': {'H2_higher': sum(r['H2_minus_H1_score'] > 0 for r in rows),
            'H1_higher': sum(r['H2_minus_H1_score'] < 0 for r in rows),
            'ties': sum(r['H2_minus_H1_score'] == 0 for r in rows)},
        'remote_json_reverification': json.loads(reply.stdout), 'remote_json_sha256': remote_expected,
        'saved_training_cost_not_independently_recomputed': comparison['training_cost'],
        'interpretation': 'At K64, frozen equal-group/equal-dose H2-minus-H1 score is -0.0013020833, slightly favoring H1 (about0.1302 percentage points of score). Known=-0.00390625, fact=0 and rule=0; two pair scores favor H1 and eight tie. Intermediate aggregate deltas change sign: step8=-0.00010205, step16=+0.00666115, step32=+0.00003799. This gives no stable H2 advantage in this one-seed development supplement. Score saturation hides retention differences: all eight fact/rule endpoint scores are1, while keep-answer counts and NLLs differ in both directions. The last medium doubling-rule pair favors H1 by6 candidate and14 strictEM keep answers, yet H1 still loses10/12 relative to the clean parent. Report the small aggregate difference alongside actual retention; do not interpret a saturated score as complete recovery or use this supplement to change frozen architecture/formal selection.',
        'limitations': comparison['limitations'] + [
            'Only one phi seed and five tasks; paired cells are not independent training-seed replicates. No confirmatory statistical or matched-Adam superiority claim.',
            'Known task and keep use the same query pool; fact/rule use distinct pools. Arithmetic worlds have only four interpolation queries each.',
            'Saved prediction/tensor audits do not independently recompute model outputs, tokenization, derivatives or per-token language NLL.',
            'Training cost counters are preserved from the controller report, omit direct QA calls and are not total FLOPs. Wrapper times include lock waits; the common warm is shared.',
            'Saved initial evaluation equality and the declared shared adapter path do not independently verify initial adapter equality tensor by tensor.',
        ],
        'evidence_sha256': evidence, 'selection_policy_changed': False, 'horizon_selected': False,
        'method_validated': False, 'scientific_promotion_allowed': False, 'full_goal_complete': False,
        'training_modified_or_restarted': False,
    }
    with output.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for path in [Path(__file__), output]:
        print(json.dumps(sync.sync_preserved(path)))
    print(json.dumps({'status': result['status'], 'final': aggregates[-1],
                      'directions': result['final_score_pair_directions']}))


if __name__ == '__main__':
    main()
