"""Verify the public record export using Python's standard library only."""
from pathlib import Path
import csv
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def close(left, right):
    assert math.isfinite(left) and math.isfinite(right)
    assert math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12), (left, right)


def predictions(path, expected):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
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
    close(sum(row['rank_correct'] for row in rows) / len(rows), expected['candidate_accuracy'])
    close(sum(row['strict_EM'] for row in rows) / len(rows), expected['strict_EM'])
    close(sum(row['correct_sequence_nll'] for row in rows) / tokens, expected['nll'])
    close(tokens, expected['answer_tokens'])
    return len(rows)


def main():
    checked = 0
    for line in (ROOT/'publication/SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = ROOT/name
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError('Unsafe path in manifest')
        if sha256(path) != expected:
            raise ValueError('Hash mismatch: '+name)
        checked += 1
    provenance = json.loads((ROOT/'publication/provenance.json').read_text())
    for entry in provenance['files']:
        if sha256(ROOT/entry['path']) != entry['published_sha256']:
            raise ValueError('Provenance mismatch: '+entry['path'])
    source = json.loads((ROOT/'gaosi_lora/records/development_aggregate_review_20261109_2058/assessment_verification.json').read_text())
    with (ROOT/'results/development_scores.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(source['results']) == 6
    for row in rows:
        result = source['results'][row['method']]
        assert float(row['frozen_development_score']) == result['score']
        for group in ('known', 'fact', 'rule'):
            assert float(row[group+'_group']) == result['group_scores'][group]
    with (ROOT/'results/development_conditions.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == source['completed_base_cells'] == 60
    expected = [(name, row) for name, result in source['results'].items() for row in result['outcomes']]
    for row, (name, original) in zip(rows, expected):
        assert (row['method'], row['group'], row['dose'], row['run']) == (name, original['group'], original['dose'], original['run'])
        assert float(row['score']) == original['score']
        assert float(row['language_nll_residual']) == original['residuals']['language']
        assert float(row['keep_nll_residual']) == original['residuals']['keep']
    prediction_rows = 0
    for name, result in source['results'].items():
        group_scores = {group: [] for group in ('known', 'fact', 'rule')}
        for outcome in result['outcomes']:
            directory = ROOT/'evidence/critical_snapshot'/outcome['run']
            raw = json.loads((directory/'result.json').read_text())
            assert raw['status'] == 'PASS' and raw['base_unchanged'] is True
            for phase, prefix in (('clean', 'clean'), ('final', 'eval_0064')):
                for route in ('task', 'keep'):
                    prediction_rows += predictions(directory/(prefix+'_'+route+'.jsonl'), raw[phase][route])
            residuals = {route: raw['final'][route]['nll']-raw['clean'][route]['nll'] for route in ('language', 'keep')}
            for route, value in residuals.items():
                close(value, outcome['residuals'][route])
            score = (raw['final']['task']['candidate_accuracy']+raw['final']['task']['strict_EM'])/2
            score -= sum(max(0., value-.1) for value in residuals.values())
            close(score, outcome['score'])
            group_scores[outcome['group']].append(score)
        close(sum(sum(values)/len(values) for values in group_scores.values())/3, result['score'])
    assert prediction_rows == source['saved_prediction_rows_rechecked'] == 75456
    status = json.loads((ROOT/'results/status.json').read_text())
    o2 = json.loads((ROOT/'gaosi_lora/records/expiry_rescue_20261111/O2_completed_recovered/result.json').read_text())
    assert status['method_validated'] is source['method_validated'] is False
    assert status['full_goal_complete'] is source['full_goal_complete'] is False
    assert status['formal_phi_frozen'] is source['policy']['formal_phi_frozen'] is False
    assert o2['status'] == status['O2_specialization']['numeric_status'] == 'PASS'
    assert o2['episodes'] == status['O2_specialization']['episodes'] == 64
    assert o2['inner_updates'] == status['O2_specialization']['inner_updates'] == 512
    assert status['O2_specialization']['full_scientific_audit_complete'] is False
    print(f'PASS: {checked} public file hashes; {len(provenance["files"])} source mappings; 6 scores; 60 development conditions; {prediction_rows} saved predictions; O2 completion and incomplete scientific status.')


if __name__ == '__main__':
    main()
