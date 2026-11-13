"""Describe the first X2560 cell against matched L2560 and X2048 evidence; no selection."""
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
    names = {'L': 'phi_dev_L_2560_t0_clean_s17', 'X': 'phi_dev_X_2560_t0_clean_s17',
             'X2048': 'phi_dev_X_2048_t0_clean_s17'}
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
        assert audit['source_files_verified'] == 25
        assert len(audit['prediction_reviews']) == 12
        assert sum(row['rows'] for row in audit['prediction_reviews']) == 6144
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
    assert audits['X2048']['clean'] == audits['X2048']['initial'] == audits['X']['clean']
    earlier_diff = sorted(k for k in set(configs['X']) | set(configs['X2048'])
                          if configs['X'].get(k) != configs['X2048'].get(k))
    assert earlier_diff == ['out', 'phi']
    for key in names:
        assert configs[key]['data'] == configs[key]['retain_data']
        assert all(audits[key][stage]['task'] == audits[key][stage]['keep']
                   for stage in ['clean', 'initial', 'final'])

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
    checkpoint_difference = {route: {key: metrics['X']['final'][route][key] - metrics['X2048']['final'][route][key]
                            for key in ['candidate_correct', 'strict_EM_correct', 'nll']}
                            for route in ['task', 'keep']}
    checkpoint_difference['language_nll'] = audits['X']['final']['language']['nll'] - audits['X2048']['final']['language']['nll']
    process_wall = {}
    for key, name in names.items():
        path = DIRECTORY / (name + '_evidence') / 'snapshot/runs' / name / 'events.jsonl'
        events = [json.loads(line) for line in path.read_text().splitlines()]
        assert events[-1]['event'] == 'evaluation' and events[-1]['step'] == 64
        process_wall[key] = events[-1]['process_wall_seconds']
        evidence[str(path.relative_to(ROOT))] = sha(path)
    evidence[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    assessment = {
        'status': 'THIRTY_FIRST_DEVELOPMENT_CELL_AND_FIRST_X2560_KNOWN_CLEAN_DESCRIPTIVE_REVIEW_ONLY',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope': 'First X2560 cell, known task, zero noise, seed17, K64; 31/60 base cells complete. Compare L2560 and X2048; no aggregate selection.',
        'runs': {a: 'runs/' + name for a, name in names.items()},
        'task_and_keep_are_the_same_query_pool': True,
        'config_differences_verified': config_diff,
        'clean_and_initial_evaluations_identical_between_architectures': True,
        'metrics_by_architecture': metrics,
        'language_nll_by_architecture': {a: {s: d[s]['language']['nll'] for s in ['clean', 'initial', 'final']}
                                         for a, d in audits.items()},
        'final_X_minus_L': difference,
        'X2560_minus_X2048': checkpoint_difference,
        'checkpoint_config_differences_verified': earlier_diff,
        'last_event_process_wall_seconds': process_wall,
        'loss_changes_by_architecture': {a: d['loss_changes'] for a, d in audits.items()},
        'job_wall_seconds': {a: d['job_wall_seconds'] for a, d in audits.items()},
        'pair_evidence_reverification': {'non_tensor_files_per_cell': counts,
                                       'remote_unique_tensor_files_sha256_reverified': tensor_review},
        'interpretation': 'X2560 and matched L2560 both end at511/512 candidate and506/512 strictEM, from a clean parent512/504; X2048 is also511/506. The new X snapshot therefore changes neither answer count on this known clean cell. Relative to L2560, X has higher task/keep NLL by0.00299772 and lower language NLL by0.00651968. Relative to X2048, X2560 has higher task/keep NLL by0.00368486 and lower language NLL by0.00925171, a mixed checkpoint change. X2560 keep residual NLL is-0.00463628 and language residual+0.01196526, below the frozen0.1 penalties, but candidate still loses one answer relative to the parent. Task and keep use the same512 queries, so they are not independent successes. Zero noise means this cell measures clean adaptation/retention, not injected-damage recovery. Only one of ten X2560 cells is complete; matched Adam and independent phi-seed evidence remain incomplete. No architecture selection or conditional extension follows before completion of the full frozen grid.',
        'limitations': audits['X']['limitations'] + [
            'Only the first X2560 cell is complete; the full frozen architecture/dose/task grid remains incomplete, and this cell has zero noise and the same task/keep query pool.',
            'Matching configurations and saved initial evaluations do not independently establish tensor-by-tensor equality of initial adapters.',
            'No architecture selection or conditional extension before completion of the frozen grid.'],
        'evidence_sha256': evidence, 'selection_policy_changed': False,
        'scientific_receipt_generated': False, 'method_validated': False,
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    assessment_path = DIRECTORY / 'thirty_first_cell_and_first_X2560_known_clean_endpoint_assessment.json'
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
