"""Independently check the complete, audited 60-cell development aggregate.

This reads saved evidence only. It neither launches training nor writes a
selection artifact used by any production controller. All 60 cells must be
audited and fetched before any score is calculated.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
AUDITS = ROOT / 'records/development_completion_20261104_2358'
LABELS = ['L_2048', 'L_2560', 'X_2048', 'X_2560',
          'standard_adam', 'paired_adam']
DOSES = ['clean', 'medium']
GROUPS = ['known', 'fact', 'rule']


def read(path):
    return json.loads(path.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            digest.update(block)
    return digest.hexdigest()


def close(left, right):
    assert math.isfinite(left) and math.isfinite(right)
    assert math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12), (left, right)


def predictions(path, expected):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == expected['n'] and rows
    assert len({row['sample_id'] for row in rows}) == len(rows)
    for row in rows:
        scores = row['candidate_scores']
        assert scores and all(math.isfinite(value) for value in scores.values())
        assert row['rank_correct'] == int(max(scores, key=scores.get) == row['answer'])
        normalized = lambda text: ' '.join(text.strip().casefold().split())
        assert row['strict_EM'] == int(normalized(row['generation']) == normalized(row['answer']))
        close(row['correct_sequence_nll'], -scores[row['answer']])
        assert row['answer_tokens'] > 0
    tokens = sum(row['answer_tokens'] for row in rows)
    metrics = {
        'n': len(rows), 'answer_tokens': tokens,
        'candidate_accuracy': sum(row['rank_correct'] for row in rows) / len(rows),
        'strict_EM': sum(row['strict_EM'] for row in rows) / len(rows),
        'nll': sum(row['correct_sequence_nll'] for row in rows) / tokens,
    }
    for key, value in metrics.items():
        close(value, expected[key])
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assessment', type=Path, required=True,
                        help='Preserved controller assessment, after 60 cells finish')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    target = (ROOT / args.out).resolve()
    assessment_path = (ROOT / args.assessment).resolve()
    assert target.is_relative_to(HERE) and not target.exists()
    assert assessment_path.is_relative_to(ROOT / 'records')
    names = [f'phi_dev_{label}_t{index}_{dose}_s17'
             for label in LABELS for index in range(5) for dose in DOSES]
    assert len(names) == len(set(names)) == 60
    # This gate precedes all outcome reads and aggregation.
    prerequisites = [AUDITS / (name + '.json') for name in names]
    prerequisites += [AUDITS / ('fetch_receipt_' + name + '.json') for name in names]
    prerequisites += [assessment_path,
        AUDITS / 'phi_dev_paired_adam_t4_medium_s17_matched_endpoint_assessment.json']
    assert all(path.is_file() for path in prerequisites), 'Complete 60-cell review required'
    assert read(prerequisites[-1])['completed_base_cells_at_review'] == 60
    assessment = read(assessment_path)
    references = {str(path.relative_to(ROOT)): sha(path) for path in prerequisites}
    references[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    artifacts = {}
    common_plan = None
    file_count = prediction_rows = 0
    for name in names:
        audit_path = AUDITS / (name + '.json')
        receipt = read(AUDITS / ('fetch_receipt_' + name + '.json'))
        audit = read(audit_path)
        assert receipt['run'] == audit['run'] == 'runs/' + name
        assert receipt['audit_sha256'] == sha(audit_path)
        assert audit['job_status'] == 'PASS'
        assert audit['checkpoint_step'] == audit['completed_recovery_steps'] == 64
        assert audit['checkpoint_parameters_and_inner_state_finite']
        assert audit['source_files_verified'] == 25
        assert audit['evaluation_steps'] == [0, 8, 16, 32, 64]
        archive = ROOT / receipt['archive']['archive']
        assert sha(archive) == receipt['archive']['sha256']
        manifest_path = AUDITS / (name + '_evidence/manifest.json')
        manifest = read(manifest_path)
        references[str(manifest_path.relative_to(ROOT))] = sha(manifest_path)
        snapshot = manifest_path.parent / 'snapshot'
        for relative, expected in manifest['files'].items():
            file = (ROOT / relative).resolve()
            assert file.is_relative_to(snapshot)
            assert sha(file) == expected, relative
            file_count += 1
        plan = read(snapshot / 'records/phi_recovery_development_plan.json')
        if common_plan is None:
            common_plan = plan
        assert plan == common_plan
        result = read(snapshot / ('runs/' + name + '/result.json'))
        config = read(snapshot / ('configs/' + name + '.json'))
        assert result['config'] == config
        assert config['out'] == 'runs/' + name and config['K'] == 64
        assert config['freeze_manifest'] == 'records/phi_recovery_development_plan.json'
        for stage, prefix in [('clean', 'clean'), ('final', 'eval_0064')]:
            assert result[stage] == audit[stage]
            for route in ['task', 'keep']:
                metrics = predictions(snapshot / ('runs/' + name) /
                    (prefix + '_' + route + '.jsonl'), result[stage][route])
                prediction_rows += metrics['n']
        artifacts[name] = result
    assert common_plan['formal_data_used'] is False
    assert common_plan['policy'] == assessment['policy']
    assert [group for group, _ in common_plan['tasks']] == ['known', 'fact', 'fact', 'rule', 'rule']
    assert common_plan['K_candidates'] == [64]
    results = {}
    for label in LABELS:
        outcomes = []
        for index, (group, data) in enumerate(common_plan['tasks']):
            for dose, q in zip(DOSES, common_plan['q_candidates']):
                name = f'phi_dev_{label}_t{index}_{dose}_s17'
                result = artifacts[name]
                config = result['config']
                assert config['data'] == data and config['q_apply'] == q
                assert config['parent'] in common_plan['allowed_parents']
                assert config['inner_lr'] in common_plan['lr_candidates']
                assert config['data_seed'] == 17 and config['noise_seed'] == 1729
                if label in ['standard_adam', 'paired_adam']:
                    assert config['method'] == label and 'phi' not in config
                else:
                    architecture, steps = label.split('_')
                    assert config['method'] == 'phi' and config['architecture'] == architecture
                    assert config['phi'].endswith('/phi_' + steps + '.pt')
                    assert config['phi'] in common_plan['allowed_phis']
                clean, final = result['clean'], result['final']
                accuracy = (final['task']['candidate_accuracy'] + final['task']['strict_EM']) / 2
                residuals = {route: final[route]['nll'] - clean[route]['nll']
                             for route in ['language', 'keep']}
                penalties = {route: max(0., value - .1) for route, value in residuals.items()}
                score = accuracy - penalties['language'] - penalties['keep']
                outcomes.append({'group': group, 'data': data, 'dose': dose,
                    'run': config['out'], 'score': score,
                    'task_mean_candidate_strict': accuracy,
                    'residuals': residuals, 'penalties': penalties})
        groups = {group: [row['score'] for row in outcomes if row['group'] == group]
                  for group in GROUPS}
        aggregate = sum(sum(values) / len(values) for values in groups.values()) / 3
        recorded = assessment['results'][label]
        close(aggregate, recorded['score'])
        assert len(recorded['outcomes']) == len(outcomes) == 10
        for actual, expected in zip(outcomes, recorded['outcomes']):
            for key in ['group', 'data', 'dose', 'run']:
                assert actual[key] == expected[key], (label, key)
            close(actual['score'], expected['score'])
        results[label] = {'score': aggregate,
            'group_scores': {group: sum(values) / len(values) for group, values in groups.items()},
            'outcomes': outcomes}
    improvements = {architecture: results[architecture + '_2560']['score'] -
        results[architecture + '_2048']['score'] for architecture in ['L', 'X']}
    # Match the controller's strict comparison, including floating point order.
    late = any(results[a + '_2560']['score'] > results[a + '_2048']['score'] + .01
               for a in ['L', 'X'])
    assert late == assessment['matched_extension_required']
    selected = None
    if not late:
        selected = max(['L', 'X'], key=lambda a: (results[a + '_2560']['score'], a == 'L'))
        if 'architecture' in assessment:
            assert assessment['architecture'] == selected and assessment['core_inner_steps'] == 2560
    output = {
        'status': 'COMPLETE_BASE_DEVELOPMENT_AGGREGATE_INDEPENDENTLY_VERIFIED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'completed_base_cells': 60, 'non_tensor_evidence_files_rehashed': file_count,
        'saved_prediction_rows_rechecked': prediction_rows,
        'policy': common_plan['policy'], 'results': results,
        'late_score_improvements': improvements, 'matched_extension_required': late,
        'selected_architecture_if_no_extension': selected,
        'evidence_sha256': references,
        'limitations': [
            'Saved evidence audit only; no independent model forward, tokenizer, derivative or language per-token recomputation.',
            'Development selection from one phi training seed; no formal test or independent-seed efficacy conclusion.',
            'Known task shares its query pool with keep; rule tasks have four queries within the support range.',
            'A selected architecture still requires organization adaptation and matched phi_clean before formal freezing.',
        ],
        'training_modified_or_restarted': False, 'method_validated': False,
        'full_goal_complete': False,
    }
    with target.open('x') as handle:
        json.dump(output, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': output['status'], 'receipt_sha256': sha(target),
        'scores': {key: value['score'] for key, value in results.items()},
        'late_score_improvements': improvements, 'matched_extension_required': late,
        'selected_architecture_if_no_extension': selected}))


if __name__ == '__main__':
    main()
