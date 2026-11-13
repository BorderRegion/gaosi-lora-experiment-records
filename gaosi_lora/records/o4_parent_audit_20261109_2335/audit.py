"""Audit completed O4 history masters and first acquired current parent on CPU."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile

import torch

ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(2**20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def finite(value):
    if torch.is_tensor(value):
        return all(bool(torch.isfinite(part).all()) for part in value.reshape(-1).split(2**20))
    if isinstance(value, dict):
        return all(finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(item) for item in value)
    return True


def main():
    torch.set_num_threads(2)
    output = HERE / 'audit.json'
    assert not output.exists()
    files = {Path(__file__).resolve()}

    def meta(path):
        files.add(path)
        return read(path)

    build = meta(ROOT / 'records/pipeline_observed_blocking_v8/build_manifest.json')
    for name, expected in build['numerical_source_sha256'].items():
        assert sha(ROOT / name) == expected, name
        files.add(ROOT / name)
    spec = importlib.util.spec_from_file_location('prediction_audit', ROOT / 'records/development_completion_20261104_2358/audit_v2.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    files.add(Path(helper.__file__))
    histories = []
    for letter in ('a', 'b'):
        run = ROOT / f'runs/o4_historical_{letter}_s17'
        job = meta(ROOT / 'records/jobs' / (run.name + '.json'))
        assert job['status'] == 'PASS' and job['exit_code'] == 0
        manifest = meta(run / 'master.pt.json')
        assert manifest['status'] == 'PASS'
        parent_result = meta((ROOT / manifest['parent']).parent / 'result.json')
        assert parent_result['acquisition_gate_passed']
        assert parent_result['config']['data'] == manifest['historical_bindings'] == f'data/synthetic/meta_parent_{letter}.jsonl'
        path = run / 'master.pt'
        files.add(path)
        saved = torch.load(path, map_location='cpu', weights_only=False, mmap=True)
        assert saved['dtype'] == 'float32' and saved['merges'] == 1 and saved['rank_sum'] == 32
        assert saved['base_revision'] == parent_result['config']['base_revision']
        assert all(tensor.dtype == torch.float32 and tensor.device.type == 'cpu' for tensor in saved['weights'].values())
        assert finite(saved['weights'])
        digest = hashlib.sha256()
        for name, tensor in saved['weights'].items():
            digest.update(name.encode())
            digest.update(memoryview(tensor.contiguous().view(torch.uint8).numpy()))
        assert digest.hexdigest() == manifest['master_digest']
        histories.append({'run': str(run.relative_to(ROOT)), 'FP32_master_digest': digest.hexdigest(),
                          'tensor_count': len(saved['weights']), 'parameters': sum(v.numel() for v in saved['weights'].values()),
                          'all_FP32_and_finite': True, 'merges': 1, 'cumulative_rank_upper_bound': 32,
                          'historical_bindings': manifest['historical_bindings'], 'file_bytes': path.stat().st_size})
        files.add(ROOT / 'records/logs' / (run.name + '.log'))
        del saved
    run = ROOT / 'runs/o4_current_b_on_history_a_s17'
    job = meta(ROOT / 'records/jobs' / (run.name + '.json'))
    result = meta(run / 'result.json')
    config = meta(run / 'config.json')
    assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
    assert result['config'] == config == meta(ROOT / 'configs' / (run.name + '.json'))
    assert config['organization'] == 'O4' and config['rank'] == 32 and config['targets'] == 'all7'
    assert config['data'] == 'data/synthetic/meta_parent_b.jsonl'
    assert config['retain_data'] == 'data/synthetic/meta_parent_a.jsonl'
    assert config['historical_base'] == 'runs/o4_historical_a_s17/master.pt'
    path = run / 'latest.pt'
    files.add(path)
    saved = torch.load(path, map_location='cpu', weights_only=False, mmap=True)
    assert saved['config'] == config and saved['step'] == result['steps'] == 58
    assert saved['base_digest'] == result['base_digest']
    assert result['base_unchanged'] and result['acquisition_gate_passed']
    assert saved['initial_keep'] == result['initial_keep']
    assert saved['initial_language'] == result['initial_language']
    assert finite(saved['adapter']) and finite(saved['optimizer'])
    assert len(saved['adapter']) == 504
    for key in ('fact_stream', 'keep_stream'):
        assert saved[key] == result[key] and len(saved[key]['covered']) == 512
    event_path = run / 'events.jsonl'
    files.add(event_path)
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    updates = [event for event in events if event['event'] == 'train']
    assert [event['step'] for event in updates] == list(range(1, 59))
    assert all(event['facts_covered'] <= 512 and event['task_tokens'] >= 512 for event in updates)
    evaluations = [event for event in events if event['event'] == 'evaluation']
    assert [event['step'] for event in evaluations] == [0, 16, 32, 58]
    final = evaluations[-1]
    assert final['candidate_accuracy'] >= .85 and final['strict_EM'] >= .75
    assert final['language']['nll'] <= result['initial_language']['nll'] + .5
    assert final['keep']['nll'] <= result['initial_keep']['nll'] + .5
    assert result['initial_keep']['candidate_accuracy'] >= .85 and result['initial_keep']['strict_EM'] >= .75
    predictions = []
    for route, key in [('task', 'data'), ('keep', 'retain_data')]:
        data = ROOT / config[key]
        files.add(data)
        queries = [row for row in helper.lines(data) if row['role'] == 'query' and
                   (row['sample_id'].endswith(':0') or row['kind'] != 'fact')]
        if route == 'keep':
            path = run / 'initial_keep.jsonl'
            files.add(path)
            predictions.append(helper.metric_review(path, result['initial_keep'], queries))
        for evaluation in evaluations:
            if route == 'keep' and evaluation['step'] == 0:
                continue
            path = run / (f"eval_{evaluation['step']:04d}.jsonl" if route == 'task'
                          else f"keep_{evaluation['step']:04d}.jsonl")
            metrics = evaluation['keep'] if route == 'keep' else {key: evaluation[key] for key in
                      ['n', 'answer_tokens', 'candidate_accuracy', 'strict_EM', 'abnormal_rate', 'mean_margin', 'nll']}
            files.add(path)
            predictions.append(helper.metric_review(path, metrics, queries))
    files.add(ROOT / 'records/logs' / (run.name + '.log'))
    value = {'status': 'O4_FP32_HISTORIES_AND_FIRST_ACQUIRED_PARENT_SAVED_EVIDENCE_AUDITED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'histories': histories, 'current_parent': {'run': str(run.relative_to(ROOT)),
                 'checkpoint_step': saved['step'], 'adapter_and_optimizer_finite': True,
                 'acquisition_gate_independently_recomputed_from_saved_metrics': True,
                 'final_current': {key: final[key] for key in ['n', 'candidate_accuracy', 'strict_EM', 'nll']},
                 'final_keep': final['keep'], 'final_language': final['language']},
             'prediction_reviews': predictions,
             'evidence_sha256': {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)},
             'method_validated': False, 'full_goal_complete': False,
             'limitations': ['CPU saved-tensor audit and saved-prediction reaggregation; no independent model/tokenizer forward.',
                            'FP32 master dtype, finiteness, metadata and digest are checked; merge matrix products are not independently recomputed.',
                            'These are ordinary parent-construction results, not phi specialization or organization efficacy.']}
    with output.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    files.add(output)
    archive = HERE / 'evidence.tar.gz'
    with tarfile.open(archive, 'x:gz') as tar:
        for path in sorted(files):
            if path.suffix != '.pt':
                tar.add(path, arcname='snapshot/' + str(path.relative_to(ROOT)))
    print(json.dumps({'status': value['status'], 'audit_sha256': sha(output),
                      'archive_sha256': sha(archive), 'prediction_rows': sum(item['rows'] for item in predictions),
                      'evidence_files': len(files) - 1}))


if __name__ == '__main__':
    main()
