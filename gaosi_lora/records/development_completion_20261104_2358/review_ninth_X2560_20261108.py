"""Describe saved second clean doubling-rule X/L evidence; preserve prior reviews and queues."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import statistics
import subprocess


ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve(path, data):
    with path.open('x') as handle:
        handle.write(json.dumps(data, indent=2, allow_nan=False) + '\n')


def main():
    names = {'L': 'phi_dev_L_2560_t4_clean_s17', 'X': 'phi_dev_X_2560_t4_clean_s17', 'X2048': 'phi_dev_X_2048_t4_clean_s17'}
    audits, configs, counts, tensors, evidence = {}, {}, {}, {}, {}
    for architecture, name in names.items():
        auditpath = DIRECTORY / (name + '.json')
        receiptpath = DIRECTORY / ('fetch_receipt_' + name + '.json')
        archive = DIRECTORY / (name + '_evidence.tar.gz')
        saved = DIRECTORY / (name + '_evidence')
        receipt = read(receiptpath)
        audit = audits[architecture] = read(auditpath)
        assert audit['job_status'] == 'PASS'
        assert audit['status'] == 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
        assert audit['completed_recovery_steps'] == audit['checkpoint_step'] == 64
        assert audit['checkpoint_parameters_and_inner_state_finite']
        assert audit['source_files_verified'] == 25
        assert len(audit['prediction_reviews']) == 12
        assert sum(row['rows'] for row in audit['prediction_reviews']) == 3096
        assert audit['q_apply'] == 0.0 and audit['method'] == 'phi'
        assert sha(auditpath) == receipt['audit_sha256']
        assert sha(archive) == receipt['archive']['sha256']
        manifestpath = saved / 'manifest.json'
        manifest = read(manifestpath)
        if 'manifest_sha256' in receipt['archive']:
            assert sha(manifestpath) == receipt['archive']['manifest_sha256']
        for filename, expected in manifest['files'].items():
            local = (ROOT / filename).resolve()
            assert local.is_relative_to(saved) and sha(local) == expected
        assert len(manifest['files']) == receipt['archive']['files']
        counts[architecture] = len(manifest['files'])
        for filename, expected in manifest['tensor_files_preserved_remotely_only_sha256'].items():
            assert filename not in tensors or tensors[filename] == expected
            tensors[filename] = expected
        configpath = saved / 'snapshot/runs' / name / 'config.json'
        configs[architecture] = read(configpath)
        for path in [auditpath, receiptpath, manifestpath, configpath]:
            evidence[str(path.relative_to(ROOT))] = sha(path)

    config_diff = sorted(k for k in set(configs['L']) | set(configs['X'])
                         if configs['L'].get(k) != configs['X'].get(k))
    assert config_diff == ['architecture', 'out', 'phi']
    assert audits['L']['clean'] == audits['X']['clean']
    assert audits['L']['initial'] == audits['X']['initial']
    assert audits['X']['initial'] == audits['X']['clean']
    assert configs['X']['data'] != configs['X']['retain_data']
    assert audits['X2048']['clean'] == audits['X2048']['initial'] == audits['X']['clean']
    earlier_diff = sorted(k for k in set(configs['X']) | set(configs['X2048']) if configs['X'].get(k) != configs['X2048'].get(k))
    assert earlier_diff == ['out', 'phi']

    distinct_pools = {}
    for architecture, name in names.items():
        config = configs[architecture]
        saved = DIRECTORY / (name + '_evidence') / 'snapshot'
        taskpath, keeppath = [saved / config[key] for key in ['data', 'retain_data']]
        taskrows, keeprows = [[json.loads(line) for line in path.read_text().splitlines()]
                              for path in [taskpath, keeppath]]
        assert len(taskrows) == 11 and len(keeprows) == 3072
        assert all(row['kind'] == 'affine_integer' for row in taskrows)
        support = [row for row in taskrows if row['role'] == 'support']
        queries = [row for row in taskrows if row['role'] == 'query']
        sx, qx = [{row['x'] for row in rows} for rows in [support, queries]]
        assert len(support) == len(sx) == 7 and len(queries) == len(qx) == 4
        assert sx.isdisjoint(qx)
        assert all(min(sx) < x < max(sx) for x in qx)
        assert all(int(row['answer']) == 2 * row['x'] for row in taskrows)
        assert {row['world_id'] for row in taskrows} == {'dev_integer_world_001'}
        assert len({row['fact_id'] for row in keeprows}) == 512
        assert {row['sample_id'] for row in taskrows}.isdisjoint({row['sample_id'] for row in keeprows})
        assert {row['world_id'] for row in taskrows}.isdisjoint({row['world_id'] for row in keeprows})
        assert audits[architecture]['coverage'] == audits[architecture]['pool'] == 7
        assert len(audits[architecture]['prediction_reviews']) == 12
        assert sum(row['rows'] for row in audits[architecture]['prediction_reviews']) == 3096
        distinct_pools[architecture] = {
            'task_kind': 'affine_integer', 'rule': 'f(x)=2x',
            'task_support_rows': 7, 'task_query_rows': 4,
            'support_inputs': sorted(sx), 'query_inputs': sorted(qx),
            'support_and_query_inputs_disjoint': True,
            'query_inputs_within_support_range': True,
            'task_and_keep_sample_ids_and_worlds_disjoint': True,
            'keep_fact_bindings': 512,
            'task_evaluation_queries': audits[architecture]['final']['task']['n'],
            'keep_evaluation_queries': audits[architecture]['final']['keep']['n'],
            'prediction_files': 12, 'prediction_rows': 3096}
        assert distinct_pools[architecture]['task_evaluation_queries'] == 4
        assert distinct_pools[architecture]['keep_evaluation_queries'] == 512
        for path in [taskpath, keeppath]:
            evidence[str(path.relative_to(ROOT))] = sha(path)

    remote = '''import hashlib,json,sys
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
print(json.dumps({'status':'REMOTE_PAIRED_TENSOR_FILE_SHA256_REVERIFIED','files':len(actual),'sha256':actual}))
'''
    ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
           '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
    reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(remote)],
                           input=json.dumps(tensors), text=True, capture_output=True,
                           check=True, timeout=60)
    tensor_review = json.loads(reply.stdout)
    assert tensor_review['sha256'] == tensors

    def summary(audit):
        return {stage: {route: {
            'candidate_correct': round(audit[stage][route]['candidate_accuracy'] * audit[stage][route]['n']),
            'strict_EM_correct': round(audit[stage][route]['strict_EM'] * audit[stage][route]['n']),
            'query_count': audit[stage][route]['n'], 'nll': audit[stage][route]['nll']}
            for route in ['task', 'keep']} for stage in ['clean', 'initial', 'final']}

    process_wall = {}
    for architecture, name in names.items():
        eventpath = DIRECTORY / (name + '_evidence') / 'snapshot/runs' / name / 'events.jsonl'
        events = [json.loads(line) for line in eventpath.read_text().splitlines()]
        assert events[-1]['event'] == 'evaluation' and events[-1]['step'] == 64
        process_wall[architecture] = events[-1]['process_wall_seconds']
        evidence[str(eventpath.relative_to(ROOT))] = sha(eventpath)
    metrics = {a: summary(d) for a, d in audits.items()}
    difference = {route: {key: metrics['X']['final'][route][key] - metrics['L']['final'][route][key]
                         for key in ['candidate_correct', 'strict_EM_correct', 'nll']}
                  for route in ['task', 'keep']}
    difference['language_nll'] = audits['X']['final']['language']['nll'] - audits['L']['final']['language']['nll']
    checkpoint_difference = {route: {key: metrics['X']['final'][route][key] - metrics['X2048']['final'][route][key] for key in ['candidate_correct', 'strict_EM_correct', 'nll']} for route in ['task', 'keep']}
    checkpoint_difference['language_nll'] = audits['X']['final']['language']['nll'] - audits['X2048']['final']['language']['nll']
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    assessment = {
        'status': 'THIRTY_NINTH_DEVELOPMENT_CELL_AND_NINTH_X2560_CLEAN_RULE_DESCRIPTIVE_REVIEW_ONLY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'Ninth X2560 cell, second doubling rule task, clean q=0, seed17, K64; 39/60 base cells complete; compare L2560 and X2048.',
        'runs': {a: 'runs/' + name for a, name in names.items()},
        'task_and_keep_are_the_same_query_pool': False,
        'distinct_rule_task_and_fact_keep_pools_verified': distinct_pools,
        'config_differences_verified': config_diff,
        'clean_and_initial_evaluations_identical_between_architectures': True,
        'metrics_by_architecture': metrics,
        'language_nll_by_architecture': {a: {s: d[s]['language']['nll'] for s in ['clean', 'initial', 'final']}
                                         for a, d in audits.items()},
        'final_X_minus_L': difference,
        'X2560_minus_X2048': checkpoint_difference,
        'checkpoint_config_differences_verified': earlier_diff,
        'loss_changes_by_architecture': {a: d['loss_changes'] for a, d in audits.items()},
        'wrapper_wall_seconds_including_possible_lock_wait': {a: d['job_wall_seconds'] for a, d in audits.items()},
        'last_event_process_wall_seconds': process_wall,
        'pair_evidence_reverification': {'non_tensor_files_per_cell': counts,
                                       'remote_unique_tensor_files_sha256_reverified': tensor_review},
        'interpretation': 'All three cells learn four held-out input queries for the doubling rule from1/4 candidate and2/4 strictEM to4/4 on both metrics. Parent keep is512/504; X2560 and X2048 retain512/502, while L2560 retains511/502. X2560 gains one keep candidate answer against L2560 at equal strict count, with keep NLL higher by0.00236622, task NLL lower by0.000001994019 and language NLL lower by0.00272108. Relative to X2048, answer counts are equal, keep NLL is higher by0.00211113, task NLL lower by0.0000000758603 and language NLL lower by0.00339593. X2560 keep residual is+0.01266848 and language residual-0.00116628; two parent strict answers remain lost despite all candidate answers being retained. Task learning is positive, with small mixed differences in old-knowledge and language metrics. Query inputs are disjoint from support inputs but inside their range, and only four queries are evaluated; this is within-world interpolation, not a demonstration of broad rule reasoning or extrapolation. Zero noise does not measure injected-damage recovery. Nine of ten X2560 cells are complete; the frozen60-cell grid remains incomplete, so no aggregate architecture choice, conditional extension, superiority to matched Adam or independent-seed claim follows.',
        'limitations': audits['X']['limitations'] + [
            'Only nine completed X2560 cells, five tasks and one phi seed; this doubling rule world has four interpolation queries and the frozen architecture/dose/task grid remains incomplete.',
            'Matching configurations and saved initial evaluations do not independently establish tensor-by-tensor equality of initial adapters.',
            'No architecture selection or conditional extension before completion of the frozen grid.'],
        'evidence_sha256': evidence, 'selection_policy_changed': False,
        'scientific_receipt_generated': False, 'method_validated': False,
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    assessment_path = DIRECTORY / 'thirty_ninth_cell_and_ninth_X2560_clean_rule_endpoint_assessment.json'
    preserve(assessment_path, assessment)

    modulepath = ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py'
    spec = importlib.util.spec_from_file_location('preserved_sync', modulepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [Path(__file__), assessment_path]:
        print(json.dumps(module.sync_preserved(path.resolve())))
    print(json.dumps({'status': assessment['status'], 'final_X_minus_L': difference,
                      'X2560_minus_X2048': checkpoint_difference, 'process_wall_seconds': process_wall}))


if __name__ == '__main__':
    main()
