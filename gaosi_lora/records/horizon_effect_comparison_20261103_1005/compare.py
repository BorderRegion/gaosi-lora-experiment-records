"""Prepare, check, or run a separate dev-only common-warm horizon comparison.

No controller, training configuration, numerical source or review is modified.
Run from the project root. 'prepare' freezes inputs without reading outcomes;
'run' exits with WAITING until independently reviewed endpoints and all ten
reusable H2 recoveries exist. It never retries an incomplete numerical job.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
H2 = 'runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
H1 = 'runs/phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8'
REVIEWS = Path('records/pipeline_observed_blocking_v8/reviews')
LOCK = Path('/tmp/gaosi_musa_device0.lock')
LOCK_INODE = 31148027670


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def preserve(path, value):
    path = Path(path)
    if path.exists():
        require(read(path) == value, 'Preserved declaration differs: ' + str(path))
        return
    with path.open('x') as f:
        f.write(json.dumps(value, indent=2, allow_nan=False) + '\n')


def select_tasks(parents, worlds):
    candidates = [p for p in parents if p['config']['data'].endswith('dev_parent.jsonl')]
    require(len(candidates) == 1 and candidates[0]['acquisition_gate_passed'], 'Need one acquired dev parent')
    parent = candidates[0]
    tasks = [{'group': 'known', 'data': parent['config']['data']}]
    for kind, group in [('fact', 'fact'), ('affine_integer', 'rule')]:
        chosen = sorted([w for w in worlds if w['split'] == 'dev' and w['kind'] == kind],
                        key=lambda w: w['world_id'])[:2]
        require(len(chosen) == 2, 'Need two dev worlds of each kind')
        tasks += [{'group': group, 'data': str(Path('data/synthetic') / w['file'])} for w in chosen]
    require(all('dev' in t['data'] and 'final' not in t['data'] for t in tasks), 'Non-dev data')
    return parent, tasks


def template(inputs):
    return {'phase': 'phi_development', 'data_seed': 17, 'noise_seed': 1729,
            'parent': inputs['parent'], 'device': 'musa', 'q_apply': 0., 'noise_scheme': 'subspace',
            'architecture': 'L', 'method': 'phi', 'data': '', 'target_ids': [],
            'task_layout': 'all', 'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
            'retain_data': inputs['retain_data'], 'language_eval': 'data/language/dev.jsonl',
            'language_eval_tokens': 4096, 'inner_lr': inputs['recovery_lr'], 'effective_tokens': 512,
            'retain_weight': .25, 'K': 64, 'eval_steps': [8, 16, 32, 64]}


def pairs(inputs):
    result = []
    for index, task in enumerate(inputs['tasks']):
        for dose, q in [('clean', 0.), ('medium', inputs['q_medium'])]:
            h2name = f'phi_dev_L_2560_t{index}_{dose}_s17'
            h1name = f'phi_horizon_L_H1_2560_t{index}_{dose}_s17_v1'
            c2 = dict(template(inputs), out='runs/' + h2name, data=task['data'], q_apply=q,
                      phi=H2 + '/phi_2560.pt', freeze_manifest='records/phi_recovery_development_plan.json')
            c1 = dict(c2, out='runs/' + h1name, phi=H1 + '/phi_2560.pt',
                      freeze_manifest=str((HERE / 'plan.json').relative_to(ROOT)),
                      initial_adapter=c2['out'] + '/initial.pt')
            result.append({'group': task['group'], 'dose': dose, 'H2': c2, 'H1': c1})
    return result


def prepare():
    existing = HERE / 'inputs.json'
    if existing.exists():
        verify_inputs(read(existing))
        return read(existing)
    calibration = read('records/calibration_frozen.json')
    require(calibration['status'] == 'CALIBRATION_FROZEN' and not calibration['formal_data_used'], 'Need frozen dev calibration')
    parent, tasks = select_tasks(read('records/parents_ready.json'), read(calibration['world_manifest'])['worlds'])
    require(LOCK.stat().st_ino == LOCK_INODE, 'Original GPU lock identity changed')
    source = read('records/pipeline_observed_blocking_v8/build_manifest.json')['numerical_source_sha256']
    for name, expected in source.items():
        require(sha(name) == expected, 'Pinned numerical source changed: ' + name)
    files = set(source) | {str(Path(__file__).relative_to(ROOT)),
        str((HERE / 'policy.json').relative_to(ROOT)), 'records/calibration_frozen.json',
        'records/parents_ready.json', calibration['world_manifest'], parent['checkpoint'],
        str(Path(parent['checkpoint']).parent / 'result.json'), 'data/language/inner.jsonl',
        'data/language/dev.jsonl', 'configs/' + Path(H2).name + '.json',
        'tools/guarded_pipeline.py', 'tools/train_guarded_meta.py', 'tools/device_guard.py',
        'tools/job.py', 'tools/storage.py'} | {t['data'] for t in tasks}
    # Bind tokenizer/config bytes as well as the parent's recorded base revision.
    model = Path(parent['config']['model'])
    for glob in ['*.json', '*.txt', '*.model']:
        files.update(str(p) for p in model.glob(glob) if p.is_file())
    inputs = {'status': 'FROZEN_BEFORE_HORIZON_EFFECT_OUTCOMES',
              'prepared_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'policy': read(HERE / 'policy.json'), 'parent': parent['checkpoint'],
              'retain_data': parent['config']['data'], 'tasks': tasks,
              'recovery_lr': calibration['recovery_lr'], 'q_medium': calibration['q_medium'],
              'device_lock': {'path': str(LOCK), 'inode': LOCK_INODE},
              'source_sha256': source, 'inputs_sha256': {p: sha(p) for p in sorted(files)},
              'formal_data_used': False, 'method_validated': False}
    preserve(existing, inputs)
    preserve(HERE / 'declared_pairs.json', pairs(inputs))
    return inputs


def verify_inputs(inputs):
    require(inputs['policy'] == read(HERE / 'policy.json'), 'Policy changed')
    require(Path(inputs['device_lock']['path']).stat().st_ino == inputs['device_lock']['inode'], 'Original GPU lock identity changed')
    for name, expected in inputs['inputs_sha256'].items():
        require(sha(name) == expected, 'Frozen source/input changed: ' + name)


def validate_endpoint_metadata(c2, c1, r2, r1):
    require(c2['architecture'] == 'L' and c2['horizon'] == 'EXACT_H2_tail', 'Need L-H2 reference')
    require(c2['episode_K'] == [8] * 64 + [16] * 128 and not c2.get('start_episode', 0), 'H2 budget changed')
    expected = dict(c2, out=H1, horizon='H1', start_episode=64,
                    initial_phi=H2 + '/phi_0512.pt', load_initial_meta_optimizer=True,
                    purpose='common_512_warm_alternate_H1')
    require(c1 == expected, 'H1 differs beyond predeclared training horizon/continuation fields')
    for result, horizon in [(r2, 'EXACT_H2_tail'), (r1, 'H1')]:
        require(result['status'] == 'PASS' and result['episodes'] == 192
                and result['inner_updates'] == 2560 and result['horizon'] == horizon, 'Endpoint incomplete or unmatched budget')


def endpoint_gate():
    from guarded_pipeline import verify_review
    import torch
    c2, c1 = [read('configs/' + Path(run).name + '.json') for run in [H2, H1]]
    r2, r1 = [read(run + '/result.json') for run in [H2, H1]]
    validate_endpoint_metadata(c2, c1, r2, r1)
    evidence = {}
    for run, config in [(H2, c2), (H1, c1)]:
        job = 'records/jobs/' + Path(run).name + '.json'
        require(read(job)['status'] == 'PASS', 'Endpoint wrapper incomplete')
        receipt = REVIEWS / (Path(run).name + '.json')
        review = verify_review(run, receipt)
        snapshot = Path(run) / 'phi_2560.pt'
        require(str(snapshot) in read(review['artifact_audit'])['evidence_sha256'], 'Endpoint snapshot not audited')
        saved = torch.load(snapshot, map_location='cpu', weights_only=False)
        require(saved['config'] == config and saved['inner_updates'] == 2560
                and saved['next_episode'] == 192, 'Endpoint snapshot/config mismatch')
        evidence.update({str(p): sha(p) for p in [Path(job), receipt, Path(run) / 'result.json', snapshot]})
    warm = Path(H2) / 'phi_0512.pt'
    saved = torch.load(warm, map_location='cpu', weights_only=False)
    require(saved['inner_updates'] == 512 and saved['next_episode'] == 64
            and saved['config'] == c2 and bool(saved['meta_optimizer']['state']), 'Missing common warm optimizer state')
    # The independent H1 artifact audit binds its initial checkpoint bytes.
    audit = read(read(REVIEWS / (Path(H1).name + '.json'))['artifact_audit'])
    require(audit['evidence_sha256'].get(str(warm)) == sha(warm), 'H1 audit does not bind common warm checkpoint')
    evidence[str(warm)] = sha(warm)
    return evidence


def validate_result(config, inputs):
    run = Path(config['out'])
    result = read(run / 'result.json')
    require(result['status'] == 'PASS' and result['config'] == config
            and read(run / 'config.json') == config, 'Recovery config/status mismatch')
    require(read('records/jobs/' + run.name + '.json')['status'] == 'PASS', 'Recovery wrapper incomplete')
    require(result['base_unchanged'] and result['phi_frozen'], 'Unfrozen recovery state')
    require(result['coverage'] == result['pool'] and result['keep_coverage']['covered'] == result['keep_coverage']['pool'], 'Incomplete data coverage')
    expected_sources = {Path(k).name: v for k, v in inputs['source_sha256'].items()}
    require(read(run / 'source_sha256.json') == expected_sources, 'Recovery source differs')
    for filename, expected in expected_sources.items():
        require(sha(run / 'source' / filename) == expected, 'Recovery saved source changed')
    plan = read(config['freeze_manifest'])
    require(plan['status'] == 'PHI_DEVELOPMENT_PLAN'
            and plan['allowed_phis'].get(config['phi']) == sha(config['phi']), 'Recovery phi is outside its frozen plan')
    events = [json.loads(s) for s in (run / 'events.jsonl').read_text().splitlines()]
    evaluations = [e for e in events if e['event'] == 'evaluation']
    require([e['step'] for e in evaluations] == [0, 8, 16, 32, 64], 'Missing/duplicate recovery evaluations')
    steps = [e for e in events if e['event'] == 'recover']
    require([e['step'] for e in steps] == list(range(1, 65)), 'Incomplete/duplicate recovery updates')
    require(evaluations[-1]['metrics'] == result['final'], 'Final result differs from ledger')
    return result, evaluations, steps


def score(metrics, clean):
    value = .5 * (metrics['task']['candidate_accuracy'] + metrics['task']['strict_EM'])
    for part in ['language', 'keep']:
        value -= max(0., metrics[part]['nll'] - clean[part]['nll'] - .1)
    require(math.isfinite(value), 'Nonfinite score')
    return value


def tensor_digest(adapter):
    import torch
    h = hashlib.sha256()
    for name, value in sorted(adapter.items()):
        value = value.detach().cpu().contiguous()
        h.update(json.dumps([name, str(value.dtype), list(value.shape)]).encode())
        h.update(value.view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def training_cost():
    ledgers = {h: [json.loads(s) for s in Path(run, 'events.jsonl').read_text().splitlines()]
               for h, run in [('H2', H2), ('H1', H1)]}
    warm = [e for e in ledgers['H2'] if e['event'] == 'episode_end' and e['episode'] == 63]
    require(len(warm) == 1, 'Missing common warm cost boundary')
    warm = warm[0]['compute_counters']
    totals = {h: read(run + '/result.json')['compute_counters'] for h, run in [('H2', H2), ('H1', H1)]}
    require(set(warm) == set(totals['H2']), 'Unmatched training cost counters')
    return {'shared_warm_512': warm, 'H2_additional_2048': {k: v - warm[k] for k, v in totals['H2'].items()},
            'H1_additional_2048': totals['H1'], 'H2_full_2560': totals['H2'],
            'wrapper_wall_seconds': {h: read('records/jobs/' + Path(run).name + '.json')['wall_seconds']
                                     for h, run in [('H2', H2), ('H1', H1)]},
            'wall_time_note': 'H2 wrapper includes warm; H1 wrapper excludes warm. Wrapper time also includes lock waiting and audits, not pure compute.'}


def report_pair(pair, inputs):
    import torch
    r2, e2, s2 = validate_result(pair['H2'], inputs)
    r1, e1, s1 = validate_result(pair['H1'], inputs)
    initial = pair['H1']['initial_adapter']
    require(initial == pair['H2']['out'] + '/initial.pt', 'Unpaired initial adapter')
    initial_state = torch.load(initial, map_location='cpu', weights_only=False)
    for key in ['parent', 'q_apply', 'noise_seed', 'noise_scheme']:
        require(initial_state[key] == pair['H1'][key], 'Initial state metadata differs')
    saved = [torch.load(c['out'] + '/latest.pt', map_location='cpu', weights_only=False)
             for c in [pair['H2'], pair['H1']]]
    require(all(v['step'] == 64 and v['config'] == c for v, c in zip(saved, [pair['H2'], pair['H1']])), 'Recovery checkpoint incomplete')
    require(saved[0]['streams'] == saved[1]['streams'], 'Different terminal data streams')
    require([e['route_tokens'] for e in s2] == [e['route_tokens'] for e in s1], 'Different per-step token budgets')
    require(r2['base_digest'] == r1['base_digest'] and r2['noise'] == r1['noise'], 'Parent/noise differs')
    require(r2['initial'] == r1['initial'] and r2['clean'] == r1['clean'], 'Initial/clean evaluations differ; inspect pairing before inference')
    curves = []
    for a, b in zip(e2, e1):
        scores = [score(v['metrics'], r['clean']) for v, r in [(a, r2), (b, r1)]]
        curves.append({'step': a['step'], 'H2': a['metrics'], 'H1': b['metrics'],
                       'H2_score': scores[0], 'H1_score': scores[1], 'H2_minus_H1': scores[0] - scores[1]})
    evidence = {}
    for c in [pair['H2'], pair['H1']]:
        for name in ['result.json', 'config.json', 'events.jsonl', 'latest.pt', 'source_sha256.json']:
            p = c['out'] + '/' + name
            evidence[p] = sha(p)
    evidence[initial] = sha(initial)
    return {'group': pair['group'], 'dose': pair['dose'], 'runs': [pair['H2']['out'], pair['H1']['out']],
            'initial_adapter_tensor_sha256': tensor_digest(initial_state['adapter']),
            'matched_final_streams': saved[0]['streams'], 'curves': curves, 'evidence_sha256': evidence,
            'cost': {h: {'recovery_step_seconds': sum(s['step_seconds'] for s in steps),
                         'compute_counters': result['compute_counters'],
                         'wrapper_wall_seconds': read('records/jobs/' + Path(c['out']).name + '.json')['wall_seconds']}
                     for h, c, result, steps in [('H2', pair['H2'], r2, s2), ('H1', pair['H1'], r1, s1)]}}


def run(inputs, check_only=False):
    verify_inputs(inputs)
    declared = pairs(inputs)
    require(read(HERE / 'declared_pairs.json') == declared, 'Declared task configs changed')
    needed = [Path(run) / filename for run in [H1, H2] for filename in ['result.json', 'phi_2560.pt']]
    needed += [REVIEWS / (Path(run).name + '.json') for run in [H1, H2]]
    needed += [Path(p['H2']['out']) / 'result.json' for p in declared]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        return {'status': 'WAITING_FOR_REVIEWED_ENDPOINTS_AND_EXISTING_H2_DEV', 'missing': missing,
                'training_started_or_modified': False, 'method_validated': False}
    endpoint_evidence = endpoint_gate()
    for pair in declared:
        validate_result(pair['H2'], inputs)
    plan = {'status': 'PHI_DEVELOPMENT_PLAN', 'policy': inputs['policy'],
            'allowed_data': sorted({t['data'] for t in inputs['tasks']}),
            'allowed_parents': [inputs['parent']],
            'allowed_phis': {H1 + '/phi_2560.pt': sha(H1 + '/phi_2560.pt')},
            'lr_candidates': [inputs['recovery_lr']], 'K_candidates': [64],
            'q_candidates': [0., inputs['q_medium']], 'noise_schemes': ['subspace'],
            'endpoint_evidence_sha256': endpoint_evidence, 'inputs_sha256': sha(HERE / 'inputs.json'),
            'formal_data_used': False}
    if check_only:
        return {'status': 'READY_NOT_STARTED', 'pairs': len(declared), 'method_validated': False}
    preserve(HERE / 'plan.json', plan)
    from guarded_pipeline import run_job
    reports = []
    for pair in declared:
        verify_inputs(inputs)
        for name, expected in endpoint_evidence.items():
            require(sha(name) == expected, 'Reviewed endpoint changed')
        config = pair['H1']
        name = Path(config['out']).name
        file = Path('configs') / (name + '.json')
        preserve(file, config)
        run_job(name, ['/usr/bin/python', '-u', '-m', 'gaosi.recover', '--config', str(file)],
                Path(config['out']) / 'result.json')
        reports.append(report_pair(pair, inputs))
        preserve(HERE / (name + '_paired_review.json'), reports[-1])
    aggregates = []
    for index, step in enumerate([0, 8, 16, 32, 64]):
        groups = {g: [p['curves'][index]['H2_minus_H1'] for p in reports if p['group'] == g]
                  for g in ['known', 'fact', 'rule']}
        means = {g: sum(values) / len(values) for g, values in groups.items()}
        aggregates.append({'step': step, 'group_H2_minus_H1': means,
                           'equal_group_H2_minus_H1': sum(means.values()) / 3})
    result = {'status': 'COMMON_WARM_HORIZON_DEV_COMPARISON_COMPLETE_REQUIRES_INTERPRETATION',
              'policy': inputs['policy'], 'endpoint_evidence_sha256': endpoint_evidence,
              'inputs_sha256': sha(HERE / 'inputs.json'), 'pairs': reports, 'aggregates': aggregates,
              'training_cost': training_cost(),
              'limitations': ['One training seed and a small development sample; no confirmatory significance claim.',
                              'Equal inner updates; H2 costs more derivative compute. Training costs include the H2 common warm only once.',
                              'Exact initial/clean metric equality is a strict gate; any mismatch requires inspection, never relaxed silently.',
                              'H2 differentiates the last two steps at a fixed earlier boundary; not the whole episode.',
                              'This supplement never alters the existing architecture or formal phi selection.'],
              'formal_data_used': False, 'method_validated': False}
    preserve(HERE / 'comparison.json', result)
    return {'status': result['status'], 'pairs': len(reports), 'output': str(HERE / 'comparison.json'), 'method_validated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'run'])
    args = parser.parse_args()
    require(Path.cwd().resolve() == ROOT, 'Run from project root')
    if args.action == 'prepare':
        inputs = prepare()
        result = {'status': inputs['status'], 'pairs': len(pairs(inputs)), 'inputs_sha256': sha(HERE / 'inputs.json'), 'training_started_or_modified': False}
    else:
        result = run(read(HERE / 'inputs.json'), check_only=args.action == 'check')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
