"""Verify one saved Adam endpoint against matched phi cells; no grid selection."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--completed-base-cells', required=True, type=int)
    args = parser.parse_args()
    match = re.fullmatch(r'phi_dev_(standard_adam|paired_adam)_t([0-4])_(clean|medium)_s17', args.run)
    assert match and 41 <= args.completed_base_cells <= 60
    method, task, dose = match.groups()
    task = int(task)
    out = HERE / (args.run + '_matched_endpoint_assessment.json')
    assert not out.exists()
    suffix = f'_t{task}_{dose}_s17'
    names = {'baseline': args.run, **{f'{a}{step}': 'phi_dev_' + a + '_' + str(step) + suffix
                                     for a in ['L', 'X'] for step in [2048, 2560]}}
    if method == 'paired_adam':
        names['standard_adam'] = 'phi_dev_standard_adam' + suffix
    evidence, tensors, audits, configs, pools, counts, process_wall = {}, {}, {}, {}, {}, {}, {}
    for label, name in names.items():
        auditpath = HERE / (name + '.json')
        fetchpath = HERE / ('fetch_receipt_' + name + '.json')
        archive = HERE / (name + '_evidence.tar.gz')
        saved = HERE / (name + '_evidence')
        manifestpath = saved / 'manifest.json'
        audit, fetch, manifest = read(auditpath), read(fetchpath), read(manifestpath)
        assert audit['status'] == 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
        assert audit['job_status'] == 'PASS' and audit['run'] == 'runs/' + name
        assert audit['completed_recovery_steps'] == audit['checkpoint_step'] == 64
        assert audit['checkpoint_parameters_and_inner_state_finite'] and audit['source_files_verified'] == 25
        assert sha(auditpath) == fetch['audit_sha256']
        assert sha(archive) == fetch['archive']['sha256']
        if 'manifest_sha256' in fetch['archive']:
            assert sha(manifestpath) == fetch['archive']['manifest_sha256']
        assert len(manifest['files']) == fetch['archive']['files']
        for filename, expected in manifest['files'].items():
            path = (ROOT / filename).resolve()
            assert path.is_relative_to(saved) and sha(path) == expected, filename
        for filename, expected in manifest['tensor_files_preserved_remotely_only_sha256'].items():
            assert filename not in tensors or tensors[filename] == expected
            tensors[filename] = expected
        configpath = saved / 'snapshot/runs' / name / 'config.json'
        config = read(configpath)
        assert config['K'] == 64 and config['data_seed'] == 17 and config['noise_seed'] == 1729
        assert config['q_apply'] == audit['q_apply'] == (0.0 if dose == 'clean' else .7)
        assert config['method'] == audit['method']
        assert config['method'] == (method if label == 'baseline' else 'standard_adam' if label == 'standard_adam' else 'phi')
        taskpath, keeppath = [saved / 'snapshot' / config[k] for k in ['data', 'retain_data']]
        taskrows, keeprows = rows(taskpath), rows(keeppath)
        assert len(keeprows) == 3072 and len({r['fact_id'] for r in keeprows}) == 512
        common = {'task_and_keep_same_query_pool': task == 0, 'task_rows': len(taskrows),
                  'keep_rows': len(keeprows), 'keep_fact_bindings': 512}
        if task == 0:
            assert taskpath == keeppath and taskrows == keeprows
            assert audit['final']['task'] == audit['final']['keep']
            assert audit['coverage'] == audit['pool'] == 512
            taskn = 512
        elif task in [1, 2]:
            assert len(taskrows) == 192 and {r['kind'] for r in taskrows} == {'fact'}
            assert len({r['fact_id'] for r in taskrows}) == 32
            assert {r['fact_id'] for r in taskrows}.isdisjoint({r['fact_id'] for r in keeprows})
            assert {r['sample_id'] for r in taskrows}.isdisjoint({r['sample_id'] for r in keeprows})
            assert audit['coverage'] == audit['pool'] == 32
            taskn = 32
        else:
            assert len(taskrows) == 11 and {r['kind'] for r in taskrows} == {'affine_integer'}
            support, query = [[r for r in taskrows if r['role'] == role] for role in ['support', 'query']]
            sx, qx = [{r['x'] for r in group} for group in [support, query]]
            assert len(support) == len(sx) == 7 and len(query) == len(qx) == 4
            assert sx.isdisjoint(qx) and all(min(sx) < x < max(sx) for x in qx)
            multiplier = 1 if task == 3 else 2
            assert all(int(r['answer']) == multiplier * r['x'] for r in taskrows)
            assert {r['world_id'] for r in taskrows} == {f'dev_integer_world_{task - 3:03d}'}
            assert {r['world_id'] for r in taskrows}.isdisjoint({r['world_id'] for r in keeprows})
            assert {r['sample_id'] for r in taskrows}.isdisjoint({r['sample_id'] for r in keeprows})
            assert audit['coverage'] == audit['pool'] == 7
            common.update(rule=f'f(x)={"x" if multiplier == 1 else "2x"}', support_inputs=sorted(sx),
                          query_inputs=sorted(qx), query_within_support_range=True)
            taskn = 4
        assert audit['final']['task']['n'] == taskn and audit['final']['keep']['n'] == 512
        assert len(audit['prediction_reviews']) == 12
        assert sum(r['rows'] for r in audit['prediction_reviews']) == 6 * (taskn + 512)
        events = rows(saved / 'snapshot/runs' / name / 'events.jsonl')
        assert events[-1]['event'] == 'evaluation' and events[-1]['step'] == 64
        process_wall[label] = events[-1]['process_wall_seconds']
        audits[label], configs[label], pools[label] = audit, config, common
        counts[label] = len(manifest['files'])
        for path in [auditpath, fetchpath, manifestpath, configpath, taskpath, keeppath]:
            evidence[str(path.relative_to(ROOT))] = sha(path)
    differences = {}
    baseline = configs['baseline']
    for label, config in configs.items():
        if label == 'baseline':
            continue
        diff = sorted(k for k in baseline.keys() | config.keys() if baseline.get(k) != config.get(k))
        expected = ['method', 'out'] if label == 'standard_adam' else ['method', 'out', 'phi']
        if label.startswith('X'):
            expected = ['architecture'] + expected
        assert diff == expected, (label, diff, expected)
        differences[label] = diff
        assert audits[label]['clean'] == audits['baseline']['clean']
        assert audits[label]['initial'] == audits['baseline']['initial']
    remote = '''import hashlib,json,sys
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora')
expected=json.load(sys.stdin)
for name,wanted in expected.items():
 path=(root/name).resolve()
 assert path.is_relative_to(root)
 h=hashlib.sha256()
 with path.open('rb') as handle:
  for block in iter(lambda:handle.read(2**20),b''):h.update(block)
 assert h.hexdigest()==wanted,name
print(json.dumps({'status':'MATCHED_REMOTE_TENSOR_SHA256_REVERIFIED','files':len(expected),'sha256':expected}))
'''
    ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
           '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
    reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(remote)], input=json.dumps(tensors),
                           text=True, capture_output=True, check=True, timeout=60)
    remote_review = json.loads(reply.stdout)
    assert remote_review['sha256'] == tensors
    def metrics(audit):
        return {stage: {route: {'candidate_correct': round(audit[stage][route]['candidate_accuracy'] * audit[stage][route]['n']),
                               'strict_EM_correct': round(audit[stage][route]['strict_EM'] * audit[stage][route]['n']),
                               'query_count': audit[stage][route]['n'], 'nll': audit[stage][route]['nll']}
                        for route in ['task', 'keep']} for stage in ['clean', 'initial', 'final']}
    summaries = {label: metrics(audit) for label, audit in audits.items()}
    deltas = {label: {route: {k: summaries['baseline']['final'][route][k] - summaries[label]['final'][route][k]
                            for k in ['candidate_correct', 'strict_EM_correct', 'nll']}
                      for route in ['task', 'keep']} for label in configs if label != 'baseline'}
    for label, delta in deltas.items():
        delta['language_nll'] = audits['baseline']['final']['language']['nll'] - audits[label]['final']['language']['nll']
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    assessment = {'status': 'SINGLE_ADAM_ENDPOINT_AND_MATCHED_PHI_SAVED_EVIDENCE_DESCRIPTIVE_REVIEW_ONLY',
                  'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'completed_base_cells_at_review': args.completed_base_cells,
                  'run': args.run, 'runs': names, 'dose': dose,
                  'task_group': ['known', 'fact', 'fact', 'rule', 'rule'][task],
                  'pool_checks': pools, 'config_differences_verified': differences,
                  'metrics_by_method': summaries, 'baseline_minus_comparison': deltas,
                  'language_nll_by_method': {label: {s: audit[s]['language']['nll'] for s in ['clean', 'initial', 'final']}
                                             for label, audit in audits.items()},
                  'loss_changes_by_method': {label: audit['loss_changes'] for label, audit in audits.items()},
                  'last_event_process_wall_seconds': process_wall,
                  'wrapper_wall_seconds_including_possible_lock_wait': {label: audit['job_wall_seconds'] for label, audit in audits.items()},
                  'pair_evidence_reverification': {'non_tensor_files_per_cell': counts, 'remote_unique_tensors': remote_review},
                  'evidence_sha256': evidence,
                  'limitations': audits['baseline']['limitations'] + [
                      'Single development task, dose and phi seed; no full-grid selection or statistical superiority conclusion.',
                      'Matched saved initial evaluations do not establish tensor-by-tensor initial adapter equality.',
                      'Known-task and keep queries coincide for t0; these are not independent retention measurements.',
                      'Four within-range rule queries do not establish general rule reasoning or extrapolation.'],
                  'grid_aggregation_or_method_selection_performed': False,
                  'scientific_receipt_generated': False, 'method_validated': False,
                  'full_goal_complete': False, 'training_modified_or_restarted': False}
    with out.open('x') as handle:
        json.dump(assessment, handle, indent=2, allow_nan=False)
        handle.write('\n')
    modulepath = ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py'
    spec = importlib.util.spec_from_file_location('preserved_sync', modulepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [Path(__file__), out]:
        print(json.dumps(module.sync_preserved(path.resolve())))
    print(json.dumps({'assessment': str(out.relative_to(ROOT)), 'sha256': sha(out),
                      'baseline_minus_comparison': deltas, 'last_event_process_wall_seconds': process_wall}))


if __name__ == '__main__':
    main()
