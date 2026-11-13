"""Finite P02/parent queue. Selection uses development data only.

No test-selected winner, no automatic retry of invalid numerics, no replacement
of requested main models. Every completed configuration and exit is retained.
"""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from storage import preserve_space

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
PYTHON = '/usr/bin/python'
STATE = ROOT / 'records/development_queue.json'


def log(event, **details):
    data = {'utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'event': event, **details}
    with (ROOT / 'records/development_queue.jsonl').open('a') as f:
        f.write(json.dumps(data) + '\n')
    STATE.write_text(json.dumps(data, indent=2))
    print(json.dumps(data), flush=True)


def verified_wait(job):
    state = json.loads(Path(f'records/jobs/{job}.json').read_text())
    while state['status'] == 'RUNNING':
        pid = state['child_pid']
        proc = Path(f'/proc/{pid}/stat')
        live = proc.exists() and proc.read_text().split()[21] == state['child_proc_start_ticks']
        if not live:
            time.sleep(2)
            state = json.loads(Path(f'records/jobs/{job}.json').read_text())
            if state['status'] == 'RUNNING':
                raise RuntimeError(f'Wrapper did not record terminal state for missing child {job}')
            break
        time.sleep(30)
        state = json.loads(Path(f'records/jobs/{job}.json').read_text())
    if state['status'] != 'PASS':
        raise RuntimeError(f'Prerequisite job failed: {job}')


def execute(name, config):
    for event in preserve_space():
        log('storage', **event)
    name = name + '_v2'
    file = Path('configs') / (name + '.json')
    config['out'] = 'runs/' + name
    file.write_text(json.dumps(config, indent=2))
    result_file = Path(config['out']) / 'result.json'
    if result_file.exists():
        result = json.loads(result_file.read_text())
        assert result['config'] == config, f'Existing run differs: {name}'
        return result
    if (Path(config['out']) / 'config.json').exists():
        raise RuntimeError(f'Incomplete run exists; inspect before resuming: {name}')
    log('launch', name=name, config=str(file))
    with Path(f'records/logs/{name}.log').open('ab', buffering=0) as output:
        code = subprocess.call([PYTHON, '-u', 'tools/job.py', '--name', name, '--',
                                PYTHON, '-u', '-m', 'gaosi.train', '--config', str(file)],
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    if code:
        log('FAIL', name=name, exit_code=code)
        raise RuntimeError(f'{name} exited {code}; preserved log, no silent restart')
    result = json.loads(result_file.read_text())
    log('completed', name=name, acquisition=result['acquisition_gate_passed'], steps=result['steps'])
    return result


def dev_score(result):
    events = [json.loads(line) for line in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
    event = [x for x in events if x['event'] == 'evaluation'][-1]
    return event['candidate_accuracy'] + event.get('strict_EM', 0.)


def main():
    log('waiting_prerequisite', job='capacity_3b_v2_default')
    verified_wait('capacity_3b_v2_default')
    template = json.loads(Path('configs/capacity_3b_v2_default.json').read_text())
    template['eval_limit'] = 64
    planfile = Path('records/development_plan_v4.json')
    plan = json.loads(planfile.read_text()) if planfile.exists() else {}
    for architecture in ['L', 'X']:
        for horizon in ['H1', 'EXACT_H2_tail']:
            name = f'profile_3b_phi_{architecture}_{horizon}_v2'
            if Path(f'checks/{name}.json').exists():
                log('profile_preserved', name=name,
                    status=json.loads(Path(f'checks/{name}.json').read_text())['status'])
                continue
            with Path(f'records/logs/{name}.log').open('ab', buffering=0) as output:
                code = subprocess.call([PYTHON, '-u', 'tools/job.py', '--name', name, '--', PYTHON, '-u',
                                        'tools/profile_transition.py', '--checkpoint',
                                        'runs/capacity_3b_all7_r32_lr1e-4_s17_v2/latest.pt',
                                        '--out', f'checks/{name}.json', '--architecture', architecture,
                                        '--horizon', horizon], stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT)
            # H2 failure is preserved; no disguised FO fallback. Ordinary LoRA
            # and H1 work continue, as specified by the numerical contract.
            log('profile_complete', name=name, exit_code=code)
    # These exact spread indices are used by every selection candidate. The
    # Original v1 smoke run had invalid cross-sample context dependence. Every
    # v2 run uses shuffled binding cycles and independent causal sample blocks.
    rules = []
    manifest = json.loads(Path('data/synthetic/manifest.json').read_text())
    for world in manifest['worlds']:
        if world['split'] == 'dev' and world['kind'] != 'fact':
            rules.extend(Path('data/synthetic', world['file']).read_text().splitlines())
    Path('data/synthetic/dev_rules_combined.jsonl').write_text('\n'.join(rules) + '\n')
    rule_file = plan.get('rule_data', 'data/synthetic/dev_rules_combined.jsonl')
    rule_suffix = plan.get('rule_run_suffix', '')
    # 7B occurs before phi selection and regardless of 3B success/failure.
    seven = json.loads(Path('configs/capacity_7b_default.json').read_text())
    execute('capacity_7b_early_all7_r32_lr1e-4_s17', seven)
    rate_results = {}
    for rate in [3e-5, 1e-4, 3e-4]:
        score = 0.
        for task in ['facts', 'rules']:
            c = copy.deepcopy(template)
            c['lr'] = rate
            if task == 'rules':
                c['data'] = rule_file
            name = f'capacity_3b_lrdev_{task}{rule_suffix if task == "rules" else ""}_lr{rate:g}_s17'
            result = execute(name, c)
            rate_results.setdefault(rate, {})[task] = result
            score += dev_score(result)
        log('rate_complete', lr=rate, combined_dev_score=score)
    selected_lr = max(rate_results, key=lambda r: sum(dev_score(v) for v in rate_results[r].values()))
    log('lr_selected', lr=selected_lr, rule='mean of final development candidate accuracy and strict EM over facts and rules')
    results = {('all7', 32): rate_results[selected_lr]}
    for targets, rank in [('all7', 16), ('all7', 64), ('attention4', 32), ('qv', 32)]:
        results[targets, rank] = {}
        for task in ['facts', 'rules']:
            c = copy.deepcopy(template)
            c.update(lr=selected_lr, rank=rank, targets=targets)
            if task == 'rules':
                c['data'] = rule_file
            result = execute(f'capacity_3b_{task}{rule_suffix if task == "rules" else ""}_{targets}_r{rank}_lr{selected_lr:g}_s17', c)
            results[targets, rank][task] = result
    # Equal opportunity for curves that have not converged: all five settings
    # extend to the same budget, at most once to 256 and once to 512. No config
    # is selected because it received more updates or a favorable test slice.
    def final_metrics(result):
        data = [json.loads(x) for x in (Path(result['config']['out']) / 'events.jsonl').read_text().splitlines()]
        return [x for x in data if x['event'] == 'evaluation'][-1]
    for total_steps in [256, 512]:
        default = results['all7', 32]['facts']
        metric = final_metrics(default)
        if metric['candidate_accuracy'] >= .85 and metric.get('strict_EM', 0.) >= .75:
            break
        for (targets, rank), pair in results.items():
            for task, previous in list(pair.items()):
                c = copy.deepcopy(previous['config'])
                c.update(continue_from=previous['checkpoint'], steps=total_steps,
                         eval_steps=[total_steps], purpose='matched_capacity_extension')
                pair[task] = execute(f'capacity_extend{total_steps}_{task}{rule_suffix if task == "rules" else ""}_{targets}_r{rank}_s17', c)
    # Capacity changes require affirmative joint evidence before phi training.
    scores = {f'{t}_r{r}': sum(dev_score(v) for v in pair.values()) / 2
              for (t, r), pair in results.items()}
    selected_rank = 64 if scores['all7_r64'] > scores['all7_r32'] + .1 else 32
    # Same budget for cheap strong baselines; these are development choices.
    for ratio in [4., 16.]:
        for task in ['facts', 'rules']:
            c = copy.deepcopy(template)
            c.update(lr=selected_lr, rank=selected_rank, ba_ratio=ratio)
            if task == 'rules':
                c['data'] = rule_file
            execute(f'factor_lr_3b_{task}{rule_suffix if task == "rules" else ""}_r{selected_rank}_ratio{ratio:g}_s17', c)
    for task in ['facts', 'rules']:
        c = copy.deepcopy(template)
        c.update(lr=selected_lr, rank=selected_rank, scale=selected_rank ** .5)
        if task == 'rules':
            c['data'] = rule_file
        execute(f'rslora_3b_{task}{rule_suffix if task == "rules" else ""}_r{selected_rank}_s17', c)
    selection = {'status': 'DEVELOPMENT_FROZEN', 'rank': selected_rank, 'targets': 'all7',
                 'lr': selected_lr, 'scale': 1., 'capacity_scores': scores,
                 'seed': 17, 'formal_data_used': False,
                 'rule_development_plan': str(planfile) if plan else None,
                 'rule_data': rule_file,
                 'note': 'Rule task floor is reported independently. Parent acquisition gate below is mandatory.'}
    Path('records/development_frozen.json').write_text(json.dumps(selection, indent=2))
    log('development_frozen', **selection)
    parents = []
    for parent in ['meta_parent_a', 'meta_parent_b', 'dev_parent']:
        c = copy.deepcopy(template)
        c.update(lr=selected_lr, rank=selected_rank, data=f'data/synthetic/{parent}.jsonl',
                 eval_limit=512, steps=256, eval_steps=[16, 32, 64, 128, 256],
                 max_epochs=6, purpose='nonzero_parent_acquisition', stop_on_acquisition=True)
        result = execute(f'parent_3b_{parent}_r{selected_rank}_s17', c)
        parents.append(result)
        if not result['acquisition_gate_passed']:
            # A single explicit extension is allowed by the parent-acquisition
            # protocol; its cost and prior unmet gate remain visible.
            extension = copy.deepcopy(c)
            extension.update(continue_from=result['checkpoint'], max_epochs=0, steps=512,
                             eval_steps=[128, 256, 512], purpose='one_parent_acquisition_extension')
            result = execute(f'parent_3b_{parent}_r{selected_rank}_s17_extend512', extension)
            parents[-1] = result
        if not result['acquisition_gate_passed']:
            log('PARENT_ACQUISITION_INCOMPLETE', parent=parent,
                reason='No valid parent: inspect learning/format/language before any phi core. No tiny or unlearned substitute.')
            return 2
    Path('records/parents_ready.json').write_text(json.dumps(parents, indent=2))
    log('DEVELOPMENT_AND_PARENTS_COMPLETE', note='Noise calibration and production H1/H2 profile are next; phi core is not marked complete.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
