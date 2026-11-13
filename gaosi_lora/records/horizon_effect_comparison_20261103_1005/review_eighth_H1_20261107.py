"""Describe the preserved first medium-noise rule H1/H2 pair without selecting a method."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = HERE / 'eighth_H1_first_medium_rule_endpoint_assessment.json'
    assert not output.exists()
    names = {'H1': 'phi_horizon_L_H1_2560_t3_medium_s17_v1', 'H2': 'phi_dev_L_2560_t3_medium_s17'}
    directories = {'H1': HERE, 'H2': ROOT / 'records/development_completion_20261104_2358'}
    audits, configs, tensors, counts, process, evidence = {}, {}, {}, {}, {}, {}
    for horizon, name in names.items():
        directory = directories[horizon]
        auditpath = directory / (name + '.json')
        receiptpath = directory / ('fetch_receipt_' + name + '.json')
        saved = directory / (name + '_evidence')
        manifestpath = saved / 'manifest.json'
        receipt, audit, manifest = [read(p) for p in [receiptpath, auditpath, manifestpath]]
        assert sha(auditpath) == receipt['audit_sha256']
        assert sha(directory / (name + '_evidence.tar.gz')) == receipt['archive']['sha256']
        assert sha(manifestpath) == receipt['archive']['manifest_sha256']
        assert audit['job_status'] == 'PASS' and audit['completed_recovery_steps'] == audit['checkpoint_step'] == 64
        assert audit['checkpoint_parameters_and_inner_state_finite'] and audit['source_files_verified'] == 25
        assert audit['q_apply'] == .7 and audit['method'] == 'phi'
        assert len(manifest['files']) == receipt['archive']['files']
        for name_in_manifest, expected in manifest['files'].items():
            path = (ROOT / name_in_manifest).resolve()
            assert path.is_relative_to(saved) and sha(path) == expected
        counts[horizon] = len(manifest['files'])
        for name_in_manifest, expected in manifest['tensor_files_preserved_remotely_only_sha256'].items():
            assert name_in_manifest not in tensors or tensors[name_in_manifest] == expected
            tensors[name_in_manifest] = expected
        configs[horizon] = read(saved / 'snapshot/runs' / name / 'config.json')
        events = [json.loads(line) for line in (saved / 'snapshot/runs' / name / 'events.jsonl').read_text().splitlines()]
        assert events[-1]['event'] == 'evaluation' and events[-1]['step'] == 64
        process[horizon] = events[-1]['process_wall_seconds']
        audits[horizon] = audit
        for path in [auditpath, receiptpath, manifestpath]:
            evidence[str(path.relative_to(ROOT))] = sha(path)
    diff = sorted(k for k in set(configs['H1']) | set(configs['H2']) if configs['H1'].get(k) != configs['H2'].get(k))
    assert diff == ['freeze_manifest', 'initial_adapter', 'out', 'phi']
    assert configs['H1']['initial_adapter'] == configs['H2']['out'] + '/initial.pt'
    assert audits['H1']['clean'] == audits['H2']['clean'] and audits['H1']['initial'] == audits['H2']['initial']
    assert configs['H1']['data'] != configs['H1']['retain_data']
    assert all(audit['initial'] != audit['clean'] for audit in audits.values())
    pools = {}
    for horizon, name in names.items():
        saved = directories[horizon] / (name + '_evidence') / 'snapshot'
        data = [saved / configs[horizon][key] for key in ['data', 'retain_data']]
        taskrows, keeprows = [[json.loads(line) for line in path.read_text().splitlines()] for path in data]
        assert len(taskrows) == 11 and len(keeprows) == 3072
        support = [row for row in taskrows if row['role'] == 'support']
        queries = [row for row in taskrows if row['role'] == 'query']
        sx, qx = [{row['x'] for row in rows} for rows in [support, queries]]
        assert len(support) == len(sx) == 7 and len(queries) == len(qx) == 4
        assert sx.isdisjoint(qx) and all(min(sx) < x < max(sx) for x in qx)
        assert all(row['kind'] == 'affine_integer' and int(row['answer']) == row['x'] for row in taskrows)
        assert {row['world_id'] for row in taskrows} == {'dev_integer_world_000'}
        assert {row['sample_id'] for row in taskrows}.isdisjoint({row['sample_id'] for row in keeprows})
        assert {row['world_id'] for row in taskrows}.isdisjoint({row['world_id'] for row in keeprows})
        assert len({row['fact_id'] for row in keeprows}) == 512
        assert audits[horizon]['coverage'] == audits[horizon]['pool'] == 7
        assert audits[horizon]['final']['task']['n'] == 4 and audits[horizon]['final']['keep']['n'] == 512
        assert len(audits[horizon]['prediction_reviews']) == 12
        assert sum(row['rows'] for row in audits[horizon]['prediction_reviews']) == 3096
        pools[horizon] = {'task_kind': 'affine_integer', 'rule': 'f(x)=x',
                          'support_inputs': sorted(sx), 'query_inputs': sorted(qx),
                          'support_and_query_inputs_disjoint': True,
                          'query_inputs_within_support_range': True,
                          'task_and_keep_sample_ids_and_worlds_disjoint': True,
                          'task_evaluation_count': 4, 'keep_fact_bindings_and_evaluation_count': 512,
                          'prediction_files': 12, 'prediction_rows': 3096}
        for path in data:
            evidence[str(path.relative_to(ROOT))] = sha(path)
    paired_name = names['H1'] + '_paired_review_v2.json'
    monitor = ROOT / 'records/horizon_effect_monitor_20261107_115552_utc'
    pairedpath = monitor / 'snapshot/records/horizon_effect_comparison_20261103_1005' / paired_name
    monitor_manifest = read(monitor / 'archive_source_sha256.json')
    assert sha(pairedpath) == monitor_manifest[str(pairedpath.relative_to(ROOT))]
    pair = read(pairedpath)
    assert pair['group'] == 'rule' and pair['dose'] == 'medium'
    assert pair['runs'] == [configs['H2']['out'], configs['H1']['out']]
    assert pair['curves'][-1]['step'] == 64
    assert all(pair['curves'][-1][h] == audits[h]['final'] for h in names)
    assert pair['noise_timing_review']['all_non_timing_fields_exactly_equal']
    assert pair['noise_timing_review']['excluded_fields'] == ['boundary_seconds']
    assert audits['H1']['noise_timing_reviews']['paired_results']['all_non_timing_fields_exactly_equal']
    expected_remote = dict(tensors)
    expected_remote[str((HERE / paired_name).relative_to(ROOT))] = sha(pairedpath)
    code = '''import hashlib,json,sys
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora')
expected=json.load(sys.stdin)
actual={}
for name,wanted in expected.items():
 path=(root/name).resolve()
 assert path.is_relative_to(root)
 h=hashlib.sha256()
 with path.open('rb') as handle:
  for block in iter(lambda:handle.read(2**20),b''):h.update(block)
 actual[name]=h.hexdigest()
 assert actual[name]==wanted,name
print(json.dumps({'status':'PAIRED_SAVED_TENSOR_FILES_AND_V2_PAIR_REPORT_SHA256_REVERIFIED','sha256':actual}))
'''
    ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
    reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(code)], input=json.dumps(expected_remote),
                           text=True, capture_output=True, timeout=60, check=True)
    remote = json.loads(reply.stdout)
    assert remote['sha256'] == expected_remote
    for path in [pairedpath, monitor / 'review.json', Path(__file__)]:
        evidence[str(path.relative_to(ROOT))] = sha(path)
    a, b = audits['H1']['final'], audits['H2']['final']
    differences = {route: {
        'candidate_correct': round(a[route]['n'] * (a[route]['candidate_accuracy'] - b[route]['candidate_accuracy'])),
        'strict_EM_correct': round(a[route]['n'] * (a[route]['strict_EM'] - b[route]['strict_EM'])),
        'nll': a[route]['nll'] - b[route]['nll']} for route in ['task', 'keep']}
    differences['language_nll'] = a['language']['nll'] - b['language']['nll']
    report = {
        'status': 'EIGHTH_H1_FIRST_MEDIUM_RULE_MATCHED_ENDPOINT_DESCRIPTIVE_REVIEW_NO_HORIZON_SELECTION',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'First rule task, medium q0.7, seed17, K64; eight of ten additional H1 cells now audited.',
        'metrics': {h: {s: audit[s] for s in ['clean', 'initial', 'final']} for h, audit in audits.items()},
        'H1_minus_H2': differences, 'config_differences_verified': diff,
        'loss_changes': {h: audit['loss_changes'] for h, audit in audits.items()},
        'frozen_single_cell_scores': {h: pair['curves'][-1][h + '_score'] for h in names},
        'task_and_keep_are_same_query_pool': False, 'distinct_pools_verified': pools,
        'non_tensor_files_rehashed': counts, 'unique_remote_tensor_files_sha256_reverified': len(tensors),
        'remote_reverification': remote, 'last_event_process_seconds': process,
        'wrapper_wall_seconds_including_possible_lock_wait': {h: audit['job_wall_seconds'] for h, audit in audits.items()},
        'noise_timing_review': pair['noise_timing_review'],
        'interpretation': 'Both learn four query inputs for the identity rule from1/4 candidate and0/4 strictEM after noise to4/4 on both metrics. Known keep recovers from364/362 to509/507 for both H1 and H2, against clean parent512/504; candidate recovery is incomplete while strictEM exceeds the clean count. H1 task NLL is lower by5.96e-8 and language NLL lower by0.00127327, while keep NLL is higher by0.00012204. H1/H2 keep residual NLL is-0.00487604/-0.00499808 and language residual-0.00451535/-0.00324207; both frozen single-cell scores are1. Noise lowers task NLL2.144102 to1.441256 and increases candidate accuracy0/4 to1/4 while strictEM remains0/4; signed injected task loss is-0.702846, so this is acquisition rather than recovery of damaged task knowledge. Keep is damaged and substantially restored. This supports within-world interpolation and old-knowledge recovery with equal discrete retention and small mixed horizon differences. Query inputs are disjoint from support inputs but inside their range; copying the input solves f(x)=x and only four queries are evaluated, so general rule reasoning and extrapolation are not established. Four development tasks, eight pairs and one phi seed cannot justify horizon selection or superiority to matched Adam.',
        'limitations': audits['H1']['limitations'] + ['Eight of ten declared H1 pairs are complete; the two second-rule pairs remain incomplete.',
            'Distinct task4 rule-query and keep512 fact-binding pools are checked; this is a small interpolation world with no formal-set or independent phi-seed conclusion.',
            'Wrapper time includes lock wait and is not additive independent GPU cost.',
            'Matching saved initial evaluations do not independently establish tensor-by-tensor equality of initial adapters.'],
        'evidence_sha256': evidence, 'selection_policy_changed': False,
        'method_validated': False, 'scientific_promotion_allowed': False, 'full_goal_complete': False,
        'training_modified_or_restarted': False}
    with output.open('x') as handle:
        handle.write(json.dumps(report, indent=2, allow_nan=False) + '\n')
    spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py')
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    for path in [Path(__file__), output]:
        print(json.dumps(sync.sync_preserved(path)))
    print(json.dumps({'status': report['status'], 'H1_minus_H2': differences, 'process_seconds': process}))


if __name__ == '__main__':
    main()
