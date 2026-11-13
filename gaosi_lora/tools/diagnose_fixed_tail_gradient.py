"""Compare real H2 gradients with finite differences at a fixed tail boundary.

The K-2 prefix, all moment/history state and both tail support batches stay
fixed while phi varies. BF16 differences diagnose scale, not exact derivatives.
Only a new isolated development output and the existing device guard are used.
"""
import argparse
import inspect
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate(config):
    from diagnose_traced_episode import validate as validate_replay
    output = validate_replay(config)
    if config.get('tail_epsilons') != [.001, .01, .1]:
        raise ValueError('Use the three declared fixed-tail sensitivity scales')
    if config.get('capture_boundary') is not True:
        raise ValueError('Preserve the exact numerical tail boundary')
    return output


def run_captured(meta, torch, atomic_save, output, epsilons, *args, **kwargs):
    """Run the original analytic rollout once; observe inputs without replacing it."""
    original_rollout = meta.rollout
    bound = inspect.signature(original_rollout).bind(*args, **kwargs)
    bound.apply_defaults()
    values = bound.arguments
    if values['horizon'] != 'EXACT_H2_tail' or 'keep' not in values['streams']:
        raise ValueError('Require exact H2 and all three support routes')
    K, model, phi = values['K'], values['model'], values['phi']
    base_before = model.base_digest() if hasattr(model, 'base_digest') else None
    original_prepare, original_support = meta.prepare, meta.support_gradients
    original_phi = {k: p.detach().clone() for k, p in phi.named_parameters()}
    boundary, batches, inner = {}, [], []
    callback = values['log_callback']

    def logged(**row):
        inner.append(row)
        if callback:
            callback(**row)

    def support(model, parameters, route, create_graph=False):
        if create_graph:
            batches.append(tuple(route))
        return original_support(model, parameters, route, create_graph)

    def prepared(parameters, gd, gr, state, *rest, **options):
        if state['step'] == K - 2:
            if boundary:
                raise RuntimeError('Tail boundary captured twice')
            # Production transitions are out of place. These detached views
            # keep the numerical inputs alive without synchronizing the GPU.
            boundary.update(parameters=meta.detach_tree(parameters),
                            state=meta.detach_tree(state))
        return original_prepare(parameters, gd, gr, state, *rest, **options)

    values['log_callback'] = logged
    meta.prepare, meta.support_gradients = prepared, support
    try:
        analytic = original_rollout(**values)
    finally:
        meta.prepare, meta.support_gradients = original_prepare, original_support
    if not boundary or len(batches) != 6 or len(inner) != K:
        raise RuntimeError('Incomplete fixed H2 boundary or route evidence')

    from gaosi.horizon import tree_digest
    from gaosi.transition import detach_tree, leaf_parameters
    cpu = lambda x: torch.utils._pytree.tree_map_only(
        torch.Tensor, lambda t: t.detach().cpu().clone(), x)
    boundary.update(batches=batches, queries=values['queries'], reference=values['reference'],
                    parent_stats=values['parent_stats'], K=K, q=values['q'],
                    eta=values['eta'], retain_weight=values['retain_weight'],
                    penalty=values['penalty'], chunk=values['chunk'])
    frozen = cpu(boundary)
    boundary_digest = tree_digest(frozen)
    atomic_save(frozen, output / 'tail_boundary.pt')
    del frozen
    gradient = {k: p.grad.detach().cpu().clone() if p.grad is not None else None
                for k, p in phi.named_parameters()}
    atomic_save({'phi': cpu(original_phi), 'gradient': gradient,
                 'terminal': analytic[2], 'tail_boundary_sha256': boundary_digest},
                output / 'tail_analytic.pt')
    flat = torch.cat([g.flatten().double() for g in gradient.values() if g is not None])
    if not bool(flat.isfinite().all()) or not float(flat.norm()):
        raise RuntimeError('Nonfinite or zero analytic gradient; evidence preserved')
    norm = float(flat.norm())
    direction = {k: (gradient[k].to(p) / norm if gradient[k] is not None
                     else torch.zeros_like(p)) for k, p in phi.named_parameters()}

    def numerical():
        work = leaf_parameters(boundary['parameters'])
        state = detach_tree(boundary['state'])
        recorded = []
        for offset in range(2):
            route = batches[3 * offset:3 * (offset + 1)]
            ld, gd = original_support(model, work, route[0], True)
            ll, gl = original_support(model, work, route[1], True)
            lk, gk = original_support(model, work, route[2], True)
            lr = .5 * ll + .5 * lk
            gr = {k: .5 * gl[k] + .5 * gk[k] for k in work}
            with torch.no_grad():
                item, diag = original_prepare(
                    work, gd, gr, state, model.specs, values['parent_stats'], ld, lr,
                    K, values['q'], values['retain_weight'],
                    layers=model.llm.config.num_hidden_layers)
                work, state = meta.apply_prepared(item, phi, values['eta'],
                                                  diag['step'], values['chunk'])
            recorded.append({'k': K - 1 + offset, 'ld': float(ld.detach().cpu()),
                             'lr': float(lr.detach().cpu())})
            work, state = leaf_parameters(work), detach_tree(state)
            del item, ld, gd, ll, gl, lk, gk, lr, gr
        with torch.no_grad():
            _, diag = meta.terminal_objective(model, work, values['queries'],
                                              values['reference'], penalty=values['penalty'])
        return diag, recorded

    result = {'status': 'SENSITIVITY_RUNNING', 'method_validated': False,
              'prefix_and_all_boundary_state_fixed': True, 'phi_updates': 0,
              'core_budget_credit': 0, 'raw_gradient_norm_fp64': norm,
              'tail_boundary_sha256': boundary_digest, 'analytic_terminal': analytic[2],
              'analytic_tail_inner': [{k: r[k] for k in ['k', 'ld', 'lr']}
                                      for r in inner[-2:]], 'measurements': [],
              'limitations': ['BF16 rounding prevents treating these differences as an FP64 proof.',
                              'A normal isolated replay does not validate previous giant gradients.']}
    try:
        baseline, baseline_inner = numerical()
        result.update(baseline_terminal=baseline, baseline_tail_inner=baseline_inner,
                      baseline_terminal_exactly_equal=baseline == analytic[2],
                      baseline_inner_exactly_equal=baseline_inner == result['analytic_tail_inner'])
        (output / 'tail_sensitivity.json').write_text(json.dumps(result, indent=2) + '\n')
        if not result['baseline_terminal_exactly_equal'] or not result['baseline_inner_exactly_equal']:
            raise RuntimeError('Unperturbed frozen-tail values differ; do not interpret differences')
        for epsilon in epsilons:
            row = {'epsilon': epsilon, 'values': []}
            for sign in [1, -1]:
                with torch.no_grad():
                    for name, p in phi.named_parameters():
                        p.copy_(original_phi[name] + sign * epsilon * direction[name])
                terminal, logged_inner = numerical()
                row['values'].append({'sign': sign, 'terminal': terminal,
                                      'tail_inner': logged_inner})
            observed = (row['values'][0]['terminal']['objective']
                        - row['values'][1]['terminal']['objective']) / (2 * epsilon)
            row.update(central_difference=observed, analytic_directional=norm,
                       relative_discrepancy=abs(observed - norm) / max(abs(observed), norm))
            result['measurements'].append(row)
            (output / 'tail_sensitivity.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps({'event': 'fixed_tail_difference', **row}), flush=True)
    finally:
        with torch.no_grad():
            for name, p in phi.named_parameters():
                p.copy_(original_phi[name])
    if tree_digest(boundary) != boundary_digest:
        raise RuntimeError('Fixed boundary unexpectedly mutated')
    if any(not torch.equal(p, original_phi[n]) for n, p in phi.named_parameters()):
        raise RuntimeError('Phi was not restored')
    if base_before is not None and model.base_digest() != base_before:
        raise RuntimeError('Frozen base unexpectedly changed')
    result.update(status='SENSITIVITY_COMPLETE_REQUIRES_REVIEW',
                  boundary_unchanged=True, phi_unchanged=True,
                  base_unchanged=True if base_before is not None else None)
    (output / 'tail_sensitivity.json').write_text(json.dumps(result, indent=2) + '\n')
    return analytic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    output = validate(config)
    if args.validate_only:
        print(json.dumps({'status': 'VALIDATED_NOT_EXECUTED', 'phi_updates': 0,
                          'tail_steps': 2, 'numeric_tail_evaluations': 7}))
        return
    from diagnose_prefix_gradient import require_inherited_lock
    require_inherited_lock('/tmp/gaosi_musa_device0.lock')
    if os.environ.get('MUSA_LAUNCH_BLOCKING') != '1':
        raise RuntimeError('This fixed-tail sensitivity run requires process-local blocking')
    import diagnose_meta_gradient as diagnosis
    import gaosi.meta as meta
    from gaosi.runtime import atomic_save
    original = diagnosis.rollout
    diagnosis.rollout = lambda *a, **kw: run_captured(
        meta, diagnosis.torch, atomic_save, output, config['tail_epsilons'], *a, **kw)
    try:
        diagnosis.main()
    finally:
        diagnosis.rollout = original


if __name__ == '__main__':
    main()
