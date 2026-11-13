"""Describe preserved first-X evidence and update conditional queue costs; no training."""
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
    names = {a: f'phi_dev_{a}_2048_t0_clean_s17' for a in ['L', 'X']}
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
        assert audit['q_apply'] == 0 and audit['method'] == 'phi'
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
    assert audits['L']['initial'] == audits['X']['initial'] == audits['X']['clean']
    assert configs['X']['data'] == configs['X']['retain_data']

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

    metrics = {a: summary(d) for a, d in audits.items()}
    difference = {route: {key: metrics['X']['final'][route][key] - metrics['L']['final'][route][key]
                         for key in ['candidate_correct', 'strict_EM_correct', 'nll']}
                  for route in ['task', 'keep']}
    difference['language_nll'] = audits['X']['final']['language']['nll'] - audits['L']['final']['language']['nll']
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    assessment = {
        'status': 'TWENTY_FIRST_DEVELOPMENT_CELL_AND_FIRST_X_L_MATCHED_ENDPOINT_DESCRIPTIVE_REVIEW_ONLY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'First X2048 cell, known task, zero noise, seed17, K64; 21/60 base cells complete.',
        'runs': {a: 'runs/' + name for a, name in names.items()},
        'task_and_keep_are_the_same_query_pool': True,
        'config_differences_verified': config_diff,
        'clean_and_initial_evaluations_identical_between_architectures': True,
        'metrics_by_architecture': metrics,
        'language_nll_by_architecture': {a: {s: d[s]['language']['nll'] for s in ['clean', 'initial', 'final']}
                                         for a, d in audits.items()},
        'final_X_minus_L': difference,
        'loss_changes_by_architecture': {a: d['loss_changes'] for a, d in audits.items()},
        'job_wall_seconds': {a: d['job_wall_seconds'] for a, d in audits.items()},
        'pair_evidence_reverification': {'non_tensor_files_per_cell': counts,
                                       'remote_unique_tensor_files_sha256_reverified': tensor_review},
        'interpretation': 'In this clean known-task cell, X ends at511/512 candidate and506/512 strictEM versus L510/506; the clean parent is512/504. X has one more candidate answer than L and equal strictEM, with lower task/keep NLL but slightly higher language NLL. Both lose candidate answers against the clean parent. Task and keep use the same512 queries, so these are not independent successes. Zero noise means this cell does not test damage recovery. One seed and one task cannot establish architecture superiority, Adam superiority or generalization.',
        'limitations': audits['X']['limitations'] + [
            'Only one completed X cell, zero noise, same task/keep query pool; full frozen architecture/dose/task grid remains incomplete.',
            'Matching configurations and saved initial evaluations do not independently establish tensor-by-tensor equality of initial adapters.',
            'No architecture selection or conditional extension before completion of the frozen grid.'],
        'evidence_sha256': evidence, 'selection_policy_changed': False,
        'scientific_receipt_generated': False, 'method_validated': False,
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    assessment_path = DIRECTORY / 'twenty_first_cell_and_first_X_L_endpoint_assessment.json'
    preserve(assessment_path, assessment)

    all_audits = {}
    for path in sorted(DIRECTORY.glob('phi_dev_*.json')):
        match = re.fullmatch(r'phi_dev_(?:[LX]_(?:2048|2560)|standard_adam|paired_adam)_t([0-4])_(?:clean|medium)_s17', path.stem)
        if not match:
            continue
        doc = read(path)
        assert doc['job_status'] == 'PASS' and doc['job_wall_seconds'] > 0
        all_audits[path.stem] = (int(match[1]), doc, path)
    assert len(all_audits) == 21
    costs = {}
    for task in range(5):
        values = [d['job_wall_seconds'] for t, d, _ in all_audits.values() if t == task]
        mean = statistics.mean(values)
        costs[str(task)] = {'completed_cells': len(values), 'remaining_base_cells': 12 - len(values),
                            'remaining_additional_H1_cells': 2,
                            'mean_observed_wall_seconds': mean,
                            'remaining_base_seconds_proxy': (12 - len(values)) * mean,
                            'additional_H1_seconds_proxy': 2 * mean}
    base = sum(c['remaining_base_seconds_proxy'] for c in costs.values()) / 86400
    h1 = sum(c['additional_H1_seconds_proxy'] for c in costs.values()) / 86400
    prior = ROOT / 'records/development_eta_20261106_0632.json'
    pairs = ROOT / 'records/horizon_effect_comparison_20261103_1005/declared_pairs.json'
    assert len(read(pairs)) == 10
    horizon = ROOT / 'records/horizon_effect_monitor_20261106_095544_utc/result.json'
    assert read(horizon)['pass_jobs'] == 0 and read(horizon)['running_jobs'] == 1
    eta = {
        'status': 'CONDITIONAL_BASE_AND_LAUNCHED_H1_SHARED_DEVICE_COST_EXTRAPOLATION_NOT_FULL_PROTOCOL_ETA',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'completed_base_cells': 21, 'declared_base_cells': 60, 'remaining_base_cells': 39,
        'additional_H1_completed': 0, 'additional_H1_remaining_including_running': 10,
        'observed_architecture_cell_counts': {'L': 20, 'X': 1, 'standard_adam': 0, 'paired_adam': 0},
        'task_cost_estimates': costs,
        'base_remaining_serial_work_days_proxy': base,
        'additional_H1_serial_work_days_proxy': h1,
        'combined_shared_device_remaining_days_proxy': base + h1,
        'communicated_base_only_rough_days': [3, 4],
        'communicated_base_and_additional_H1_rough_days': [4, 5],
        'assumptions': [
            'Existing base and added H1 queues serialize on the original single device lock; H1 work is included in the combined wall-clock estimate.',
            'Unmeasured X/Adam/H1 tasks cost approximately the corresponding observed task mean; only one X cell and no Adam/H1 endpoints are measured.',
            'Running or waiting cells are counted at full estimated active cost; no partial-step credit or elapsed queue wait is subtracted.',
            'No interruption, retries, conditionally triggered training extension or additional GPU workloads.'],
        'limitations': [
            'This is arithmetic from20 L and1 X cells, not a validated cross-method ETA, upper bound or completion promise.',
            'First X known clean cell took7772.08 seconds, similar to its L match7827.02 seconds; one pair cannot calibrate all tasks.',
            'Future multi-controller job wall times can include device-lock wait and must not be summed as active GPU costs without accounting for queue wait.',
            'The4-5 day range covers the39 remaining base cells plus10 additional H1 cells only.',
            'Conditional development/core extensions and all subsequent full-protocol stages are excluded.'],
        'prior_estimate': str(prior.relative_to(ROOT)), 'full_protocol_eta': None,
        'full_protocol_remaining_scope': read(prior)['full_protocol_remaining_scope'],
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in
                           [prior, pairs, horizon, assessment_path, Path(__file__)] +
                           [p for _, _, p in all_audits.values()]},
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    eta_path = ROOT / 'records/development_eta_20261106_1005.json'
    preserve(eta_path, eta)
    modulepath = ROOT / 'records/development_monitor_20261104_2128/review_prefix_v4.py'
    spec = importlib.util.spec_from_file_location('preserved_sync', modulepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [Path(__file__), assessment_path, eta_path]:
        print(json.dumps(module.sync_preserved(path.resolve())))
    print(json.dumps({'status': assessment['status'], 'final_X_minus_L': difference,
                      'base_days_proxy': base, 'additional_H1_days_proxy': h1,
                      'combined_days_proxy': base + h1}))


if __name__ == '__main__':
    main()
