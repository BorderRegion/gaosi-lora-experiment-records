"""Ordinary LoRA fitting, including an honest resumable acquisition gate."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
import time

import torch

from .data import FactStream, LanguageStream, rows, route_batches, token_count
from .evaluate import evaluate_language, evaluate_qa
from .model import AdapterModel, memory, synchronize
from .runtime import Run, atomic_save, restore_rng, rng_state, seed_all


def route_gradient(model, stream, effective_tokens, params):
    device = next(model.parameters()).device
    batches, total = route_batches(stream, device, effective_tokens)
    mean = 0.
    for batch in batches:
        fraction = token_count(batch) / total
        loss = model(batch)
        (loss * fraction).backward()
        mean += float(loss.detach().cpu()) * fraction
    return mean, total, len(batches)


def train(config, resume=False):
    run = Run(config['out'], config, resume)
    seed_all(config['seed'])
    model = AdapterModel(config['model'], config['rank'], config['targets'],
                         config['organization'], config['device'], scale=config['scale'])
    if config.get('historical_base'):
        from .merge import MergeMaster
        historical = MergeMaster(model, config['historical_base'])
        run.log('historical_base', path=config['historical_base'],
                master_digest=historical.digest(), merges=historical.merges)
        del historical
    summary = model.summary()
    run.log('model', **summary)
    run.log('data_policy', sampler_version=2, binding_order='reshuffle_every_binding_cycle',
            packed_attention='independent_sample_causal_blocks', positions='reset_per_sample')
    if config.get('gradient_checkpointing'):
        model.llm.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        model.llm.train()
        # Qwen dropout is zero. Other modules stay frozen, including embeddings.
    before_digest = model.base_digest()
    support = rows(config['data'], 'support')
    if 'final' in str(config['data']) and not config.get('phi_freeze_manifest'):
        raise ValueError('Final parent requires a saved phi freeze manifest before training')
    stream = FactStream(support, model.tokenizer, config['seed'], config['seq'], config['microbatch'])
    language = LanguageStream(config['language_inner'], model.tokenizer, config['seq'], config['seed'], config['microbatch'])
    lang_dev = LanguageStream(config['language_dev'], model.tokenizer, config['seq'], 991)
    keep = None
    keep_query = None
    if config.get('retain_data'):
        if config['organization'] != 'O4' or not config.get('historical_base'):
            raise ValueError('Historical parent retention requires an O4 historical base')
        if config['retain_data'] == config['data']:
            raise ValueError('Current and historical O4 bindings must be distinct')
        keep = FactStream(rows(config['retain_data'], 'support'), model.tokenizer,
                          config['seed'], config['seq'], config['microbatch'])
        keep_query = [r for r in rows(config['retain_data'], 'query')
                      if r['sample_id'].endswith(':0') or r['kind'] != 'fact']
    query = rows(config.get('query_data', config['data']), 'query')
    # Fixed per-binding first query; supplementary complete evaluation can use
    # both phrasings. These are dev data, never used as fitting labels.
    query = [r for r in query if r['sample_id'].endswith(':0') or r['kind'] != 'fact']
    if config.get('eval_limit'):
        # Fixed spread over entities/attributes, avoiding the first few entities
        # becoming the entire development acquisition measurement.
        if len(query) > config['eval_limit']:
            grouped = {}
            for row in query:
                attribute = row.get('fact_id', row['sample_id']).rsplit(':a', 1)[-1]
                grouped.setdefault(attribute, []).append(row)
            generator = random.Random(991)
            for group in grouped.values():
                generator.shuffle(group)
            balanced = []
            for index in range(max(len(group) for group in grouped.values())):
                balanced.extend(group[index] for key, group in sorted(grouped.items()) if index < len(group))
            query = balanced[:config['eval_limit']]
    params = list(model.adapter(True).values())
    groups = [{'params': [p for name, p in model.adapter(True).items() if model.specs[name].factor == f],
               'lr': config['lr'] * (config['ba_ratio'] if f == 'B' else 1.)}
              for f in ['A', 'B']]
    optimizer = torch.optim.Adam(groups, betas=(.9, .999), eps=1e-8, weight_decay=0., foreach=False)
    step = 0
    previous_wall = 0.
    latest = run.path / 'latest.pt'
    source_checkpoint = latest if resume else config.get('continue_from')
    saved_language_reference = None
    saved_keep_reference = None
    if source_checkpoint:
        state = torch.load(source_checkpoint, map_location='cpu', weights_only=False)
        for key in ['model', 'rank', 'targets', 'organization', 'scale', 'lr', 'ba_ratio',
                    'data', 'seq', 'microbatch', 'effective_tokens', 'retain_weight', 'seed']:
            if state['config'][key] != config[key]:
                raise ValueError(f'Continuation changed {key}; this requires a new protocol branch')
        for key in ['historical_base', 'retain_data']:
            if state['config'].get(key) != config.get(key):
                raise ValueError(f'Continuation changed {key}; historical parent differs')
        model.load_adapter(state['adapter'])
        optimizer.load_state_dict(state['optimizer'])
        stream.load_state_dict(state['fact_stream'])
        language.load_state_dict(state['language_stream'])
        if keep is not None:
            keep.load_state_dict(state['keep_stream'])
        restore_rng(state['rng'])
        step = state['step']
        previous_wall = state['training_wall_seconds']
        assert state['base_digest'] == before_digest
        saved_language_reference = state.get('initial_language')
        saved_keep_reference = state.get('initial_keep')
        if saved_language_reference is None:
            previous_result = Path(source_checkpoint).parent / 'result.json'
            if previous_result.exists():
                saved_language_reference = json.loads(previous_result.read_text())['initial_language']
    initial = saved_language_reference or evaluate_language(model, lang_dev, config['language_eval_tokens'])
    run.log('initial_language', step=step, **initial)
    initial_keep = None
    if keep is not None:
        initial_keep = saved_keep_reference or evaluate_qa(model, keep_query, run.path / 'initial_keep.jsonl')
        run.log('initial_keep', step=step, metrics=initial_keep)
    start = time.time()

    def checkpoint():
        atomic_save({'adapter': model.adapter_cpu(), 'optimizer': optimizer.state_dict(),
                     'fact_stream': stream.state_dict(), 'language_stream': language.state_dict(),
                     'keep_stream': keep.state_dict() if keep is not None else None,
                     'rng': rng_state(), 'step': step, 'base_digest': before_digest,
                     'model_summary': summary, 'base_revision': config['base_revision'],
                     'initial_language': initial,
                     'initial_keep': initial_keep,
                     'training_wall_seconds': previous_wall + time.time() - start,
                     'config': config}, latest)

    curves = set(config['eval_steps'])
    if step == 0:
        eval_metrics = evaluate_qa(model, query, run.path / 'eval_0000.jsonl',
                                   generate=config['generate'])
        run.log('evaluation', step=0, **eval_metrics)
    acquisition = False
    for step in range(step + 1, config['steps'] + 1):
        synchronize(config['device'])
        t = time.time()
        optimizer.zero_grad(set_to_none=True)
        ld, nt, fw = route_gradient(model, stream, config['effective_tokens'], params)
        if config['retain_weight']:
            # Accumulate the language route with its own effective-token mean.
            batch_list, nr = route_batches(language, config['device'], config['effective_tokens'])
            lrtn = 0.
            for batch in batch_list:
                weight = token_count(batch) / nr
                loss = model(batch)
                mass = .5 if keep is not None else 1.
                (loss * weight * config['retain_weight'] * mass).backward()
                lrtn += float(loss.detach().cpu()) * weight
            fw += len(batch_list)
        else:
            nr, lrtn = 0, 0.
        nk, lk = 0, 0.
        if keep is not None:
            batches, nk = route_batches(keep, config['device'], config['effective_tokens'])
            for batch in batches:
                fraction = token_count(batch) / nk
                loss = model(batch)
                (loss * fraction * .5 * config['retain_weight']).backward()
                lk += float(loss.detach().cpu()) * fraction
            fw += len(batches)
        gn = torch.nn.utils.clip_grad_norm_(params, 1.)
        if not torch.isfinite(gn):
            checkpoint()
            raise RuntimeError('Nonfinite support gradient')
        optimizer.step()
        synchronize(config['device'])
        run.log('train', step=step, loss_task=ld, loss_language=lrtn,
                grad_norm=float(gn.detach().cpu()), step_seconds=time.time() - t,
                task_tokens=nt, language_tokens=nr, forwards=fw, backwards=fw,
                facts_covered=len(stream.covered), unique_fact_pool=len(stream.ids),
                fact_exposures=stream.exposures, **memory(config['device']))
        if keep is not None:
            run.log('historical_retention', step=step, loss_known=lk, known_tokens=nk,
                    language_mass=.5 * config['retain_weight'], known_mass=.5 * config['retain_weight'],
                    known_covered=len(keep.covered), known_pool=len(keep.ids))
        if step % config['save_every'] == 0 or step in curves:
            checkpoint()
        epoch_cap = bool(config.get('max_epochs') and stream.exposures >= len(support) * config['max_epochs'])
        if step in curves or step == config['steps'] or epoch_cap:
            metrics = evaluate_qa(model, query, run.path / f'eval_{step:04d}.jsonl',
                                  generate=config['generate'])
            metrics['language'] = evaluate_language(model, lang_dev, config['language_eval_tokens'])
            if keep is not None:
                metrics['keep'] = evaluate_qa(model, keep_query, run.path / f'keep_{step:04d}.jsonl')
            run.log('evaluation', step=step, **metrics)
            acquisition = (metrics['candidate_accuracy'] >= .85 and
                           metrics.get('strict_EM', 0.) >= .75 and
                           len(stream.covered) == len(stream.ids) and
                           metrics['language']['nll'] <= initial['nll'] + .5)
            if keep is not None:
                acquisition = (acquisition and len(keep.covered) == len(keep.ids)
                               and metrics['keep']['nll'] <= initial_keep['nll'] + .5)
            if acquisition and config.get('stop_on_acquisition', False):
                run.log('acquisition_gate', step=step, action='stop_after_dev_frozen_gate')
                break
        if epoch_cap:
            run.log('epoch_cap', step=step, epochs=stream.exposures / len(support))
            break
    checkpoint()
    after_digest = model.base_digest()
    assert before_digest == after_digest, 'Frozen base mutated'
    run.result(status='PASS', steps=step, acquisition_gate_passed=acquisition,
               base_unchanged=True, base_digest=before_digest,
               model=summary, config=config, fact_stream=stream.state_dict(),
               initial_language=initial, training_wall_seconds=previous_wall + time.time() - start,
               initial_keep=initial_keep,
               keep_stream=keep.state_dict() if keep is not None else None,
               checkpoint=str(latest), memory=memory(config['device']),
               note='PASS means numerical execution completed; acquisition_gate_passed determines whether this is a valid learned parent.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    train(config, args.resume)


if __name__ == '__main__':
    main()
