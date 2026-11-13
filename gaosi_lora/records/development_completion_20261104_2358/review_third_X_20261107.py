"""Describe saved first clean fact X/L evidence; preserve prior reviews and queues."""
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
    names = {a: f'phi_dev_{a}_2048_t1_clean_s17' for a in ['L', 'X']}
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

    distinct_pools = {}
    for architecture, name in names.items():
        config = configs[architecture]
        saved = DIRECTORY / (name + '_evidence') / 'snapshot'
        taskpath, keeppath = [saved / config[key] for key in ['data', 'retain_data']]
        taskrows, keeprows = [[json.loads(line) for line in path.read_text().splitlines()]
                              for path in [taskpath, keeppath]]
        taskbindings, keepbindings = [{row['fact_id'] for row in rows} for rows in [taskrows, keeprows]]
        assert len(taskbindings) == 32 and len(keepbindings) == 512
        assert taskbindings.isdisjoint(keepbindings)
        assert {row['sample_id'] for row in taskrows}.isdisjoint({row['sample_id'] for row in keeprows})
        distinct_pools[architecture] = {'task_bindings': 32, 'keep_bindings': 512,
                                        'fact_ids_and_sample_ids_disjoint': True,
                                        'task_evaluation_queries': audits[architecture]['final']['task']['n'],
                                        'keep_evaluation_queries': audits[architecture]['final']['keep']['n']}
        assert distinct_pools[architecture]['task_evaluation_queries'] == 32
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
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    assessment = {
        'status': 'TWENTY_THIRD_DEVELOPMENT_CELL_AND_FIRST_CLEAN_FACT_X_L_ENDPOINT_DESCRIPTIVE_REVIEW_ONLY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'Third X2048 cell, first fact task, clean q=0, seed17, K64; 23/60 base cells complete.',
        'runs': {a: 'runs/' + name for a, name in names.items()},
        'task_and_keep_are_the_same_query_pool': False,
        'distinct_fact_pools_verified': distinct_pools,
        'config_differences_verified': config_diff,
        'clean_and_initial_evaluations_identical_between_architectures': True,
        'metrics_by_architecture': metrics,
        'language_nll_by_architecture': {a: {s: d[s]['language']['nll'] for s in ['clean', 'initial', 'final']}
                                         for a, d in audits.items()},
        'final_X_minus_L': difference,
        'loss_changes_by_architecture': {a: d['loss_changes'] for a, d in audits.items()},
        'wrapper_wall_seconds_including_possible_lock_wait': {a: d['job_wall_seconds'] for a, d in audits.items()},
        'last_event_process_wall_seconds': process_wall,
        'pair_evidence_reverification': {'non_tensor_files_per_cell': counts,
                                       'remote_unique_tensor_files_sha256_reverified': tensor_review},
        'interpretation': 'Both learn the first clean fact world from2 to32/32 candidate and strictEM answers. X retains512/507 known keep answers versus L511/507; parent keep is512/504. X has one more keep candidate answer and equal strictEM, lower keep NLL by0.00456339 and lower language NLL by0.00121200, while task NLL is higher by0.00016773. This is positive within-world acquisition with small mixed architecture differences, slightly favoring X on measured retention. This zero-noise cell does not measure injected-damage recovery. Distinct task32 and keep512 fact-binding pools are verified; no aggregate architecture, matched Adam or held-out generalization conclusion follows.',
        'limitations': audits['X']['limitations'] + [
            'Only three completed X cells, two tasks and one phi seed; the first fact world has32 evaluation queries and the frozen architecture/dose/task grid remains incomplete.',
            'Matching configurations and saved initial evaluations do not independently establish tensor-by-tensor equality of initial adapters.',
            'No architecture selection or conditional extension before completion of the frozen grid.'],
        'evidence_sha256': evidence, 'selection_policy_changed': False,
        'scientific_receipt_generated': False, 'method_validated': False,
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    assessment_path = DIRECTORY / 'twenty_third_cell_and_first_clean_fact_X_L_endpoint_assessment.json'
    preserve(assessment_path, assessment)

    modulepath = ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py'
    spec = importlib.util.spec_from_file_location('preserved_sync', modulepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [Path(__file__), assessment_path]:
        print(json.dumps(module.sync_preserved(path.resolve())))
    print(json.dumps({'status': assessment['status'], 'final_X_minus_L': difference,
                      'process_wall_seconds': process_wall}))


if __name__ == '__main__':
    main()
