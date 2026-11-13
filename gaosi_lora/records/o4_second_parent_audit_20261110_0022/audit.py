"""Audit the second acquired O4 current parent, including any authorized continuation.

Reuse the completed FP32 history audit; inspect this new checkpoint and saved QA
predictions on CPU. No model forward, training, or history reconstruction runs.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tarfile


ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = Path(__file__).resolve().parent
BASE = 'runs/o4_current_a_on_history_b_s17'
PREVIOUS = 'records/o4_parent_audit_20261109_2335/audit.json'
PREVIOUS_SHA = 'd8f662cab8b29b1452db304a2e514ab072f73d696ab40f7e8378c3048ae55bed'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', choices=[BASE, BASE + '_extend512'], default=BASE)
    args = parser.parse_args()
    import torch
    torch.set_num_threads(2)
    output = HERE / 'audit.json'
    assert not output.exists()
    files = {Path(__file__).resolve()}

    def read(path):
        files.add(path)
        return json.loads(path.read_text())

    def finite(value):
        if torch.is_tensor(value):
            assert value.device.type == 'cpu'
            return all(bool(torch.isfinite(part).all()) for part in value.reshape(-1).split(2**20))
        if isinstance(value, dict):
            return all(finite(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return all(finite(item) for item in value)
        return not isinstance(value, float) or math.isfinite(value)

    assert sha(ROOT / PREVIOUS) == PREVIOUS_SHA
    previous = read(ROOT / PREVIOUS)
    assert previous['status'] == 'O4_FP32_HISTORIES_AND_FIRST_ACQUIRED_PARENT_SAVED_EVIDENCE_AUDITED'
    history = 'runs/o4_historical_b_s17/master.pt'
    for name in (history, history + '.json'):
        files.add(ROOT / name)
        assert sha(ROOT / name) == previous['evidence_sha256'][name]
    build = read(ROOT / 'records/pipeline_observed_blocking_v8/build_manifest.json')
    for name, expected in build['numerical_source_sha256'].items():
        files.add(ROOT / name)
        assert sha(ROOT / name) == expected, name
    helper_path = ROOT / 'records/development_completion_20261104_2358/audit_v2.py'
    assert sha(helper_path) == previous['evidence_sha256'][str(helper_path.relative_to(ROOT))]
    files.add(helper_path)
    spec = importlib.util.spec_from_file_location('prediction_audit', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    runs = [ROOT / BASE] + ([ROOT / args.run] if args.run != BASE else [])
    all_updates, evaluations, configs, results = [], [], [], []
    predictions = []
    for run in runs:
        job = read(ROOT / 'records/jobs' / (run.name + '.json'))
        config = read(run / 'config.json')
        result = read(run / 'result.json')
        assert job['status'] == result['status'] == 'PASS' and job['exit_code'] == 0
        assert result['config'] == config == read(ROOT / 'configs' / (run.name + '.json'))
        assert config['out'] == str(run.relative_to(ROOT)) and result['base_unchanged']
        assert config['organization'] == 'O4' and config['rank'] == 32 and config['targets'] == 'all7'
        assert config['data'] == 'data/synthetic/meta_parent_a.jsonl'
        assert config['retain_data'] == 'data/synthetic/meta_parent_b.jsonl'
        assert config['historical_base'] == history
        source = read(run / 'source_sha256.json')
        assert source == {Path(k).name: v for k, v in build['numerical_source_sha256'].items()}
        for name, expected in source.items():
            path = run / 'source' / name
            files.add(path)
            assert sha(path) == expected
        events_path = run / 'events.jsonl'
        files.add(events_path)
        events = helper.lines(events_path)
        assert finite(events)
        updates = [e for e in events if e['event'] == 'train']
        start = results[-1]['steps'] if results else 0
        assert [e['step'] for e in updates] == list(range(start + 1, result['steps'] + 1))
        assert all(e['task_tokens'] >= 512 and e['facts_covered'] <= 512 for e in updates)
        all_updates += updates
        ev = [e for e in events if e['event'] == 'evaluation']
        assert ev and ev[-1]['step'] == result['steps']
        assert all(e['step'] in config['eval_steps'] or e['step'] in (0, result['steps']) for e in ev)
        for route, key in [('task', 'data'), ('keep', 'retain_data')]:
            data = ROOT / config[key]
            files.add(data)
            queries = [r for r in helper.lines(data) if r['role'] == 'query' and
                       (r['sample_id'].endswith(':0') or r['kind'] != 'fact')]
            assert len(queries) == len({r['sample_id'] for r in queries}) == 512
            if route == 'keep' and run == runs[0]:
                path = run / 'initial_keep.jsonl'
                files.add(path)
                predictions.append(helper.metric_review(path, result['initial_keep'], queries))
            for evaluation in ev:
                if route == 'keep' and evaluation['step'] == 0:
                    continue
                path = run / (f"eval_{evaluation['step']:04d}.jsonl" if route == 'task'
                              else f"keep_{evaluation['step']:04d}.jsonl")
                metrics = evaluation['keep'] if route == 'keep' else {key: evaluation[key] for key in
                          ['n', 'answer_tokens', 'candidate_accuracy', 'strict_EM', 'abnormal_rate', 'mean_margin', 'nll']}
                files.add(path)
                predictions.append(helper.metric_review(path, metrics, queries))
        files.add(ROOT / 'records/logs' / (run.name + '.log'))
        evaluations += ev
        configs.append(config)
        results.append(result)
    if len(runs) == 2:
        assert not results[0]['acquisition_gate_passed']
        assert configs[1]['continue_from'] == results[0]['checkpoint'] == BASE + '/latest.pt'
        assert configs[0]['max_epochs'] == 6 and configs[1]['max_epochs'] == 0
        assert configs[1]['steps'] == 512
        for key in ('initial_language', 'initial_keep', 'base_digest'):
            assert results[0][key] == results[1][key]
        for key in configs[0]:
            if key not in ('out', 'continue_from', 'max_epochs', 'steps', 'eval_steps', 'purpose'):
                assert configs[0][key] == configs[1][key], key
    config, result, final = configs[-1], results[-1], evaluations[-1]
    checkpoint = runs[-1] / 'latest.pt'
    files.add(checkpoint)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=False, mmap=True)
    assert saved['config'] == config and saved['step'] == result['steps']
    assert saved['base_digest'] == result['base_digest']
    assert saved['base_revision'] == config['base_revision']
    assert saved['initial_keep'] == result['initial_keep']
    assert saved['initial_language'] == result['initial_language']
    assert len(saved['adapter']) == 504 and finite(saved['adapter']) and finite(saved['optimizer'])
    for key in ('fact_stream', 'keep_stream'):
        assert saved[key] == result[key] and len(saved[key]['covered']) == 512
    gate = (final['candidate_accuracy'] >= .85 and final['strict_EM'] >= .75
            and final['language']['nll'] <= result['initial_language']['nll'] + .5
            and final['keep']['nll'] <= result['initial_keep']['nll'] + .5)
    assert gate and result['acquisition_gate_passed']
    assert result['initial_keep']['candidate_accuracy'] >= .85 and result['initial_keep']['strict_EM'] >= .75
    assert [e['step'] for e in all_updates] == list(range(1, result['steps'] + 1))
    value = {'status': 'O4_SECOND_ACQUIRED_PARENT_SAVED_EVIDENCE_INDEPENDENTLY_AUDITED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'run': args.run, 'checkpoint_step': saved['step'], 'continuation_used': len(runs) == 2,
             'previous_FP32_history_audit': PREVIOUS, 'previous_FP32_history_audit_sha256': PREVIOUS_SHA,
             'historical_master_bytes_match_previous_audit': True,
             'adapter_and_optimizer_finite': True, 'all_512_current_and_keep_bindings_covered': True,
             'acquisition_gate_recomputed_from_saved_metrics': gate,
             'final_current': {key: final[key] for key in ['n', 'candidate_accuracy', 'strict_EM', 'nll']},
             'final_keep': final['keep'], 'final_language': final['language'],
             'prediction_reviews': predictions,
             'evidence_sha256': {str(path.relative_to(ROOT)): sha(path) for path in sorted(files)},
             'method_validated': False, 'full_goal_complete': False,
             'limitations': ['CPU checkpoint inspection and saved-prediction reaggregation; no independent model/tokenizer forward.',
                            'Previously audited historical FP32 master bytes are rehashed, not remerged or retensor-audited.',
                            'Ordinary O4 parent construction does not establish phi specialization or organization efficacy.']}
    with output.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    files.add(output)
    with tarfile.open(HERE / 'evidence.tar.gz', 'x:gz') as tar:
        for path in sorted(files):
            if path.suffix != '.pt':
                tar.add(path, arcname='snapshot/' + str(path.relative_to(ROOT)))
    print(json.dumps({'status': value['status'], 'audit_sha256': sha(output),
                      'archive_sha256': sha(HERE / 'evidence.tar.gz'),
                      'prediction_rows': sum(item['rows'] for item in predictions),
                      'non_tensor_files': sum(path.suffix != '.pt' for path in files)}))


if __name__ == '__main__':
    main()
