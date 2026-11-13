"""Development-only functional noise window; preserve all doses and controls."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
import torch_musa
from gaosi.model import AdapterModel
from gaosi.data import rows, LanguageStream
from gaosi.evaluate import evaluate_language, evaluate_qa
from gaosi.noise import canonical_noise
from gaosi.runtime import Run, seed_all


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--parent', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--plan', help='Recorded development-only extension of the original dose grid')
    a = p.parse_args()
    torch.set_num_threads(8)
    seed_all(17)
    checkpoint = torch.load(a.parent, map_location='cpu', weights_only=False)
    c = checkpoint['config']
    result = json.loads((Path(a.parent).parent / 'result.json').read_text())
    if not result['acquisition_gate_passed']:
        raise ValueError('Cannot calibrate recovery damage on an unlearned adapter')
    if 'dev_parent' not in c['data']:
        raise ValueError('Noise doses must be selected on development parent, not final facts')
    config = {'parent': a.parent, 'out': a.out, 'noise_seed': 1729,
              'q_grid': [0, .0001, .0003, .001, .003, .01, .03, .1, .3],
              'data': c['data'], 'language_pool': 'dev',
              'selection_rule': 'Two development doses causing measurable known-knowledge loss, subject to full ordinary recovery validation before formal freezing'}
    previous = None
    if a.plan:
        plan = json.loads(Path(a.plan).read_text())
        if (plan['status'] != 'DOSE_EXTENSION_PLAN' or plan['parent'] != a.parent
                or plan.get('formal_data_used') is not False):
            raise ValueError('Dose extension must name this development parent before evaluation')
        previous = json.loads(Path(plan['previous_result']).read_text())
        if (previous['status'] != 'CALIBRATION_INCOMPLETE'
                or previous['config']['parent'] != a.parent
                or previous['config']['noise_seed'] != config['noise_seed']):
            raise ValueError('Preserved previous scan does not match the planned parent/noise')
        extra = plan['additional_q_grid']
        if any(not isinstance(q, (int, float)) or not 0 < q <= 1 for q in extra):
            raise ValueError('N-sub q must lie in (0, 1]')
        config.update(q_grid=sorted(set(config['q_grid'] + extra)),
                      extension_plan=a.plan, previous_result=plan['previous_result'])
    run = Run(a.out, config)
    model = AdapterModel(c['model'], c['rank'], c['targets'], 'O1', 'musa')
    query = [r for r in rows(c['data'], 'query') if r['sample_id'].endswith(':0')]
    language = LanguageStream('data/language/dev.jsonl', model.tokenizer, 512, 991)
    metrics = []
    cached = {m['q']: m for m in previous['curves']} if previous else {}
    for q in config['q_grid']:
        if q in cached:
            metrics.append(cached[q])
            run.log('dose_reused', previous_result=config['previous_result'], **cached[q])
            continue
        noisy, corruption = canonical_noise(checkpoint['adapter'], model.specs, q, config['noise_seed'])
        model.load_adapter(noisy)
        qa = evaluate_qa(model, query, run.path / f'q{q:g}_examples.jsonl')
        lang = evaluate_language(model, language, 4096)
        row = {'q': q, 'scheme': 'subspace', 'qa': qa, 'language': lang,
               'noise': {k: v for k, v in corruption.items() if k != 'factors'}}
        run.log('dose', **row)
        metrics.append(row)
    clean = metrics[0]
    nonzero = [m for m in metrics[1:] if clean['qa']['candidate_accuracy'] - m['qa']['candidate_accuracy'] >= .015625
               or clean['qa']['strict_EM'] - m['qa']['strict_EM'] >= .015625]
    if not nonzero:
        run.result(status='CALIBRATION_INCOMPLETE', config=config, curves=metrics,
                   reason='No measurable functional damage in predeclared q grid; do not pretend Gaussian recovery is being tested')
        return 2
    def damage(m):
        return clean['qa']['candidate_accuracy'] - m['qa']['candidate_accuracy']
    light = min(nonzero, key=lambda m: abs(damage(m) - .05))
    medium = min(nonzero, key=lambda m: abs(damage(m) - .20))
    # Same-seed factor, shrink, and same-variance uniform controls. Different
    # schemes are not asserted to have matched functional doses.
    controls = []
    for scheme in ['factor', 'shrink', 'uniform']:
        state, corruption = canonical_noise(checkpoint['adapter'], model.specs, medium['q'], config['noise_seed'], scheme)
        model.load_adapter(state)
        row = {'q': medium['q'], 'scheme': scheme,
               'qa': evaluate_qa(model, query, run.path / f'control_{scheme}.jsonl'),
               'language': evaluate_language(model, language, 4096),
               'noise': {k: v for k, v in corruption.items() if k != 'factors'}}
        run.log('control', **row)
        controls.append(row)
    run.result(status='CANDIDATE_WINDOW', config=config, curves=metrics, controls=controls,
               light_q=light['q'], medium_q=medium['q'],
               formal_frozen=False, ordinary_recovery_validated=False,
               note='These doses require full ordinary recovery with dev-frozen endpoint before phi core/final use. Fixed-update norm matching remains a separate required control.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
