"""Describe the selected development checkpoint's local signals from audited evidence.

This is a descriptive comparison, with no selection, significance test, training,
scientific promotion, or new model evaluation.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ASSESSMENT = ROOT / 'records/development_aggregate_review_20261109_2058/assessment_verification.json'
EXPECTED = 'a3c814c81ddc7b3fdd33641132c2b8dc1a9b6b6f0c04949071e3c226f2052218'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    output = HERE / 'descriptive_comparison.json'
    assert not output.exists()
    assert sha(ASSESSMENT) == EXPECTED
    assessment = read(ASSESSMENT)
    assert assessment['status'] == 'COMPLETE_BASE_DEVELOPMENT_AGGREGATE_INDEPENDENTLY_VERIFIED'
    selected = assessment['results']['X_2560']
    baselines = ['standard_adam', 'paired_adam']
    assert len(selected['outcomes']) == 10
    comparisons = {}
    for name in baselines:
        baseline = assessment['results'][name]
        assert len(baseline['outcomes']) == 10
        counts = {route: {'lower': 0, 'equal': 0, 'higher': 0}
                  for route in ['language', 'keep']}
        for phi, adam in zip(selected['outcomes'], baseline['outcomes']):
            assert all(phi[key] == adam[key] for key in ['group', 'data', 'dose'])
            for route in counts:
                difference = phi['residuals'][route] - adam['residuals'][route]
                counts[route]['lower' if difference < 0 else 'higher' if difference > 0 else 'equal'] += 1
        assert counts['language'] == {'lower': 10, 'equal': 0, 'higher': 0}
        assert counts['keep'] == {'lower': 1, 'equal': 0, 'higher': 9}
        comparisons[name] = {'phi_minus_baseline_score': selected['score'] - baseline['score'],
                             'phi_NLL_residual_comparison_counts': counts}
    evidence = {str(ASSESSMENT.relative_to(ROOT)): sha(ASSESSMENT),
                str(Path(__file__).relative_to(ROOT)): sha(Path(__file__))}
    example = {}
    common_config = None
    common_clean = None
    for name in ['X_2560', *baselines]:
        run = 'phi_dev_' + name + '_t4_clean_s17'
        folder = ROOT / 'records/development_completion_20261104_2358' / (run + '_evidence')
        manifest_path = folder / 'manifest.json'
        manifest_key = str(manifest_path.relative_to(ROOT))
        assert sha(manifest_path) == assessment['evidence_sha256'][manifest_key]
        evidence[manifest_key] = sha(manifest_path)
        result_path = folder / 'snapshot/runs' / run / 'result.json'
        result_key = str(result_path.relative_to(ROOT))
        assert sha(result_path) == read(manifest_path)['files'][result_key]
        evidence[result_key] = sha(result_path)
        result = read(result_path)
        assert result['status'] == 'PASS'
        config = {key: result['config'][key] for key in
                  ['data', 'parent', 'inner_lr', 'q_apply', 'K', 'data_seed', 'noise_seed']}
        assert config['q_apply'] == 0 and config['K'] == 64
        if common_config is None:
            common_config, common_clean = config, result['clean']
        assert config == common_config and result['clean'] == common_clean
        assert result['final']['task']['n'] == 4
        assert result['final']['keep']['n'] == 512
        assert result['final']['task']['candidate_accuracy'] == result['final']['task']['strict_EM'] == 1.
        example[name] = {'task': result['final']['task'], 'keep': result['final']['keep'],
                         'language': result['final']['language']}
    value = {
        'status': 'SELECTED_DEVELOPMENT_CHECKPOINT_DESCRIPTIVE_SIGNALS_RECORDED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'selected_checkpoint_label': 'X_2560', 'conditions_per_method': 10,
        'comparisons': comparisons,
        'local_retention_example': {'config': common_config, 'clean': common_clean, 'final': example},
        'interpretation': 'Language NLL residual is lower in all ten conditions; keep NLL residual is lower in one and higher in nine. Neither composite comparison favors phi.',
        'evidence_sha256': evidence,
        'limitations': [
            'One phi training seed, development data only; conditions are not ten independent training replicates.',
            'Descriptive signs do not establish statistical significance, causal mechanisms, or generalization.',
            'The rule example has only four task queries within the support range.',
            'Saved predictions and language event metrics are reused; no fresh model forward or per-token language audit.',
            'This comparison concerns the core checkpoint before organization specialization, not the pending organization result.'
        ],
        'training_modified_or_restarted': False,
        'scientific_promotion_generated': False,
        'method_validated': False,
        'full_goal_complete': False,
    }
    with output.open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': value['status'], 'receipt': str(output.relative_to(ROOT)),
                      'receipt_sha256': sha(output), 'comparisons': comparisons}))


if __name__ == '__main__':
    main()
