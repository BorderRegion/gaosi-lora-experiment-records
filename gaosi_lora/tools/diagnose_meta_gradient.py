"""Replay a recorded readiness/core episode without changing training.

BF16 directional differences are a scale/sensitivity diagnostic, not a smooth
FP64 derivative proof. This extra run never updates phi or uses formal data.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from gaosi.data import FactStream, LanguageStream, route_batches, rows
from gaosi.meta import (QueryStream, functional_loss, rollout,
                        support_gradients, terminal_objective)
from gaosi.model import AdapterModel, memory, synchronize
from gaosi.noise import canonical_noise
from gaosi.runtime import Run, atomic_save, restore_rng, seed_all
from gaosi.transition import (Phi, apply_prepared, detach_tree, leaf_parameters,
                              prepare, scalar_stats, zero_state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] != 'musa':
        raise ValueError('This diagnosis requires the actual MUSA backend')
    import torch_musa
    torch.set_num_threads(8)
    source_file = Path(config['source_config'])
    if hashlib.sha256(source_file.read_bytes()).hexdigest() != config['source_config_sha256']:
        raise ValueError('Readiness configuration changed')
    source = json.loads(source_file.read_text())
    episode = config.get('episode', 0)
    if (source['selection_data'] != 'development_only'
            or source['architecture'] != 'L' or source['organization'] != 'O1'
            or episode not in range(len(source['episode_K']))
            or any('final' in name for name in source['parent_data'])):
        raise ValueError('Diagnosis is restricted to recorded L development episodes')
    if 'initial_phi' not in config and episode != 0:
        raise ValueError('A later episode requires its exact saved phi state')
    run = Run(config['out'], config)
    seed_all(source['seed'])
    model = AdapterModel(source['model'], source['rank'], source['targets'],
                         source['organization'], 'musa', scale=source['scale'])
    base_digest = model.base_digest()
    phi = Phi('L').to('musa')
    if 'initial_phi' in config:
        checkpoint = Path(config['initial_phi'])
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != config['initial_phi_sha256']:
            raise ValueError('Replay checkpoint changed')
        state = torch.load(checkpoint, map_location='cpu', weights_only=False)
        if state['next_episode'] != episode or state['config'] != source:
            raise ValueError('Replay checkpoint does not match the source episode/configuration')
        phi.load_state_dict(state['phi'])
        restore_rng(state['rng'])
    run.log('model', **model.summary())
    original = {k: p.detach().clone() for k, p in phi.named_parameters()}
    parent_index = episode % len(source['parents'])
    parent = torch.load(source['parents'][parent_index], map_location='cpu', weights_only=False)['adapter']
    episode_seed = source['seed'] * 100000 + episode
    K = source['episode_K'][episode]
    q = source['q_cycle'][episode % len(source['q_cycle'])]
    clean, _ = canonical_noise(parent, model.specs, 0., episode_seed)
    model.load_adapter(clean)
    stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
    noisy, _ = canonical_noise(parent, model.specs, q, episode_seed)
    initial = {k: v.to('musa') for k, v in noisy.items()}
    task_file = source['parent_data'][parent_index]
    if episode >= source['known_warm_episodes'] and episode % 2:
        raise ValueError('This diagnostic only replays recorded known-task episodes')
    queries = {}
    for key in ['task', 'keep']:
        stream = QueryStream(rows(task_file, 'query'), model.tokenizer,
                             seed=episode_seed, seq=source['seq'])
        queries[key], _ = route_batches(stream, 'musa', source['effective_tokens'])
    stream = LanguageStream('data/language/meta_query.jsonl', model.tokenizer,
                            source['seq'], episode_seed)
    queries['language'], _ = route_batches(stream, 'musa', source['effective_tokens'])
    with torch.no_grad():
        reference = {key: functional_loss(model, {}, batches).detach()
                     for key, batches in queries.items()}

    def streams():
        return {
            'task': FactStream(rows(task_file, 'support'), model.tokenizer,
                               episode_seed, source['seq']),
            'language': LanguageStream('data/language/inner.jsonl', model.tokenizer,
                                       source['seq'], episode_seed),
            'keep': FactStream(rows(task_file, 'support'), model.tokenizer,
                               episode_seed, source['seq']),
        }

    started = time.time()
    phi.zero_grad(set_to_none=True)
    _, _, analytic_diagnostics = rollout(
        model, initial, zero_state(initial), phi, streams(), stats, K, q,
        source['inner_lr'], source['effective_tokens'], source['retain_weight'],
        'EXACT_H2_tail', queries, reference, source['penalty'], source['chunk'],
        lambda **kw: run.log('analytic_inner', **kw))
    synchronize()
    gradients = {k: (p.grad.detach().clone() if p.grad is not None else torch.zeros_like(p))
                 for k, p in phi.named_parameters()}
    # Move only the finished gradients for diagnostics; the actual LLM
    # forward/HVP and transition remain on MUSA with unchanged precision.
    cpu_gradients = {k: g.cpu() for k, g in gradients.items()}
    top = {k: {'finite': bool(torch.isfinite(g).all()),
               'nonfinite_elements': int((~torch.isfinite(g)).sum()),
               'norm': float(g.double().norm()) if bool(torch.isfinite(g).all()) else None,
               'max_abs': float(g.abs().max()) if bool(torch.isfinite(g).all()) else None}
           for k, g in cpu_gradients.items()}
    all_finite = all(v['finite'] for v in top.values())
    norm = (math.sqrt(sum(float(g.double().square().sum()) for g in cpu_gradients.values()))
            if all_finite else None)
    atomic_save({'phi': {k: v.cpu() for k, v in original.items()},
                 'gradient': cpu_gradients, 'config': config,
                 'analytic_terminal': analytic_diagnostics}, run.path / 'analytic.pt')
    run.log('analytic_gradient', raw_norm_fp64_reduction=norm, all_finite=all_finite,
            episode=episode, K=K, q_train=q, parameters=top,
            **analytic_diagnostics,
            seconds=time.time() - started)
    if not all_finite or not norm or not config['epsilons']:
        run.result(status='PASS', diagnosis_executed=True, method_validated=False,
                   actual_device='musa:0', episode=episode, K=K, q_train=q,
                   all_gradients_finite=all_finite, raw_gradient_norm=norm,
                   gradient_parameters=top, analytic_terminal=analytic_diagnostics,
                   compute_counters=model.compute_counters,
                   scope='Real MUSA episode replay with raw gradients saved before clipping; no phi update')
        return
    direction = {k: g / norm for k, g in gradients.items()}

    def numerical_objective():
        work = leaf_parameters(initial)
        state = zero_state(work)
        support = streams()
        for _ in range(K):
            task, _ = route_batches(support['task'], 'musa', source['effective_tokens'])
            language, _ = route_batches(support['language'], 'musa', source['effective_tokens'])
            keep, _ = route_batches(support['keep'], 'musa', source['effective_tokens'])
            ld, gd = support_gradients(model, work, task)
            ll, gl = support_gradients(model, work, language)
            lk, gk = support_gradients(model, work, keep)
            lr = .5 * ll + .5 * lk
            gr = {k: .5 * gl[k] + .5 * gk[k] for k in work}
            with torch.no_grad():
                prepared, diag = prepare(work, gd, gr, state, model.specs, stats,
                                         ld, lr, K, q, source['retain_weight'],
                                         layers=model.llm.config.num_hidden_layers)
                work, state = apply_prepared(prepared, phi, source['inner_lr'],
                                             diag['step'], source['chunk'])
            work = leaf_parameters(work)
        with torch.no_grad():
            value, diagnostics = terminal_objective(model, work, queries, reference,
                                                    penalty=source['penalty'])
        return float(value.cpu()), diagnostics

    measurements = []
    for epsilon in [0., *config['epsilons']]:
        values = []
        for sign in ([0] if epsilon == 0. else [1, -1]):
            with torch.no_grad():
                for name, parameter in phi.named_parameters():
                    parameter.copy_(original[name] + sign * epsilon * direction[name])
            value, diagnostics = numerical_objective()
            values.append(value)
            run.log('directional_objective', epsilon=epsilon, sign=sign, **diagnostics)
        row = {'epsilon': epsilon, 'values': values}
        if epsilon:
            finite = (values[0] - values[1]) / (2 * epsilon)
            row.update(central_difference=finite, analytic_directional=norm,
                       relative_discrepancy=abs(norm - finite) / max(abs(norm), abs(finite), 1e-30))
        measurements.append(row)
    assert model.base_digest() == base_digest
    run.result(status='PASS', diagnosis_executed=True, method_validated=False,
               actual_device='musa:0', raw_gradient_norm=norm, measurements=measurements,
               analytic_terminal=analytic_diagnostics,
               baseline_objective_absolute_difference=abs(
                   measurements[0]['values'][0] - analytic_diagnostics['objective']),
               base_unchanged=True, compute_counters=model.compute_counters, memory=memory(),
               scope='Additional real 3B derivative sensitivity diagnosis; no phi updates',
               limitations=['BF16 rounding makes finite differences noisy; this is not a smooth FP64 derivative proof.',
                            'Execution PASS does not resolve the large gradient or validate training efficacy.',
                            'The source episode and any loaded starting phi are identified in the immutable config.'])


if __name__ == '__main__':
    main()
