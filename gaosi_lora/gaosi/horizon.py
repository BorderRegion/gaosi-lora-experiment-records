"""Small development-only full H4/H8 versus truncated terminal derivatives.

Every replay uses the production rollout, identical samples and numerical
initial state. No optimizer is created and no trained phi is updated here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from .scale import sha256


def check_config(config):
    plan = json.loads(Path(config['horizon_manifest']).read_text())
    if (plan.get('status') != 'FROZEN_DEVELOPMENT_HORIZON_PLAN'
            or plan.get('formal_data_used') or config not in plan['configs']):
        raise ValueError('Horizon diagnostic differs from the development declaration')
    for group in ['artifact_sha256', 'data_sha256']:
        for path, expected in plan[group].items():
            if sha256(path) != expected:
                raise ValueError(f'Horizon diagnostic input bytes changed: {path}')
    required = {config['data'], config['retain_data'], config['language_support'],
                config['language_query']}
    if not required <= plan['data_sha256'].keys():
        raise ValueError('Horizon diagnostic has undeclared data')
    if (config['retain_data'] != 'data/synthetic/dev_parent.jsonl'
            or config['language_support'] != 'data/language/inner.jsonl'
            or config['language_query'] != 'data/language/dev.jsonl'
            or config['K'] not in [4, 8] or config['tail'] not in [1, 2, config['K']]):
        raise ValueError('Only the declared small development trajectories are allowed')
    for path in [config['data'], config['retain_data']]:
        source = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        if not source or any(not r.get('world_id', '').startswith('dev') for r in source):
            raise ValueError('Diagnostic fact/query rows must belong to development worlds')
    return plan


def tree_digest(value):
    import torch
    h = hashlib.sha256()

    def visit(x):
        if isinstance(x, torch.Tensor):
            t = x.detach().cpu().contiguous()
            h.update(str((str(t.dtype), list(t.shape))).encode())
            h.update(t.reshape(-1).view(torch.uint8).numpy().tobytes())
        elif isinstance(x, dict):
            for k in sorted(x):
                h.update(k.encode())
                visit(x[k])
        elif isinstance(x, (tuple, list)):
            for item in x:
                visit(item)
        else:
            h.update(repr(x).encode())

    visit(value)
    return h.hexdigest()


def compare_trees(observed, expected, atol=1e-6, rtol=1e-5):
    import torch
    max_abs, difference_sq, expected_sq, tensors = 0., 0., 0., 0

    def visit(x, y):
        nonlocal max_abs, difference_sq, expected_sq, tensors
        if isinstance(x, torch.Tensor):
            x, y = x.detach().cpu(), y.detach().cpu()
            torch.testing.assert_close(x, y, atol=atol, rtol=rtol)
            d = (x.double() - y.double())
            max_abs = max(max_abs, float(d.abs().max()))
            difference_sq += float(d.square().sum())
            expected_sq += float(y.double().square().sum())
            tensors += 1
        elif isinstance(x, dict):
            assert x.keys() == y.keys()
            for key in x:
                visit(x[key], y[key])
        elif isinstance(x, (list, tuple)):
            assert len(x) == len(y)
            for a, b in zip(x, y):
                visit(a, b)
        else:
            assert x == y

    visit(observed, expected)
    return {'tensor_count': tensors, 'max_absolute_difference': max_abs,
            'relative_l2_difference': (difference_sq / max(expected_sq, 1e-300)) ** .5,
            'atol': atol, 'rtol': rtol}


def gradient_comparison(observed, full):
    import torch
    a, b = observed.double(), full.double()
    norm_a, norm_b = float(a.norm()), float(b.norm())
    return {'gradient_norm': norm_a, 'full_gradient_norm': norm_b,
            'relative_l2_error': float((a - b).norm()) / max(norm_b, 1e-300),
            'cosine': float(torch.dot(a, b)) / max(norm_a * norm_b, 1e-300),
            'both_zero': norm_a == 0. and norm_b == 0.}


def run(config):
    plan = check_config(config)  # Before loading models or checkpoints.
    import torch
    from .data import rows, FactStream, LanguageStream, route_batches
    from .meta import rollout, QueryStream, functional_loss
    from .model import AdapterModel, memory, synchronize
    from .noise import canonical_noise
    from .runtime import Run, atomic_save, seed_all
    from .transition import Phi, scalar_stats, detach_tree, zero_state

    record = Run(config['out'], config)
    seed_all(config['data_seed'])
    parent = torch.load(config['parent'], map_location='cpu', weights_only=False)
    pc = parent['config']
    if pc['data'] != config['retain_data'] or pc['organization'] != 'O1':
        raise ValueError('Horizon parent is not the declared learned development parent')
    parent_result = json.loads((Path(config['parent']).parent / 'result.json').read_text())
    if not parent_result.get('acquisition_gate_passed'):
        raise ValueError('Horizon parent has not passed acquisition')
    model = AdapterModel(pc['model'], pc['rank'], pc['targets'], 'O1', config['device'],
                         scale=pc['scale'])
    base_digest = model.base_digest()
    record.log('model', **model.summary())
    phi = Phi(config['architecture']).to(config['device'])
    saved = torch.load(config['phi'], map_location='cpu', weights_only=False)
    if saved['config']['architecture'] != config['architecture'] or saved['inner_updates'] < 2560:
        raise ValueError('Horizon diagnostic needs the trained development-selected core phi')
    phi.load_state_dict(saved['phi'])
    phi_digest = tree_digest(phi.state_dict())
    clean, _ = canonical_noise(parent['adapter'], model.specs, 0., config['noise_seed'])
    model.load_adapter(clean)
    stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
    initial, corruption = canonical_noise(parent['adapter'], model.specs,
                                          config['q'], config['noise_seed'])
    initial = {k: v.to(config['device']) for k, v in initial.items()}
    streams = {
        'task': FactStream(rows(config['data'], 'support'), model.tokenizer,
                           config['data_seed'], config['seq']),
        'keep': FactStream(rows(config['retain_data'], 'support'), model.tokenizer,
                           config['data_seed'], config['seq']),
        'language': LanguageStream(config['language_support'], model.tokenizer,
                                   config['seq'], config['data_seed'])}
    queries = {}
    for key, file in [('task', config['data']), ('keep', config['retain_data'])]:
        qs = QueryStream(rows(file, 'query'), model.tokenizer,
                         seed=config['query_seed'], seq=config['seq'])
        queries[key], _ = route_batches(qs, config['device'], config['effective_tokens'])
    qs = LanguageStream(config['language_query'], model.tokenizer,
                        config['seq'], config['query_seed'])
    queries['language'], _ = route_batches(qs, config['device'], config['effective_tokens'])
    with torch.no_grad():
        reference = {key: functional_loss(model, {}, batches).detach()
                     for key, batches in queries.items()}
    query_digest = tree_digest(queries)
    initial_digest = tree_digest(initial)
    phi.zero_grad(set_to_none=True)
    if config['device'].startswith('musa'):
        torch.musa.reset_peak_memory_stats()
    synchronize(config['device'])
    start = time.time()
    horizon = 'H1' if config['tail'] == 1 else f"EXACT_H{config['tail']}_tail"
    endpoint, state, diag = rollout(
        model, initial, zero_state(initial), phi, streams, stats,
        config['K'], config['q'], config['inner_lr'], config['effective_tokens'],
        config['retain_weight'], horizon, queries, reference, config['penalty'], config['chunk'],
        lambda **kw: record.log('inner', **kw))
    synchronize(config['device'])
    seconds = time.time() - start
    gradient = torch.cat([p.grad.detach().cpu().flatten() if p.grad is not None
                          else torch.zeros(p.numel(), dtype=p.dtype) for p in phi.parameters()])
    if not torch.isfinite(gradient).all() or not all(torch.isfinite(v).all() for v in endpoint.values()):
        raise RuntimeError('Nonfinite diagnostic endpoint or gradient')
    end = {'adapter': {k: v.cpu() for k, v in endpoint.items()},
           'state': torch.utils._pytree.tree_map_only(torch.Tensor, lambda x: x.detach().cpu(), state),
           'streams': {k: v.state_dict() for k, v in streams.items()},
           'queries_sha256': query_digest, 'initial_sha256': initial_digest,
           'terminal': {k: torch.tensor(v, dtype=torch.float64) for k, v in diag.items()}}
    comparisons = None
    if config['reference_endpoint']:
        other = torch.load(config['reference_endpoint'], map_location='cpu', weights_only=False)
        comparisons = compare_trees(end, other)
    else:
        atomic_save(end, Path(config['out']) / 'endpoint.pt')
    if model.base_digest() != base_digest or tree_digest(phi.state_dict()) != phi_digest:
        raise RuntimeError('Diagnostic unexpectedly changed frozen base or phi')
    atomic_save({'gradient': gradient, 'config': config, 'terminal': diag,
                 'endpoint_sha256': tree_digest(end)}, Path(config['out']) / 'gradient.pt')
    result = {'status': 'PASS', 'diagnostic_status': 'COMPLETE', 'config': config,
              'seconds': seconds, 'terminal': diag, 'memory': memory(config['device']),
              'gradient_norm': float(gradient.double().norm()), 'endpoint_sha256': tree_digest(end),
              'endpoint_matches_H1': comparisons, 'base_unchanged': True, 'phi_unchanged': True,
              'optimizer_updates': 0, 'compute_counters': model.compute_counters,
              'noise': {k: v for k, v in corruption.items() if k != 'factors'},
              'phi_sha256': plan['artifact_sha256'][config['phi']],
              'scope': 'A few fixed development query batches; derivative truncation, not full recovery effectiveness.'}
    record.result(**result)
    record.log('diagnostic_complete', **result)
    return result


def limitation(error, device):
    message = str(error).lower()
    if device.startswith('musa') and 'musa' in message and 'out of memory' in message:
        return 'NOT_AVAILABLE_MEMORY'
    if device.startswith('musa') and ('musa' in message and
                                    ('not implemented' in message or 'could not run' in message)):
        return 'NOT_AVAILABLE_BACKEND'
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if Path(config['out']).exists():
        raise RuntimeError('Preserve prior horizon output; inspect it before a new run')
    import torch
    if config['device'].startswith('musa'):
        import torch_musa
    torch.set_num_threads(8)
    try:
        run(config)
    except Exception as error:
        reason = limitation(error, config['device'])
        result = {'status': 'PASS' if reason else 'FAIL',
                  'diagnostic_status': reason or 'ERROR', 'config': config,
                  'error': repr(error), 'traceback': traceback.format_exc(),
                  'derivative_obtained': False,
                  'scope': 'An execution limitation does not provide the missing full derivative.'}
        path = Path(config['out'])
        path.mkdir(parents=True, exist_ok=True)
        (path / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        if not reason:
            raise


if __name__ == '__main__':
    main()
