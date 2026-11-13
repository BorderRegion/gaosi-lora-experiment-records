"""Frozen-phi/paired-Adam recovery, including the fixed-slot D+/D- comparison.

Formal runs require a learned parent, calibrated q, frozen stopping rule, and
phi freeze manifest. Development calibration permits only dev data and ordinary
optimizers under a separately recorded development plan. The F slot in D- is zero; R exposure is not
doubled. Query and clean coordinates are absent from the support transition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import torch

from .data import rows, FactStream, LanguageStream, route_batches
from .evaluate import evaluate_qa, evaluate_language
from .meta import support_gradients
from .model import AdapterModel, memory, synchronize
from .noise import canonical_noise, split_parent_single_to_dual
from .runtime import Run, atomic_save, restore_rng, rng_state, seed_all
from .transition import Phi, scalar_stats, zero_state, prepare, apply_prepared, detach_tree


def run_recovery(config, resume=False):
    run = Run(config['out'], config, resume)
    freeze = json.loads(Path(config['freeze_manifest']).read_text())
    phase = config.get('phase', 'formal')
    phi_development = phase == 'phi_development'
    development = phase in ['development', 'phi_development']
    if development:
        expected_status = 'PHI_DEVELOPMENT_PLAN' if phi_development else 'DEVELOPMENT_PLAN'
        if freeze['status'] != expected_status or (not phi_development and config['method'] == 'phi'):
            raise ValueError('Development calibration requires an ordinary optimizer and a recorded plan')
        if config['data'] not in freeze['allowed_data'] or config['parent'] not in freeze['allowed_parents']:
            raise ValueError('Development recovery is restricted to the named dev parent/data')
        if config['inner_lr'] not in freeze['lr_candidates'] or config['K'] not in freeze['K_candidates']:
            raise ValueError('Development budget/rate is outside the recorded plan')
        if config['q_apply'] not in freeze['q_candidates'] or config['noise_scheme'] not in freeze['noise_schemes']:
            raise ValueError('Development noise is outside the recorded plan')
        if 'dev' not in str(config['data']) or 'final' in str(config['data']):
            raise ValueError('Formal facts cannot enter development calibration')
        if config.get('retain_data'):
            if config['retain_data'] not in freeze['allowed_data'] or 'final' in str(config['retain_data']):
                raise ValueError('Retention data is outside the development plan')
    elif freeze['status'] != 'FROZEN' or not freeze.get('stopping_rule_frozen'):
        raise ValueError('Need a phi/development freeze manifest')
    seed_all(config['data_seed'])
    checkpoint = torch.load(config['parent'], map_location='cpu', weights_only=False)
    parent_result = json.loads((Path(config['parent']).parent / 'result.json').read_text())
    if not parent_result['acquisition_gate_passed']:
        raise ValueError('Parent did not acquire the facts')
    c = checkpoint['config']
    organization = config.get('organization', c['organization'])
    model = AdapterModel(c['model'], c['rank'], c['targets'], organization, config['device'], scale=c['scale'])
    if c.get('historical_base'):
        from .merge import MergeMaster
        master = MergeMaster(model, c['historical_base'])
        run.log('historical_base', path=c['historical_base'], master_digest=master.digest())
        del master
    base_digest = model.base_digest()
    parent = checkpoint['adapter']
    if organization == 'O3' and c['organization'] != 'O3':
        parent = split_parent_single_to_dual(parent, config.get('split_seed', 17))
    clean, _ = canonical_noise(parent, model.specs, 0., config['noise_seed'])
    noisy, corruption = canonical_noise(parent, model.specs, config['q_apply'], config['noise_seed'], config['noise_scheme'])
    if config.get('initial_adapter'):
        initial = torch.load(config['initial_adapter'], map_location='cpu', weights_only=False)
        if (initial['parent'] != config['parent'] or initial['noise_seed'] != config['noise_seed']
                or initial['q_apply'] != config['q_apply'] or initial['noise_scheme'] != config['noise_scheme']
                or initial['organization'] != organization):
            raise ValueError('Paired initial-state manifest mismatch')
        if set(initial['adapter']) != set(noisy) or any(not torch.equal(initial['adapter'][k], noisy[k]) for k in noisy):
            raise ValueError('Paired adapter bytes differ from the declared corruption')
        noisy = initial['adapter']
    else:
        atomic_save({'adapter': noisy, 'parent': config['parent'], 'q_apply': config['q_apply'],
                     'noise_seed': config['noise_seed'], 'noise_scheme': config['noise_scheme'],
                     'organization': organization, 'corruption': corruption}, run.path / 'initial.pt')
    model.load_adapter(clean)
    stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
    phi = Phi(config['architecture']).to(config['device'])
    if config['method'] == 'phi':
        phi_checkpoint = torch.load(config['phi'], map_location='cpu', weights_only=False)
        phi.load_state_dict(phi_checkpoint['phi'])
        file_hash = hashlib.sha256(Path(config['phi']).read_bytes()).hexdigest()
        expected_hash = (freeze.get('allowed_phis', {}).get(config['phi'])
                         if phi_development else freeze['phi_sha256'])
        if file_hash != expected_hash:
            raise ValueError('Frozen phi hash differs')
    elif config['method'] not in ['paired_adam', 'standard_adam', 'factor_lr']:
        raise ValueError('Unknown method')
    phi.requires_grad_(False)
    phi.eval()
    task_data = rows(config['data'], 'support')
    layout = config.get('task_layout', 'selective')
    if layout not in ['all', 'selective']:
        raise ValueError('Unknown task layout')
    if config.get('retain_data') and layout != 'all':
        raise ValueError('Separate known-fact retention is for new-task/full recovery, not selective slots')
    target_ids = set(config.get('target_ids', []))
    F = [r for r in task_data if r.get('fact_id') in target_ids]
    R = [r for r in task_data if r.get('fact_id') not in target_ids]
    if layout == 'selective' and config['retain_subset_fraction'] < 1:
        # The same precommitted subset is shared by all methods.
        import random
        ids = sorted({r['fact_id'] for r in R})
        random.Random(config['subset_seed']).shuffle(ids)
        selected = set(ids[:round(len(ids) * config['retain_subset_fraction'])])
        R = [r for r in R if r['fact_id'] in selected]
    streams = {'language': LanguageStream('data/language/inner.jsonl', model.tokenizer, c['seq'], config['data_seed'])}
    if config.get('retain_data'):
        streams['keep'] = FactStream(rows(config['retain_data'], 'support'), model.tokenizer,
                                     config['data_seed'], c['seq'])
    if layout == 'all':
        streams['task'] = FactStream(task_data, model.tokenizer, config['data_seed'], c['seq'])
        slots, coverage_slot = [('task', 1.)], 'task'
    else:
        if not F or not R:
            raise ValueError('Selective recovery needs nonempty F and R pools')
        streams['R'] = FactStream(R, model.tokenizer, config['data_seed'], c['seq'])
        if config['D'] == 'plus':
            streams['F'] = FactStream(F, model.tokenizer, config['data_seed'], c['seq'])
        elif config['D'] != 'minus':
            raise ValueError('D must be plus or minus')
        slots, coverage_slot = [('F', .5), ('R', .5)], 'R'
    query = rows(config['data'], 'query')
    query = [r for r in query if r['sample_id'].endswith(':0') or r['kind'] != 'fact']
    queries = ({'task': query} if layout == 'all' else
               {'F': [r for r in query if r['fact_id'] in target_ids],
                'R': [r for r in query if r['fact_id'] not in target_ids]})
    if config.get('retain_data'):
        queries['keep'] = [r for r in rows(config['retain_data'], 'query')
                           if r['sample_id'].endswith(':0') or r['kind'] != 'fact']
    lang_eval = LanguageStream(config['language_eval'], model.tokenizer, c['seq'], 991)
    clean_eval = {part: evaluate_qa(model, data, run.path / f'clean_{part}.jsonl')
                  for part, data in queries.items() if data}
    clean_eval['language'] = evaluate_language(model, lang_eval, config['language_eval_tokens'])
    parameters = {k: noisy[k].to(v).requires_grad_(True) for k, v in model.adapter(True).items()}
    state = zero_state(parameters)
    standard_optimizer = None
    if config['method'] == 'standard_adam':
        standard_optimizer = torch.optim.Adam(list(parameters.values()), lr=config['inner_lr'],
                                               betas=(.9, .999), eps=1e-8, weight_decay=0., foreach=False)
    step_start = 0
    saved = None
    if resume:
        saved = torch.load(run.path / 'latest.pt', map_location='cpu', weights_only=False)
        parameters = {k: v.to(config['device']).requires_grad_(True) for k, v in saved['parameters'].items()}
        if config['method'] == 'standard_adam':
            standard_optimizer = torch.optim.Adam(list(parameters.values()), lr=config['inner_lr'],
                                                   betas=(.9, .999), eps=1e-8, weight_decay=0., foreach=False)
            standard_optimizer.load_state_dict(saved['standard_optimizer'])
        state = torch.utils._pytree.tree_map(lambda v: v.to(config['device']) if isinstance(v, torch.Tensor) else v,
                                             saved['inner_state'])
        for name, stream in streams.items():
            stream.load_state_dict(saved['streams'][name])
        restore_rng(saved['rng'])
        step_start = saved['step']
        if saved['base_digest'] != base_digest:
            raise ValueError('Resume base differs from the saved frozen base')
    norm_targets = None
    if config.get('match_update_norms'):
        norm_targets = json.loads(Path(config['match_update_norms']).read_text())
        if len(norm_targets) < config['K'] or any(not math.isfinite(float(x)) or float(x) < 0 for x in norm_targets):
            raise ValueError('Need one finite nonnegative target update norm per step')

    def save(step):
        atomic_save({'parameters': {k: v.detach().cpu() for k, v in parameters.items()},
                     'inner_state': torch.utils._pytree.tree_map(lambda v: v.detach().cpu() if isinstance(v, torch.Tensor) else v, state),
                     'streams': {k: v.state_dict() for k, v in streams.items()},
                     'step': step, 'rng': rng_state(), 'config': config,
                     'initial_evaluation': initial_eval,
                     'standard_optimizer': standard_optimizer.state_dict() if standard_optimizer else None,
                     'base_revision': c['base_revision'], 'base_digest': base_digest}, run.path / 'latest.pt')

    def evaluate(step):
        values = dict(noisy)
        values.update(parameters)
        model.load_adapter(values)
        result = {part: evaluate_qa(model, data, run.path / f'eval_{step:04d}_{part}.jsonl')
                  for part, data in queries.items() if data}
        result['language'] = evaluate_language(model, lang_eval, config['language_eval_tokens'])
        residual = result['language']['nll'] - clean_eval['language']['nll']
        run.log('evaluation', step=step, metrics=result, language_residual_nats=residual,
                coverage_slot=coverage_slot, coverage=len(streams[coverage_slot].covered),
                pool=len(streams[coverage_slot].ids))
        return result

    if step_start == 0:
        run.log('clean_evaluation', metrics=clean_eval)
        initial_eval = evaluate(0)
    else:
        initial_eval = saved['initial_evaluation']
    final_eval = None
    # Checkpoints are committed before evaluation. A restart at that boundary
    # must finish the declared measurement before another optimizer update.
    if resume and step_start > 0 and (step_start in config['eval_steps'] or step_start == config['K']):
        events = [json.loads(line) for line in (run.path / 'events.jsonl').read_text().splitlines()]
        completed = [event for event in events
                     if event['event'] == 'evaluation' and event['step'] == step_start]
        if completed:
            final_eval = completed[-1]['metrics'] if step_start == config['K'] else None
        else:
            final_eval = evaluate(step_start)
    for k in range(step_start, config['K']):
        started = time.time()
        if standard_optimizer is None:
            parameters = {k: v.detach().requires_grad_(True) for k, v in parameters.items()}
        previous_parameters = {name: value.detach().clone() for name, value in parameters.items()}
        gd = {k: torch.zeros_like(v) for k, v in parameters.items()}
        ld = 0.
        route_counts = {}
        # Iteration order and independent binding cursors are identical across
        # D+ and D-. Removing F does not alter R/language batches or their mass.
        for slot, mass in slots:
            if slot not in streams:
                route_counts[slot] = 0
                continue
            batches, n = route_batches(streams[slot], config['device'], config['effective_tokens'])
            loss, gradients = support_gradients(model, parameters, batches)
            ld = ld + mass * loss
            gd = {key: gd[key] + mass * gradients[key] for key in parameters}
            route_counts[slot] = n
        batches, n = route_batches(streams['language'], config['device'], config['effective_tokens'])
        lr, gr = support_gradients(model, parameters, batches)
        route_counts['language'] = n
        if 'keep' in streams:
            batches, n = route_batches(streams['keep'], config['device'], config['effective_tokens'])
            lk, gk = support_gradients(model, parameters, batches)
            lr = .5 * lr + .5 * lk
            gr = {name: .5 * gr[name] + .5 * gk[name] for name in parameters}
            route_counts['keep'] = n
        if standard_optimizer is not None:
            standard_optimizer.zero_grad(set_to_none=True)
            for name, p in parameters.items():
                p.grad = gd[name] + config['retain_weight'] * gr[name]
            norm = torch.nn.utils.clip_grad_norm_(list(parameters.values()), 1.)
            if not torch.isfinite(norm):
                raise RuntimeError('Nonfinite standard Adam recovery gradient')
            standard_optimizer.step()
        else:
          with torch.no_grad():
            prepared, diag = prepare(parameters, gd, gr, state, model.specs, stats, ld, lr,
                                      config['K'], config['q_apply'], config['retain_weight'],
                                      layers=model.llm.config.num_hidden_layers)
            parameters, state = apply_prepared(prepared, phi, config['inner_lr'], diag['step'])
            if config['method'] == 'factor_lr':
                for name, item in prepared.items():
                    ratio = config['ba_ratio'] if model.specs[name].factor == 'B' else 1.
                    parameters[name] = item['w'] + ratio * (parameters[name] - item['w'])
        update_norm = torch.stack([(parameters[name].detach() - previous_parameters[name]).square().sum()
                                   for name in parameters]).sum().sqrt()
        raw_update_norm = float(update_norm.cpu())
        applied_update_norm = raw_update_norm
        norm_scale = 1.
        if norm_targets is not None:
            norm_scale = norm_targets[k] / max(raw_update_norm, 1e-30)
            if raw_update_norm == 0 and norm_targets[k] != 0:
                raise RuntimeError('Cannot normalize a zero update to a nonzero norm')
            with torch.no_grad():
                for name, value in parameters.items():
                    value.copy_(previous_parameters[name] + norm_scale * (value - previous_parameters[name]))
            applied_update_norm = float(torch.stack([(parameters[name].detach() - previous_parameters[name]).square().sum()
                                                     for name in parameters]).sum().sqrt().cpu())
        del previous_parameters
        state = detach_tree(state)
        synchronize(config['device'])
        run.log('recover', step=k + 1, loss_D=float(ld.cpu()), loss_language=float(lr.cpu()),
                route_tokens=route_counts, step_seconds=time.time() - started,
                coverage_slot=coverage_slot, coverage=len(streams[coverage_slot].covered),
                raw_update_norm=raw_update_norm, update_norm=applied_update_norm,
                update_norm_scale=norm_scale,
                compute_counters=model.compute_counters, **memory(config['device']))
        save(k + 1)
        if k + 1 in config['eval_steps'] or k + 1 == config['K']:
            final_eval = evaluate(k + 1)
    if final_eval is None:
        final_eval = evaluate(config['K'])
    for slot in [coverage_slot] + (['keep'] if 'keep' in streams else []):
        if len(streams[slot].covered) != len(streams[slot].ids):
            raise RuntimeError(f'Recovery endpoint did not cover every predeclared {slot} binding')
    assert model.base_digest() == base_digest, 'Frozen recovery base mutated'
    damage = {}
    if initial_eval is not None:
        for part in final_eval:
            clean_loss, initial_loss, final_loss = (m[part]['nll'] for m in [clean_eval, initial_eval, final_eval])
            damage[part] = {'injected': initial_loss - clean_loss, 'recovered': initial_loss - final_loss,
                            'residual': final_loss - clean_loss}
    run.result(status='PASS', config=config, initial=initial_eval, clean=clean_eval, final=final_eval,
               coverage_slot=coverage_slot, coverage=len(streams[coverage_slot].covered),
               pool=len(streams[coverage_slot].ids), base_digest=base_digest, base_unchanged=True,
               keep_coverage=({'covered': len(streams['keep'].covered), 'pool': len(streams['keep'].ids)}
                              if 'keep' in streams else None),
               compute_counters=model.compute_counters, loss_changes=damage,
               phi_frozen=True, checkpoint=str(run.path / 'latest.pt'), noise=corruption,
               conclusion='Measured endpoint only; compare paired runs to infer a method effect.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    config = json.loads(Path(a.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    run_recovery(config, a.resume)


if __name__ == '__main__':
    main()
