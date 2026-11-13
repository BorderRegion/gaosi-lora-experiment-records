"""Twelve fixed replays, no training, on two development trajectories."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from gaosi.horizon import gradient_comparison
from gaosi.scale import sha256
from organization_queue import wait_for
from phi_core_queue import run_job
from phi_development_queue import preserve_json


POLICY = {
    'version': 1,
    'purpose': 'Few full H4/H8 development trajectories versus H1/H2 terminal gradients at the same K and numerical state.',
    'task_selection': 'Known development parent plus first fact development world sorted by world_id.',
    'phi': 'Development-selected mixed core checkpoint, unchanged; no phi or adapter optimization is retained.',
    'K': [4, 8], 'tails_for_each_K': '1,2,K',
    'q': 'Frozen development medium dose; one shared corruption per task/K replay.',
    'data_seed': 1701, 'query_seed': 1702, 'noise_seed': 1729,
    'support': 'Production task + .25*(half inner language, half development known facts), three routes each512 effective tokens.',
    'query': 'One fixed512-effective-token batch group per task/keep/dev-language route. Positive terminal task CE plus language/keep residual above .1; not a full512-binding recovery benchmark.',
    'features': 'Same declared total K across tails; all adapter, moment and continuous-history dependencies remain within the exact tail.',
    'metrics': 'Full terminal state/sample-flow equality, objective, phi gradient cosine and relative L2 error against complete H4/H8, wall/peak memory/compute.',
    'limitation': 'Record explicit MUSA OOM or unavailable operator; no sequence/rank/token reduction, no silent H1 substitute. Unknown failure stops for diagnosis.',
    'training_settings_selected_from_this_diagnostic': False,
    'formal_data_used': False,
}


def main():
    preserve_json('records/horizon_policy_v1.json', POLICY)
    for path in ['checks/horizon_cpu.json', 'checks/horizon_upstream_cpu.json']:
        if json.loads(Path(path).read_text())['status'] != 'PASS':
            raise RuntimeError('Independent H4/H8 derivative checks must pass')
    selected = wait_for('records/phi_architecture_selected.json', 'phi_development_queue_observed_blocking_v8')
    calibration = json.loads(Path('records/calibration_frozen.json').read_text())
    if calibration['formal_data_used'] or not calibration['ordinary_recovery_validated']:
        raise ValueError('Horizon requires development-only valid calibration')
    parents = json.loads(Path('records/parents_ready.json').read_text())
    parent = next(p for p in parents if p['config']['data'] == 'data/synthetic/dev_parent.jsonl')
    worlds = json.loads(Path(calibration['world_manifest']).read_text())['worlds']
    world = sorted([w for w in worlds if w['split'] == 'dev' and w['kind'] == 'fact'],
                   key=lambda w: w['world_id'])[0]
    tasks = [('known', parent['config']['data']), ('newfact', str(Path('data/synthetic') / world['file']))]
    planfile = 'records/horizon_plan_v1.json'
    configs = []
    for task, data in tasks:
        for K in [4, 8]:
            reference = f'runs/horizon_{task}_K{K}_H1_s17_observed_blocking_v8/endpoint.pt'
            for tail in [1, 2, K]:
                c = {'horizon_manifest': planfile, 'out': f'runs/horizon_{task}_K{K}_H{tail}_s17_observed_blocking_v8',
                     'data': data, 'retain_data': parent['config']['data'],
                     'language_support': 'data/language/inner.jsonl',
                     'language_query': 'data/language/dev.jsonl',
                     'parent': parent['checkpoint'], 'phi': selected['checkpoint'],
                     'architecture': selected['architecture'], 'device': 'musa',
                     'K': K, 'tail': tail, 'q': calibration['q_medium'],
                     'inner_lr': calibration['recovery_lr'], 'seq': 512, 'effective_tokens': 512,
                     'retain_weight': .25, 'penalty': 1., 'chunk': 262144,
                     'data_seed': POLICY['data_seed'], 'query_seed': POLICY['query_seed'],
                     'noise_seed': POLICY['noise_seed'],
                     'reference_endpoint': reference if tail != 1 else None}
                configs.append(c)
    artifacts = ['records/phi_architecture_selected.json', 'records/calibration_frozen.json',
                 parent['checkpoint'], selected['checkpoint']]
    datafiles = [p for _, p in tasks] + ['data/language/inner.jsonl', 'data/language/dev.jsonl',
                                        calibration['world_manifest']]
    plan = {'status': 'FROZEN_DEVELOPMENT_HORIZON_PLAN', 'policy': POLICY,
            'formal_data_used': False, 'configs': configs,
            'artifact_sha256': {p: sha256(p) for p in artifacts},
            'data_sha256': {p: sha256(p) for p in datafiles}}
    preserve_json(planfile, plan)
    outcomes = []
    for c in configs:
        file = f"configs/{Path(c['out']).name}.json"
        preserve_json(file, c)
        result = run_job(Path(c['out']).name,
                         ['/usr/bin/python', '-u', '-m', 'gaosi.horizon', '--config', file],
                         Path(c['out']) / 'result.json')
        if result['config'] != c:
            raise ValueError('Preserved horizon result has different settings')
        if c['tail'] == 1 and result['diagnostic_status'] != 'COMPLETE':
            raise RuntimeError('Missing numerical H1 reference; inspect before other tails')
        outcomes.append(result)
    import torch
    comparisons = []
    for task, _ in tasks:
        for K in [4, 8]:
            group = [r for r in outcomes if f'horizon_{task}_K{K}_' in r['config']['out']]
            complete = next(r for r in group if r['config']['tail'] == K)
            if complete['diagnostic_status'] != 'COMPLETE':
                comparisons.append({'task': task, 'K': K, 'status': complete['diagnostic_status'],
                                    'full_derivative_available': False})
                continue
            full = torch.load(Path(complete['config']['out']) / 'gradient.pt',
                              map_location='cpu', weights_only=False)['gradient']
            for result in group:
                if result['diagnostic_status'] != 'COMPLETE':
                    continue
                grad = torch.load(Path(result['config']['out']) / 'gradient.pt',
                                  map_location='cpu', weights_only=False)['gradient']
                comparisons.append({'task': task, 'K': K, 'tail': result['config']['tail'],
                                    'status': 'COMPLETE', **gradient_comparison(grad, full)})
    preserve_json('records/horizon_ready_v1.json',
                  {'status': 'DEVELOPMENT_HORIZON_DIAGNOSTIC_REPLAYS_FINISHED',
                   'all_full_derivatives_available': all(r['diagnostic_status'] == 'COMPLETE' for r in outcomes),
                   'plan': plan, 'outcomes': outcomes, 'comparisons': comparisons,
                   'formal_data_used': False, 'training_horizon_changed': False})


if __name__ == '__main__':
    main()
