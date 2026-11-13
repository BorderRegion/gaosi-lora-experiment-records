"""Finite dev-only rule revision after the matched three-rate original screen."""
import copy
import json
from pathlib import Path
import subprocess
import time

from development_queue import execute, log


def main():
    boundary = Path('records/lr_review_boundary.json')
    while not boundary.exists():
        time.sleep(30)
    state = json.loads(boundary.read_text())
    if state['last_job_status'] != 'PASS':
        raise RuntimeError('Original LR task failed numerically; inspect before changing difficulty')
    original = []
    for rate in ['3e-05', '0.0001', '0.0003']:
        path = Path(f'runs/capacity_3b_lrdev_rules_lr{rate}_s17_v2/events.jsonl')
        metrics = [json.loads(x) for x in path.read_text().splitlines()]
        original.append([x for x in metrics if x['event'] == 'evaluation'][-1])
    if max(x['candidate_accuracy'] for x in original) >= .5:
        raise RuntimeError('Original rules are above the pilot floor threshold; review instead of changing family')
    dest = Path('data/synthetic/rules_integer_v1')
    if not dest.exists():
        subprocess.run(['/usr/bin/python', 'tools/build_rule_development.py'], check=True)
    log('rule_family_development_pilot', original_metrics=original,
        manifest=str(dest / 'manifest.json'), final_data_used=False)
    template = json.loads(Path('configs/capacity_3b_v2_default.json').read_text())
    template.update(data=str(dest / 'dev_combined.jsonl'), lr=.0003, eval_limit=64)
    result = execute('capacity_3b_lrdev_rules_integer_v1_lr0.0003_s17', copy.deepcopy(template))
    events = [json.loads(x) for x in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
    metrics = [x for x in events if x['event'] == 'evaluation'][-1]
    Path('records/rule_integer_pilot.json').write_text(json.dumps({
        'status': 'PILOT_COMPLETE', 'metrics': metrics, 'result_file': result['config']['out'] + '/result.json',
        'original_rule_metrics': original, 'meta_final_generated': False,
        'next': 'If measurable, freeze revised task generator, match all LR/capacity opportunities and then generate held-out worlds. No method conclusion from pilot.'}, indent=2))


if __name__ == '__main__':
    main()
