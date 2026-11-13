"""Terminal-only phi learning with explicit H1 or exact two-step tail.

The same support transition is used for a frozen phi. Prefix values and all
moment/history boundary state are fixed before the differentiable tail.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import time

import torch
from torch.func import functional_call

from .data import rows, FactStream, LanguageStream, route_batches, token_count
from .model import AdapterModel, memory, synchronize
from .noise import canonical_noise, split_parent_single_to_dual
from .runtime import Run, atomic_save, restore_rng, rng_state, seed_all
from .recompute import recomputed_loss, recomputed_support
from .transition import (Phi, zero_state, scalar_stats, prepare, apply_prepared,
                          detach_tree, leaf_parameters, h1_replay)


def functional_loss(model, parameters, batches):
    if parameters and torch.is_grad_enabled():
        return recomputed_loss(model, parameters, batches)
    total = sum(token_count(b) for b in batches)
    result = 0.
    for batch in batches:
        result = result + functional_call(model, parameters, (batch,), strict=False) * token_count(batch) / total
    return result


def support_gradients(model, parameters, batches, create_graph=False):
    if create_graph:
        return recomputed_support(model, parameters, batches)
    total = sum(token_count(b) for b in batches)
    mean = 0.
    gradients = {name: torch.zeros_like(p) for name, p in parameters.items()}
    for batch in batches:
        fraction = token_count(batch) / total
        loss = functional_call(model, parameters, (batch,), strict=False) * fraction
        values = torch.autograd.grad(loss, tuple(parameters.values()), create_graph=create_graph)
        for name, g in zip(parameters, values):
            gradients[name] = gradients[name] + g
        mean = mean + loss.detach()
    return mean if create_graph else mean.detach(), gradients


def terminal_objective(model, parameters, queries, reference, delta=.1, penalty=1.):
    task = functional_loss(model, parameters, queries['task'])
    language = functional_loss(model, parameters, queries['language'])
    keep = functional_loss(model, parameters, queries['keep'])
    J = task + penalty * torch.relu(language - reference['language'] - delta)
    J = J + penalty * torch.relu(keep - reference['keep'] - delta)
    return J, {'query_task': float(task.detach().cpu()), 'query_language': float(language.detach().cpu()),
               'query_keep': float(keep.detach().cpu()), 'objective': float(J.detach().cpu())}


def rollout(model, initial_parameters, state, phi, streams, parent_stats, K, q, eta,
            effective_tokens=512, retain_weight=.25, horizon='H1', queries=None,
            reference=None, penalty=1., chunk=262144, log_callback=None):
    parameters = leaf_parameters(initial_parameters)
    state = detach_tree(state)
    device = next(model.parameters()).device
    diagnostic = None
    for k in range(K):
        t = time.time()
        exact = horizon == 'EXACT_H2_tail' and k >= K - 2 and queries is not None
        # State is detached only outside the declared exact tail. For K=2 this
        # covers the full trajectory, including moments and history features.
        if not exact or k == max(K - 2, 0):
            parameters = leaf_parameters(parameters)
            state = detach_tree(state)
        bd, nd = route_batches(streams['task'], device, effective_tokens)
        br, nr = route_batches(streams['language'], device, effective_tokens)
        ld, gd = support_gradients(model, parameters, bd, exact)
        lr, gr = support_gradients(model, parameters, br, exact)
        nk, keep_forwards = 0, 0
        if 'keep' in streams:
            bk, nk = route_batches(streams['keep'], device, effective_tokens)
            lk, gk = support_gradients(model, parameters, bk, exact)
            lr = .5 * lr + .5 * lk
            gr = {name: .5 * gr[name] + .5 * gk[name] for name in parameters}
            keep_forwards = len(bk)
        if exact:
            prepared, diag = prepare(parameters, gd, gr, state, model.specs, parent_stats,
                                      ld, lr, K, q, retain_weight, layers=model.llm.config.num_hidden_layers)
            parameters, state = apply_prepared(prepared, phi, eta, diag['step'], chunk)
        else:
            with torch.no_grad():
                prepared, diag = prepare(parameters, gd, gr, state, model.specs, parent_stats,
                                          ld, lr, K, q, retain_weight, layers=model.llm.config.num_hidden_layers)
                parameters, state = apply_prepared(prepared, phi, eta, diag['step'], chunk)
        if log_callback:
            synchronize(device)
            log_callback(k=k + 1, ld=float(ld.detach().cpu()), lr=float(lr.detach().cpu()),
                         task_tokens=nd, language_tokens=nr, support_forwards=len(bd) + len(br) + keep_forwards,
                         keep_tokens=nk, keep_forwards=keep_forwards,
                         step_seconds=time.time() - t, exact_window=exact, **memory(device))
        if k == K - 1 and queries is not None:
            if horizon == 'H1':
                parameters = leaf_parameters(parameters)
                J, diagnostic = terminal_objective(model, parameters, queries, reference, penalty=penalty)
                gq = dict(zip(parameters, torch.autograd.grad(J, tuple(parameters.values()))))
                h1_replay(detach_tree(prepared), phi, eta, gq, chunk)
            elif horizon == 'EXACT_H2_tail':
                J, diagnostic = terminal_objective(model, parameters, queries, reference, penalty=penalty)
                J.backward()
            else:
                raise ValueError(horizon)
    return detach_tree(parameters), detach_tree(state), diagnostic


class QueryStream(FactStream):
    def __init__(self, data, tokenizer, **kwargs):
        # Only used to construct the outer objective. Its role never changes
        # the original files, sample IDs, or permissions on support streams.
        source = [dict(r, role='support') for r in data]
        super().__init__(source, tokenizer, **kwargs)


def train(config, resume=False):
    run = Run(config['out'], config, resume)
    seed_all(config['seed'])
    model = AdapterModel(config['model'], config['rank'], config['targets'],
                         config['organization'], config['device'], scale=config.get('scale', 1.))
    run.log('model', **model.summary())
    base_hash = model.base_digest()
    phi = Phi(config['architecture']).to(config['device'])
    warm_checkpoint = None
    if config.get('initial_phi'):
        warm_checkpoint = torch.load(config['initial_phi'], map_location='cpu', weights_only=False)
        phi.load_state_dict(warm_checkpoint['phi'])
    meta_optimizer = torch.optim.Adam(phi.parameters(), lr=config['meta_lr'], foreach=False)
    if config.get('initial_phi') and config.get('load_initial_meta_optimizer', False):
        meta_optimizer.load_state_dict(warm_checkpoint['meta_optimizer'])
    parent_states = []
    historical_bases = []
    for index, path in enumerate(config['parents']):
        loaded = torch.load(path, map_location='cpu', weights_only=False)
        if not loaded.get('acquisition_gate_passed', False):
            # A checkpoint payload can refer to its verified run result; never
            # infer acquisition merely from the fact that steps ran.
            result = json.loads((Path(path).parent / 'result.json').read_text())
            if not result['acquisition_gate_passed']:
                raise RuntimeError(f'Parent has not passed acquisition: {path}')
        adapter = loaded['adapter']
        if config['organization'] == 'O3':
            adapter = split_parent_single_to_dual(adapter, config['seed'])
        if config['organization'] == 'O4':
            if (len(config.get('historical_bases', [])) != len(config['parents'])
                    or len(config.get('historical_data', [])) != len(config['parents'])):
                raise ValueError('O4 requires a historical FP32 master paired with every current adapter')
            historical_base = config['historical_bases'][index]
            parent_config = loaded.get('config', {})
            if (parent_config.get('organization') != 'O4'
                    or parent_config.get('historical_base') != historical_base
                    or parent_config.get('data') != config['parent_data'][index]):
                raise ValueError('O4 current parent was not trained on its declared historical base/data')
            history = json.loads(Path(str(historical_base) + '.json').read_text())
            historical_data = config['historical_data'][index]
            if (history['status'] != 'PASS' or history['historical_bindings'] != historical_data
                    or historical_data == config['parent_data'][index]):
                raise ValueError('O4 historical/current binding sets must match the recorded independent construction')
            historical_bases.append(historical_base)
        else:
            historical_bases.append(None)
        parent_states.append(adapter)
    latest = run.path / 'latest.pt'
    episode_start = config.get('start_episode', 0)
    if episode_start:
        if warm_checkpoint is None or warm_checkpoint['next_episode'] != episode_start:
            raise ValueError('A partial schedule requires the matching warm episode checkpoint')
    if resume:
        state = torch.load(latest, map_location='cpu', weights_only=False)
        phi.load_state_dict(state['phi'])
        meta_optimizer.load_state_dict(state['meta_optimizer'])
        restore_rng(state['rng'])
        episode_start = state['next_episode']
    worlds = json.loads(Path(config.get('world_manifest', 'data/synthetic/manifest.json')).read_text())['worlds']
    worlds = [w for w in worlds if w['split'] == 'meta']
    cumulative_steps = 0
    for episode, K in enumerate(config['episode_K']):
        if episode < episode_start:
            cumulative_steps += K
            continue
        pindex = episode % len(parent_states)
        parent = parent_states[pindex]
        if historical_bases[pindex] is not None:
            # One real historical base at a time, loaded outside the graph.
            # Its FP32 master is not rebuilt from a BF16 work copy.
            from .merge import MergeMaster
            master = MergeMaster(model, historical_bases[pindex])
            run.log('historical_base', episode=episode, master_digest=master.digest(),
                    master_file=historical_bases[pindex], merges=master.merges)
            del master
        parent_file = config['parent_data'][pindex]
        if 'final' in str(parent_file):
            raise ValueError('Formal facts cannot enter phi training')
        mode = 'known' if episode < config['known_warm_episodes'] or episode % 2 == 0 else 'new'
        task_file = parent_file
        if mode == 'new':
            task_file = str(Path('data/synthetic') / worlds[(episode // 2) % len(worlds)]['file'])
        seed = config['seed'] * 100000 + episode
        clean, _ = canonical_noise(parent, model.specs, 0., seed)
        model.load_adapter(clean)
        stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
        q = config['q_cycle'][episode % len(config['q_cycle'])]
        noisy, corruption = canonical_noise(parent, model.specs, q, seed)
        parameters = {k: noisy[k].to(v).requires_grad_(True) for k, v in model.adapter(True).items()}
        streams = {'task': FactStream(rows(task_file, 'support'), model.tokenizer, seed, config['seq']),
                   'language': LanguageStream('data/language/inner.jsonl', model.tokenizer, config['seq'], seed)}
        retain_data = (config.get('historical_data', config['parent_data'])[pindex]
                       if config['organization'] == 'O4' else parent_file)
        if config.get('retain_fact_support', True):
            streams['keep'] = FactStream(rows(retain_data, 'support'), model.tokenizer, seed, config['seq'])
        queries = {}
        for key, file in [('task', task_file), ('keep', config.get('historical_data', config['parent_data'])[pindex]
                           if config['organization'] == 'O4' else parent_file)]:
            qs = QueryStream(rows(file, 'query'), model.tokenizer, seed=seed, seq=config['seq'])
            queries[key], _ = route_batches(qs, config['device'], config['effective_tokens'])
        qlang = LanguageStream('data/language/meta_query.jsonl', model.tokenizer, config['seq'], seed)
        queries['language'], _ = route_batches(qlang, config['device'], config['effective_tokens'])
        # Model contains canonical clean state; query values are labels, never
        # feature inputs. They do not appear in support transition().
        with torch.no_grad():
            reference = {key: functional_loss(model, {}, batches).detach()
                         for key, batches in queries.items()}
        run.log('episode_start', episode=episode, mode=mode, K=K, q_train=q,
                task_file=task_file, parent_file=parent_file, retain_file=retain_data,
                retain_route='half_language_half_known_facts' if 'keep' in streams else 'language_only',
                noise={k: v for k, v in corruption.items() if k != 'factors'})
        meta_optimizer.zero_grad(set_to_none=True)
        phi.requires_grad_(True)
        parameters, inner_state, diagnostics = rollout(
            model, parameters, zero_state(parameters), phi, streams, stats, K, q,
            config['inner_lr'], config['effective_tokens'], config['retain_weight'],
            config['horizon'], queries, reference, config['penalty'], config['chunk'],
            lambda **kw: run.log('inner', episode=episode, **kw))
        phi_grad = torch.nn.utils.clip_grad_norm_(phi.parameters(), 1.)
        if not torch.isfinite(phi_grad):
            raise RuntimeError('Nonfinite phi gradient')
        meta_optimizer.step()
        cumulative_steps += K
        run.log('episode_end', episode=episode, inner_updates=cumulative_steps,
                phi_grad_norm=float(phi_grad.cpu()), compute_counters=model.compute_counters, **diagnostics)
        atomic_save({'phi': {k: v.detach().cpu() for k, v in phi.state_dict().items()},
                     'meta_optimizer': meta_optimizer.state_dict(), 'rng': rng_state(),
                     'next_episode': episode + 1, 'inner_updates': cumulative_steps,
                     'base_revision': config['base_revision'], 'base_digest': base_hash,
                     'config': config, 'data_cursor': {'episode': episode + 1},
                     'inner_state_policy': 'discarded_at_episode_end'}, latest)
        if cumulative_steps in [512, 2048, 2560, 3072]:
            atomic_save(torch.load(latest, map_location='cpu', weights_only=False),
                        run.path / f'phi_{cumulative_steps:04d}.pt')
    if config['organization'] != 'O4':
        assert model.base_digest() == base_hash
    run.result(status='PASS', episodes=len(config['episode_K']), inner_updates=cumulative_steps,
               base_unchanged=config['organization'] != 'O4', checkpoint=str(latest), horizon=config['horizon'],
               compute_counters=model.compute_counters,
               note='Meta-training completion does not prove superiority; dev selection and frozen holdout tests remain required.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    config = json.loads(Path(a.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    train(config, a.resume)


if __name__ == '__main__':
    main()
