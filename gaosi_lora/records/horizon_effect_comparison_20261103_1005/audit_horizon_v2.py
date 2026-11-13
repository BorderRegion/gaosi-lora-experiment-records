"""CPU audit of one completed supplementary H1 development recovery; no training or approval."""
import argparse
from noise_metadata_v2 import noise_pair
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

import torch


ROOT = Path('/root/autodl-tmp/gaosi_lora')


def read(path):
    return json.loads(path.read_text())


def lines(path):
    raw = path.read_bytes()
    assert not raw or raw.endswith(b'\n'), 'Completed evidence has a partial final line'
    return [json.loads(line) for line in raw.splitlines()]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def finite(value):
    if torch.is_tensor(value):
        assert value.device.type == 'cpu'
        return bool(torch.isfinite(value).all())
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(v) for v in value)
    return not isinstance(value, float) or math.isfinite(value)


def close(left, right):
    assert math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12), (left, right)


def normalize(text):
    return ' '.join(text.strip().casefold().split())


def metric_review(path, expected, queries):
    predictions = lines(path)
    assert len(predictions) == len(queries) == expected['n']
    assert [r['sample_id'] for r in predictions] == [r['sample_id'] for r in queries]
    for row, query in zip(predictions, queries):
        assert all(row[key] == query.get(key) for key in ['answer', 'world_id', 'fact_id'])
        scores = row['candidate_scores']
        assert list(scores) == query['candidates'] and finite(row)
        best = max(query['candidates'], key=scores.__getitem__)
        assert row['rank_correct'] == int(best == query['answer'])
        close(row['correct_sequence_nll'], -scores[query['answer']])
        close(row['margin'], scores[query['answer']] - max(v for k, v in scores.items() if k != query['answer']))
        assert row['answer_tokens'] > 0
        text = row['generation']
        assert row['strict_EM'] == int(normalize(text) == normalize(query['answer']))
        assert row['abnormal'] == int(not text.strip() or len(text.split()) > 8)
    tokens = sum(r['answer_tokens'] for r in predictions)
    recomputed = {
        'n': len(predictions), 'answer_tokens': tokens,
        'candidate_accuracy': sum(r['rank_correct'] for r in predictions) / len(predictions),
        'strict_EM': sum(r['strict_EM'] for r in predictions) / len(predictions),
        'abnormal_rate': sum(r['abnormal'] for r in predictions) / len(predictions),
        'mean_margin': sum(r['margin'] for r in predictions) / len(predictions),
        'nll': sum(r['correct_sequence_nll'] for r in predictions) / tokens}
    assert set(recomputed) == set(expected)
    for key, value in recomputed.items():
        close(value, expected[key])
    return {'file': str(path.relative_to(ROOT)), 'rows': len(predictions),
            'metrics_recomputed_from_saved_predictions': recomputed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    run = ROOT / args.run
    assert run.parent == ROOT / 'runs'
    assert re.fullmatch(r'phi_horizon_L_H1_2560_t[0-4]_(?:clean|medium)_s17_v1', run.name)
    out = (ROOT / args.out).resolve()
    assert out.is_relative_to(ROOT / 'records') and not out.exists()
    evidence = set()

    def metadata(path):
        evidence.add(path)
        return read(path)

    jobpath = ROOT / 'records/jobs' / (run.name + '.json')
    job = metadata(jobpath)
    assert job['status'] == 'PASS' and job['exit_code'] == 0
    config = metadata(run / 'config.json')
    assert config == metadata(ROOT / 'configs' / (run.name + '.json'))
    assert config['out'] == args.run and config['phase'] == 'phi_development'
    assert config['K'] == 64 and config['data_seed'] == 17 and config['noise_seed'] == 1729
    declared_path = ROOT / 'records/horizon_effect_comparison_20261103_1005/declared_pairs.json'
    declared = metadata(declared_path)
    pairs = [p for p in declared if p['H1']['out'] == args.run]
    assert len(pairs) == 1 and config == pairs[0]['H1']
    pair = pairs[0]
    assert config['method'] == 'phi' and config['architecture'] == 'L'
    assert config['initial_adapter'] == pair['H2']['out'] + '/initial.pt'
    reference = metadata(ROOT / pair['H2']['out'] / 'result.json')
    assert reference['status'] == 'PASS' and reference['config'] == pair['H2']
    assert metadata(ROOT / 'records/jobs' / (Path(pair['H2']['out']).name + '.json'))['status'] == 'PASS'
    result = metadata(run / 'result.json')
    assert finite(result) and result['status'] == 'PASS' and result['config'] == config
    assert result['base_unchanged'] and result['phi_frozen']
    assert result['clean'] == reference['clean'] and result['initial'] == reference['initial']
    assert result['base_digest'] == reference['base_digest']
    paired_noise_review = noise_pair(result['noise'], reference['noise'])
    assert result['coverage'] == result['pool'] and result['keep_coverage']['covered'] == result['keep_coverage']['pool']
    plan = metadata(ROOT / config['freeze_manifest'])
    assert plan['status'] == 'PHI_DEVELOPMENT_PLAN' and not plan['formal_data_used']
    assert config['parent'] in plan['allowed_parents'] and config['K'] in plan['K_candidates']
    assert config['inner_lr'] in plan['lr_candidates'] and config['q_apply'] in plan['q_candidates']
    assert config['noise_scheme'] in plan['noise_schemes']
    for key in ['data', 'retain_data']:
        assert config[key] in plan['allowed_data'] and 'dev' in config[key] and 'final' not in config[key]
    sources = metadata(ROOT / 'records/pipeline_observed_blocking_v8/build_manifest.json')['numerical_source_sha256']
    assert metadata(run / 'source_sha256.json') == {Path(k).name: v for k, v in sources.items()}
    for name, expected in sources.items():
        for path in [ROOT / name, run / 'source' / Path(name).name]:
            assert sha(path) == expected
            evidence.add(path)
    parentpath = ROOT / config['parent']
    parent = torch.load(parentpath, map_location='cpu', weights_only=False)
    evidence.add(parentpath)
    parent_result = metadata(parentpath.parent / 'result.json')
    assert parent_result['acquisition_gate_passed'] and finite(parent['adapter'])
    initialpath = ROOT / config.get('initial_adapter', args.run + '/initial.pt')
    initial = torch.load(initialpath, map_location='cpu', weights_only=False)
    evidence.add(initialpath)
    assert all(initial[k] == config[k] for k in ['parent', 'q_apply', 'noise_seed', 'noise_scheme'])
    assert initial['organization'] == config.get('organization', parent['config']['organization'])
    assert finite(initial['adapter'])
    shared_initial_noise_review = noise_pair(initial['corruption'], result['noise'])
    saved = torch.load(run / 'latest.pt', map_location='cpu', weights_only=False)
    evidence.add(run / 'latest.pt')
    assert saved['step'] == config['K'] and saved['config'] == config
    assert saved['initial_evaluation'] == result['initial']
    assert saved['base_digest'] == result['base_digest'] == parent['base_digest']
    assert saved['base_revision'] == parent['config']['base_revision']
    assert finite(saved['parameters']) and finite(saved['inner_state']) and finite(saved['standard_optimizer'])
    assert saved['parameters'] and set(saved['parameters']) <= set(initial['adapter'])
    assert all(v.shape == initial['adapter'][k].shape for k, v in saved['parameters'].items())
    phi_check = None
    if config['method'] == 'phi':
        phipath = ROOT / config['phi']
        digest = sha(phipath)
        assert digest == plan['allowed_phis'][config['phi']]
        phi = torch.load(phipath, map_location='cpu', weights_only=False)
        assert finite(phi['phi'])
        evidence.add(phipath)
        phi_check = {'sha256': digest, 'input_phi_tensors_finite': True}
    else:
        assert config['method'] in ['standard_adam', 'paired_adam']
    events = lines(run / 'events.jsonl')
    evidence.add(run / 'events.jsonl')
    updates = [e for e in events if e['event'] == 'recover']
    evaluations = [e for e in events if e['event'] == 'evaluation']
    clean = [e for e in events if e['event'] == 'clean_evaluation']
    assert finite(events) and [e['step'] for e in updates] == list(range(1, 65))
    assert [e['step'] for e in evaluations] == [0, 8, 16, 32, 64]
    assert len(clean) == 1 and clean[0]['metrics'] == result['clean']
    assert evaluations[0]['metrics'] == result['initial'] and evaluations[-1]['metrics'] == result['final']
    assert all(set(e['route_tokens']) == {'task', 'language', 'keep'} for e in updates)
    assert all(v >= config['effective_tokens'] for e in updates for v in e['route_tokens'].values())
    before, after = updates[-1]['compute_counters'], result['compute_counters']
    assert set(before) == set(after)
    final_evaluation_counter_delta = {k: after[k] - before[k] for k in before}
    assert all(v >= 0 for v in final_evaluation_counter_delta.values())
    assert final_evaluation_counter_delta['loss_backwards'] == 0
    assert final_evaluation_counter_delta['hessian_vector_products'] == 0
    assert final_evaluation_counter_delta['supervised_tokens'] == result['final']['language']['answer_tokens']
    assert final_evaluation_counter_delta['input_tokens'] >= final_evaluation_counter_delta['supervised_tokens']
    assert final_evaluation_counter_delta['loss_forwards'] > 0
    assert set(saved['streams']) == {'task', 'language', 'keep'}
    predictions = []
    for part, key in [('task', 'data'), ('keep', 'retain_data')]:
        datapath = ROOT / config[key]
        evidence.add(datapath)
        queries = [r for r in lines(datapath) if r['role'] == 'query' and
                   (r['sample_id'].endswith(':0') or r['kind'] != 'fact')]
        assert len({r['sample_id'] for r in queries}) == len(queries)
        for label, metrics in [('clean', result['clean']), *[(f"eval_{e['step']:04d}", e['metrics']) for e in evaluations]]:
            path = run / f'{label}_{part}.jsonl'
            evidence.add(path)
            predictions.append(metric_review(path, metrics[part], queries))
    for part, changes in result['loss_changes'].items():
        a, b, c = [result[k][part]['nll'] for k in ['clean', 'initial', 'final']]
        for key, expected in [('injected', b-a), ('recovered', b-c), ('residual', c-a)]:
            close(changes[key], expected)
    if config['q_apply'] == 0:
        assert result['clean'] == result['initial']
    evidence.add(Path(__file__).resolve())
    evidence.add(Path(__file__).resolve().with_name('noise_metadata_v2.py'))
    audit = {
        'status': 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'run': args.run,
        'job_status': job['status'], 'job_wall_seconds': job['wall_seconds'],
        'method': config['method'], 'q_apply': config['q_apply'],
        'completed_recovery_steps': 64, 'evaluation_steps': [0, 8, 16, 32, 64],
        'checkpoint_step': saved['step'], 'checkpoint_parameters_and_inner_state_finite': True,
        'input_phi': phi_check, 'source_files_verified': len(sources),
        'minimum_effective_tokens': {k: min(e['route_tokens'][k] for e in updates) for k in ['task', 'language', 'keep']},
        'prediction_reviews': predictions,
        'clean': result['clean'], 'initial': result['initial'], 'final': result['final'],
        'loss_changes': result['loss_changes'], 'coverage': result['coverage'], 'pool': result['pool'],
        'keep_coverage': result['keep_coverage'],
        'final_language_evaluation_counter_delta': final_evaluation_counter_delta,
        'mean_recovery_step_seconds': statistics.mean(e['step_seconds'] for e in updates),
        'noise_timing_reviews': {'paired_results': paired_noise_review, 'shared_initial': shared_initial_noise_review},
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sorted(evidence)},
        'method_validated': False, 'scientific_promotion_allowed': False, 'full_goal_complete': False,
        'training_modified_or_restarted': False,
        'limitations': ['CPU inspection of saved tensors and re-aggregation of saved predictions only; no model forward, tokenizer or derivative recomputation.',
                       'Language NLL is checked against the event ledger; no saved per-token language audit.',
                       'Model compute counters omit direct QA candidate/generation calls; they are not total evaluation FLOPs or total model forwards.',
                       'Frozen phi/base flags plus pinned source and file identities are provenance evidence, not an independent in-memory mutation trace.',
                       'No paired-method efficacy, held-out generalization, convergence or multi-seed conclusion from one completed development cell.']}
    with out.open('x') as handle:
        handle.write(json.dumps(audit, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': audit['status'], 'run': args.run, 'out': str(out.relative_to(ROOT)),
                      'sha256': sha(out), 'evidence_files': len(evidence),
                      'prediction_files': len(predictions), 'prediction_rows': sum(p['rows'] for p in predictions)}))


if __name__ == '__main__':
    main()
