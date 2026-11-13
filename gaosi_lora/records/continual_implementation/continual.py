"""Four LoRA organizations with shared eight-round schedules and fixed replay.

This runner does not tune on formal curves, does not route by task ID, and does
not reset O3 plastic state. O4 uses a persistent FP32 base master.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import time

import torch

from .data import rows, FactStream, LanguageStream, route_batches, token_count
from .evaluate import evaluate_qa, evaluate_language
from .model import AdapterModel, memory
from .merge import MergeMaster
from .meta import support_gradients
from .noise import canonical_noise, split_parent_single_to_dual
from .runtime import Run, atomic_save, restore_rng, rng_state, seed_all
from .train import route_gradient
from .transition import Phi, scalar_stats, detach_tree, zero_state, prepare, apply_prepared


def task_rows(world):
    return rows(world)


def day(model, data, language, K, lr, seed, seq, effective_tokens, callback):
    model.set_stage('day')
    params = list(model.adapter(True).values())
    opt = torch.optim.Adam(params, lr=lr, foreach=False, weight_decay=0.)
    stream = FactStream(data, model.tokenizer, seed, seq)
    for k in range(K):
        t = time.time()
        opt.zero_grad(set_to_none=True)
        ld, nd, fw = route_gradient(model, stream, effective_tokens, params)
        bs, nr = route_batches(language, next(model.parameters()).device, effective_tokens)
        lrtn = 0.
        for batch in bs:
            l = model(batch)
            fraction = token_count(batch) / nr
            (l * fraction * .25).backward()
            lrtn += float(l.detach().cpu()) * fraction
        gn = torch.nn.utils.clip_grad_norm_(params, 1.)
        if not torch.isfinite(gn):
            raise RuntimeError('Nonfinite day gradient')
        opt.step()
        callback(k=k + 1, loss_task=ld, loss_language=lrtn, task_tokens=nd,
                 language_tokens=nr, seconds=time.time() - t)
    return stream.state_dict()


def night(model, data, language, phi, K, lr, q, seed, seq, effective_tokens, callback,
          method='paired_adam'):
    model.set_stage('night')
    clean = model.adapter_cpu()
    noisy, corruption = canonical_noise(clean, model.specs, q, seed)
    canonical, _ = canonical_noise(clean, model.specs, 0., seed)
    model.load_adapter(canonical)
    stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
    model.load_adapter(noisy)
    work = {k: v.detach().requires_grad_(True) for k, v in model.adapter(True).items()}
    state = None if method == 'standard_adam' else zero_state(work)
    optimizer = (torch.optim.Adam(list(work.values()), lr=lr, foreach=False, weight_decay=0.)
                 if method == 'standard_adam' else None)
    if method not in ['standard_adam', 'paired_adam', 'phi']:
        raise ValueError(method)
    stream = FactStream(data, model.tokenizer, seed, seq)
    device = next(model.parameters()).device
    for k in range(K):
        started = time.time()
        bd, nd = route_batches(stream, device, effective_tokens)
        br, nr = route_batches(language, device, effective_tokens)
        ld, gd = support_gradients(model, work, bd)
        lrtn, gr = support_gradients(model, work, br)
        if optimizer is not None:
            optimizer.zero_grad(set_to_none=True)
            for name, value in work.items():
                value.grad = gd[name] + .25 * gr[name]
            norm = torch.nn.utils.clip_grad_norm_(list(work.values()), 1.)
            if not torch.isfinite(norm):
                raise RuntimeError('Nonfinite standard Adam night gradient')
            optimizer.step()
        else:
            with torch.no_grad():
                prepared, diag = prepare(work, gd, gr, state, model.specs, stats, ld, lrtn, K, q,
                                          .25, layers=model.llm.config.num_hidden_layers)
                work, state = apply_prepared(prepared, phi, lr, diag['step'])
            work = {k: v.detach().requires_grad_(True) for k, v in work.items()}
        callback(k=k + 1, loss_task=float(ld.cpu()), loss_language=float(lrtn.cpu()),
                 task_tokens=nd, language_tokens=nr, seconds=time.time() - started,
                 replay_covered=len(stream.covered), replay_pool=len(stream.ids))
    updated = dict(noisy)
    updated.update(work)
    model.load_adapter(updated)
    return stream.state_dict(), corruption


def scratch_probes(model, worlds, out, seed, seq, effective_tokens, lr):
    results = []
    for i, file in enumerate(worlds):
        seed_all(seed + i)
        model.add_scratch(16)
        try:
            support = rows(file, 'support')
            query = rows(file, 'query')
            stream = FactStream(support, model.tokenizer, seed + i, seq)
            params = list(model.adapter(True).values())
            optimizer = torch.optim.Adam(params, lr=lr, foreach=False, weight_decay=0.)
            curves = []
            for k in range(1, 17):
                optimizer.zero_grad(set_to_none=True)
                loss, tokens, fw = route_gradient(model, stream, effective_tokens, params)
                torch.nn.utils.clip_grad_norm_(params, 1.)
                optimizer.step()
                if k in [1, 4, 16]:
                    result = evaluate_qa(model, query, Path(out) / f'fresh_{i}_k{k}.jsonl')
                    curves.append({'steps': k, 'loss_support': loss, **result})
            results.append({'world': file, 'rank': 16, 'curves': curves})
        finally:
            model.remove_scratch()
    return results


def train(config, resume=False):
    run = Run(config['out'], config, resume)
    frozen = json.loads(Path(config['freeze_manifest']).read_text())
    assert frozen['status'] == 'FROZEN'
    assert len(config['worlds']) == config['rounds']
    seed_all(config['seed'])
    parent = torch.load(config['parent'], map_location='cpu', weights_only=False)
    verified = json.loads((Path(config['parent']).parent / 'result.json').read_text())
    if not verified['acquisition_gate_passed']:
        raise RuntimeError('Initial historical parent has not learned the knowledge')
    c = parent['config']
    device = config.get('device', 'musa')
    model = AdapterModel(c['model'], c['rank'], c['targets'], config['organization'], device,
                         scale=c.get('scale', 1.))
    adapter = parent['adapter']
    if config['organization'] == 'O3':
        adapter = split_parent_single_to_dual(adapter, config['seed'])
    adapter, _ = canonical_noise(adapter, model.specs, 0., config['seed'])
    model.load_adapter(adapter)
    original_hash = model.base_digest()
    phi = Phi(config['architecture']).to(device)
    if config['night_method'] == 'phi':
        if hashlib.sha256(Path(config['phi']).read_bytes()).hexdigest() != frozen['phi_sha256']:
            raise RuntimeError('Frozen phi mismatch')
        phi.load_state_dict(torch.load(config['phi'], map_location='cpu', weights_only=False)['phi'])
    elif config['night_method'] not in ['paired_adam', 'standard_adam']:
        raise ValueError(config['night_method'])
    phi.requires_grad_(False)
    master = None
    if config['organization'] == 'O4':
        if not resume:
            master = MergeMaster(model)
            run.log('merge_initial_history', **master.merge(model))
        else:
            saved = torch.load(run.path / 'latest.pt', map_location='cpu', weights_only=False)
            master = MergeMaster(model, saved['base_master'])
    history = rows(config['parent_data'])
    all_bindings = {r['fact_id']: [] for r in history if r['role'] == 'support'}
    for row in history:
        if row['role'] == 'support':
            all_bindings[row['fact_id']].append(row)
    # Fixed random priorities implement an order-independent reservoir: every
    # group sees exactly the same 128 current/past true bindings per boundary.
    def priority(key):
        return hashlib.sha256(f"{config['replay_seed']}:{key}".encode()).hexdigest()
    start = 0
    if resume:
        saved = torch.load(run.path / 'latest.pt', map_location='cpu', weights_only=False)
        model.load_adapter(saved['adapter'])
        history = saved['history']
        all_bindings = saved['all_bindings']
        start = saved['next_round']
        restore_rng(saved['rng'])
    lang = LanguageStream('data/language/inner.jsonl', model.tokenizer, config['seq'], config['seed'])
    if resume:
        lang.load_state_dict(saved['language_stream'])
    langeval = LanguageStream(config['language_eval'], model.tokenizer, config['seq'], 991)

    def boundary_eval(round_number):
        data = [r for r in history if r['role'] == 'query' and r['sample_id'].endswith(':0')]
        metrics = evaluate_qa(model, data, run.path / f'history_round_{round_number:02d}.jsonl')
        metrics['language'] = evaluate_language(model, langeval, config['language_eval_tokens'])
        run.log('history_evaluation', round=round_number, **metrics)
        if round_number in [0, 4, 8]:
            fresh = scratch_probes(model, config['probe_worlds'], run.path / f'probe_round_{round_number}',
                                    config['probe_seed'], config['seq'], config['effective_tokens'], config['day_lr'])
            run.log('fresh_capacity', round=round_number, worlds=fresh, common_extra_rank=16)

    if start == 0:
        boundary_eval(0)
    for round_index in range(start, config['rounds']):
        round_seed = config['seed'] * 1000 + round_index
        new = task_rows(config['worlds'][round_index])
        support = [r for r in new if r['role'] == 'support']
        assert len({r['fact_id'] for r in support}) == 32
        canonical, _ = canonical_noise(model.adapter_cpu(), model.specs, 0., round_seed)
        model.load_adapter(canonical)
        run.log('day_start', round=round_index + 1, model=model.summary(), replay_capacity=128)
        day_state = day(model, support, lang, config['day_steps'], config['day_lr'], round_seed,
                        config['seq'], config['effective_tokens'],
                        lambda **kw: run.log('day', round=round_index + 1, **kw))
        run.log('day_complete', round=round_index + 1, fact_stream=day_state)
        for row in support:
            all_bindings.setdefault(row['fact_id'], []).append(row)
        selected = sorted(all_bindings, key=priority)[:128]
        replay = [row for key in selected for row in all_bindings[key]]
        state, corruption = night(model, replay, lang, phi, config['night_steps'], config['night_lr'],
                                   config['q_apply'], round_seed, config['seq'], config['effective_tokens'],
                                   lambda **kw: run.log('night', round=round_index + 1, **kw),
                                   method=config['night_method'])
        run.log('night_complete', round=round_index + 1, replay_ids=selected,
                replay_coverage=state, corruption={k: v for k, v in corruption.items() if k != 'factors'})
        history.extend(new)
        if master:
            run.log('merge', round=round_index + 1, **master.merge(model), **memory(device))
        boundary_eval(round_index + 1)
        atomic_save({'adapter': model.adapter_cpu(), 'history': history,
                     'all_bindings': all_bindings, 'next_round': round_index + 1,
                     'language_stream': lang.state_dict(), 'rng': rng_state(),
                     'base_master': master.state_dict(c['base_revision']) if master else None,
                     'base_revision': c['base_revision'], 'config': config,
                     'inner_optimizer_policy': 'reset_at_each_day_night_boundary'}, run.path / 'latest.pt')
    if not master:
        assert model.base_digest() == original_hash
    run.result(status='PASS', config=config, completed_rounds=config['rounds'],
               replay_capacity=128, phi_frozen=True, total_adapter_rank=model.rank,
               cumulative_merge_rank_upper_bound=master.rank_sum if master else 0,
               note='Eight rounds are a finite pilot, not evidence of indefinite continual learning.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    if json.loads(Path(a.config).read_text()).get('device', 'musa') == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    train(json.loads(Path(a.config).read_text()), a.resume)


if __name__ == '__main__':
    main()
