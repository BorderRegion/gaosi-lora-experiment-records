"""Ordinary learning after selective recovery: pure R or two taught facts.

Load the exact finished endpoint without QR, further corruption, or access to
the original clean factors. All arms start fresh stock Adam state and share
the same support stream. F queries and language are evaluation only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import torch

from .data import FactStream, LanguageStream, rows, route_batches
from .evaluate import evaluate_qa, evaluate_language
from .meta import support_gradients
from .model import AdapterModel, memory, synchronize
from .runtime import Run, atomic_save, restore_rng, rng_state, seed_all


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def check_freezes(config):
    # Called before opening any task, parent, or source checkpoint.
    frozen = json.loads(Path(config['freeze_manifest']).read_text())
    if (frozen['status'] != 'FROZEN' or not frozen['stopping_rule_frozen']
            or frozen['formal_data_used'] or frozen['organization'] != 'O1'
            or file_hash(frozen['phi']) != frozen['phi_sha256']):
        raise ValueError('Reflow requires an intact frozen O1 phi before reading data')
    rates = json.loads(Path(config['lr_freeze_manifest']).read_text())
    if (rates['status'] != 'REFLOW_LRS_FROZEN' or rates['formal_data_used']
            or len(set(rates['lrs'])) != 2 or config['inner_lr'] not in rates['lrs']):
        raise ValueError('Reflow needs two development-frozen learning rates')
    if (rates['recovery_K'] != frozen['recovery_K']
            or rates['development_parent'] != frozen['development_parent']):
        raise ValueError('Reflow rates and phi refer to different development settings')
    if config['mode'] not in ['pure_R', 'reteach_two']:
        raise ValueError('Unknown reflow request')
    if config['K'] <= 0 or any(step <= 0 or step > config['K'] for step in config['eval_steps']):
        raise ValueError('Invalid reflow measurement budget')
    return frozen


def request_pools(data, target_ids, mode, taught_ids):
    targets, taught = set(target_ids), set(taught_ids)
    all_ids = {row['fact_id'] for row in data}
    if not targets or not targets < all_ids:
        raise ValueError('Request must have nonempty F and R')
    support = [row for row in data if row['role'] == 'support']
    query = [row for row in data if row['role'] == 'query' and row['sample_id'].endswith(':0')]
    if {row['fact_id'] for row in query} != all_ids:
        raise ValueError('Reflow requires one fixed query per binding')
    queries = {'F': [row for row in query if row['fact_id'] in targets],
               'R': [row for row in query if row['fact_id'] not in targets]}
    if mode == 'pure_R':
        if taught:
            raise ValueError('Pure-R learning cannot include taught F facts')
        selected = all_ids - targets
    elif mode == 'reteach_two':
        entities = {row['entity'] for row in data if row['fact_id'] in targets}
        if (len(targets) != 8 or len(entities) != 1 or len(taught) != 2 or not taught < targets
                or {row['fact_id'] for row in data if row['entity'] in entities} != targets):
            raise ValueError('Reteaching requires exactly two of one complete eight-fact entity')
        selected = taught
        queries['taught_two'] = [row for row in query if row['fact_id'] in taught]
        queries['untaught_six'] = [row for row in query if row['fact_id'] in targets - taught]
    else:
        raise ValueError('Unknown reflow request')
    training = [row for row in support if row['fact_id'] in selected]
    if {row['fact_id'] for row in training} != selected:
        raise ValueError('Every requested training binding needs support expressions')
    # Only selected support expressions reach the training stream. Other facts
    # are used by the evaluator, never by the optimizer or support gradient.
    return training, queries


def load_source(config, parent_config, parent_digest, frozen):
    source = torch.load(config['source_checkpoint'], map_location='cpu', weights_only=False)
    result = json.loads((Path(config['source_checkpoint']).parent / 'result.json').read_text())
    c = source['config']
    if result['status'] != 'PASS' or result['config'] != c or source['base_digest'] != parent_digest:
        raise ValueError('Reflow source must be a completed matching endpoint on the same base')
    if set(c.get('target_ids', [])) != set(config['target_ids']):
        raise ValueError('Source and reflow target requests differ')
    if config['source_kind'] == 'selective':
        if (c.get('task_layout') != 'selective' or c['D'] != 'minus' or c['data'] != config['data']
                or c['parent'] != config['parent'] or source['step'] != c['K']
                or c['K'] != frozen['recovery_K'] or c['q_apply'] != frozen['q_medium']
                or c['method'] not in ['standard_adam', 'phi']
                or not result['base_unchanged']):
            raise ValueError('Need the exact finished D-minus endpoint')
        adapter = source['parameters']
    elif config['source_kind'] == 'retain_only':
        if (c.get('parent_layout') != 'selective' or c.get('parent_mode') != 'retain_only'
                or c['data'] != config['data'] or not result['acquisition_gate_passed']):
            raise ValueError('Need an acquired matched retain-only parent')
        for key in ['model', 'rank', 'targets', 'organization', 'scale', 'seq']:
            if c[key] != parent_config[key]:
                raise ValueError(f'Retain-only source differs in {key}')
        adapter = source['adapter']
    else:
        raise ValueError('Unknown reflow source kind')
    return adapter, file_hash(config['source_checkpoint'])


def run_reflow(config, resume=False):
    frozen = check_freezes(config)
    run = Run(config['out'], config, resume)
    seed_all(config['data_seed'])
    parent = torch.load(config['parent'], map_location='cpu', weights_only=False)
    c = parent['config']
    parent_result = json.loads((Path(config['parent']).parent / 'result.json').read_text())
    if (c['organization'] != 'O1' or c.get('historical_base')
            or c.get('parent_layout') != 'selective' or c.get('parent_mode') != 'all'
            or c['data'] != config['data'] or set(c['target_ids']) != set(config['target_ids'])
            or not parent_result['acquisition_gate_passed'] or not parent_result['base_unchanged']):
        raise ValueError('This reflow experiment requires the fixed-base O1 organization')
    source, source_hash = load_source(config, c, parent['base_digest'], frozen)
    model = AdapterModel(c['model'], c['rank'], c['targets'], 'O1', config['device'], scale=c['scale'])
    base_digest = model.base_digest()
    if base_digest != parent['base_digest'] or set(source) != set(model.adapter()):
        raise ValueError('Source has a different frozen base or adapter topology')
    model.load_adapter(source)
    del source, parent
    parameters = model.adapter(True)
    optimizer = torch.optim.Adam(list(parameters.values()), lr=config['inner_lr'],
                                 betas=(.9, .999), eps=1e-8, weight_decay=0., foreach=False)
    training, queries = request_pools(rows(config['data']), config['target_ids'],
                                     config['mode'], config['taught_ids'])
    stream = FactStream(training, model.tokenizer, config['data_seed'], c['seq'])
    language = LanguageStream(config['language_eval'], model.tokenizer, c['seq'], 991)
    initial_eval, step_start = None, 0
    if resume:
        saved = torch.load(run.path / 'latest.pt', map_location='cpu', weights_only=False)
        if saved['source_sha256'] != source_hash or saved['base_digest'] != base_digest:
            raise ValueError('Reflow resume source or frozen base changed')
        model.load_adapter(saved['adapter'])
        optimizer.load_state_dict(saved['optimizer'])
        stream.load_state_dict(saved['stream'])
        restore_rng(saved['rng'])
        initial_eval, step_start = saved['initial'], saved['step']

    def evaluate(step):
        metrics = {name: evaluate_qa(model, data, run.path / f'eval_{step:04d}_{name}.jsonl')
                   for name, data in queries.items()}
        metrics['language'] = evaluate_language(model, language, config['language_eval_tokens'])
        run.log('evaluation', step=step, metrics=metrics, coverage=len(stream.covered), pool=len(stream.ids))
        return metrics

    def save(step):
        atomic_save({'adapter': model.adapter_cpu(), 'optimizer': optimizer.state_dict(),
                     'stream': stream.state_dict(), 'step': step, 'rng': rng_state(), 'initial': initial_eval,
                     'base_digest': base_digest, 'source_sha256': source_hash, 'config': config},
                    run.path / 'latest.pt')

    if initial_eval is None:
        run.log('reflow_policy', mode=config['mode'], source_sha256=source_hash,
                objective='1.0 CE(selected support); no F/R renormalization, language or query gradient',
                optimizer='fresh stock Adam(.9,.999,eps=1e-8), global norm clip 1',
                additional_noise=False, additional_QR=False, phi_called=False,
                training_ids=stream.ids, full_query_ids={k: sorted({r['fact_id'] for r in v})
                                                        for k, v in queries.items()})
        initial_eval = evaluate(0)
        save(0)
    final_eval = None
    if resume and step_start and (step_start in config['eval_steps'] or step_start == config['K']):
        events = [json.loads(line) for line in (run.path / 'events.jsonl').read_text().splitlines()]
        found = [event for event in events if event['event'] == 'evaluation' and event['step'] == step_start]
        measured = found[-1]['metrics'] if found else evaluate(step_start)
        if step_start == config['K']:
            final_eval = measured
    for step in range(step_start + 1, config['K'] + 1):
        started = time.time()
        batches, count = route_batches(stream, config['device'], config['effective_tokens'])
        loss, gradients = support_gradients(model, parameters, batches)
        optimizer.zero_grad(set_to_none=True)
        for name, value in parameters.items():
            value.grad = gradients[name]
        norm = torch.nn.utils.clip_grad_norm_(list(parameters.values()), 1.)
        if not torch.isfinite(norm):
            raise RuntimeError('Nonfinite reflow support gradient')
        optimizer.step()
        synchronize(config['device'])
        run.log('reflow', step=step, loss=float(loss.cpu()), support_tokens=count,
                coverage=len(stream.covered), pool=len(stream.ids), step_seconds=time.time() - started,
                compute_counters=model.compute_counters, **memory(config['device']))
        save(step)
        if step in config['eval_steps'] or step == config['K']:
            final_eval = evaluate(step)
    if final_eval is None:
        final_eval = evaluate(config['K'])
    if len(stream.covered) != len(stream.ids) or model.base_digest() != base_digest:
        raise RuntimeError('Reflow endpoint needs complete training-pool coverage and unchanged base')
    run.result(status='PASS', config=config, initial=initial_eval, final=final_eval,
               coverage=len(stream.covered), pool=len(stream.ids), source_sha256=source_hash,
               stream=stream.state_dict(), base_digest=base_digest, base_unchanged=True,
               checkpoint=str(run.path / 'latest.pt'), compute_counters=model.compute_counters,
               same_paragraph_R='N/A: independent synthetic entries, no mixed paragraphs',
               interpretation='Behavior after ordinary continuation; no claim of permanent deletion')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    run_reflow(config, args.resume)


if __name__ == '__main__':
    main()
