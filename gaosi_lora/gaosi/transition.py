"""One numerical transition for recovery and H1/H2 meta learning.

No query data, clean factor coordinates, or sampled noise are accepted here.
Parent information is scalar-only. Continuous statistics retain derivatives in
the exact H2 window. H1 detachment/replay is the caller's explicit decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import nn


def rms(x):
    return (x.square().mean() + 1e-24).sqrt()


def cosine(x, y):
    return (x * y).mean() / (rms(x) * rms(y) + 1e-24)


def logrms(x):
    return torch.clamp(torch.log(rms(x) + 1e-12), -30, 10) / 10


def scalar_stats(x):
    mean = x.mean()
    std = ((x - mean).square().mean() + 1e-24).sqrt()
    return {'mean': mean, 'std': std, 'rms': rms(x),
            'std_floor': std.clamp_min(1e-12), 'rms_floor': rms(x).clamp_min(1e-12)}


class Phi(nn.Module):
    def __init__(self, architecture='L'):
        super().__init__()
        self.architecture = architecture
        self.net = nn.Sequential(nn.Linear(48, 256), nn.SiLU(), nn.Linear(256, 256),
                                 nn.SiLU(), nn.Linear(256, 18 if architecture == 'X' else 2))
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)
        if architecture == 'X':
            self.coord = nn.Sequential(nn.Linear(24, 32), nn.SiLU(), nn.Linear(32, 1))
            nn.init.zeros_(self.coord[-1].weight)
            nn.init.zeros_(self.coord[-1].bias)

    def forward(self, features):
        raw = self.net(features)
        return torch.exp(math.log(4) * torch.tanh(raw[:2])), raw[2:]

    def coordinate(self, local, context):
        return torch.tanh(self.coord(torch.cat((local, context.expand(local.shape[0], -1)), -1))).squeeze(-1)


def zero_state(parameters):
    return {'moments': {name: (torch.zeros_like(w), torch.zeros_like(w), torch.zeros_like(w))
                        for name, w in parameters.items()},
            'history': {}, 'step': 0}


def detach_tree(value):
    if isinstance(value, torch.Tensor):
        return value.detach()
    if isinstance(value, dict):
        return {k: detach_tree(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(detach_tree(v) for v in value)
    return value


def leaf_parameters(parameters):
    return {k: v.detach().requires_grad_(True) for k, v in parameters.items()}


def metadata(spec, x, layers, pair_trainable):
    group = {'q_proj': 1, 'k_proj': 1, 'v_proj': 2, 'o_proj': 2,
             'gate_proj': 3, 'up_proj': 3, 'down_proj': 4}[spec.module]
    vals = [float(i == group) for i in range(6)]
    vals += [spec.layer / max(layers - 1, 1), 1., math.log1p(x.numel()) / 20,
             math.log1p(x.shape[0]) / 12, math.log1p(x.shape[1]) / 12,
             x.ndim / 4, 0., 0.]
    return vals


def features(spec, w, gd, gr, ud, ur, ld, lr, history, parent, step, K, q,
             pair_trainable, layers):
    gdr, grr = rms(gd), rms(gr)
    cs = cosine(gd, gr)
    if history is None:
        emald, emalr, emacos, emaupdate = ld, lr, cs, w.new_zeros(())
        dld, dlr = w.new_zeros(()), w.new_zeros(())
    else:
        emald = .9 * history['ld'] + .1 * ld
        emalr = .9 * history['lr'] + .1 * lr
        emacos = .9 * history['cos'] + .1 * cs
        emaupdate = .9 * history['update'] + .1 * history['last_update']
        dld, dlr = torch.tanh(ld - history['prev_ld']), torch.tanh(lr - history['prev_lr'])
    std = ((w - w.mean()).square().mean() + 1e-24).sqrt()
    values = [w.new_tensor(v) for v in metadata(spec, w, layers, pair_trainable)]
    values += [w.new_tensor(math.sqrt(q)), w.new_tensor(step / max(K - 1, 1)),
               w.new_tensor(math.log1p(step) / 5), torch.log1p(ld), torch.log1p(lr),
               logrms(gd), logrms(gr), cs, (gd * gr < 0).to(w.dtype).mean(),
               logrms(ud), logrms(ur), cosine(ud, ur), logrms(ud + ur),
               torch.tanh((w.mean() - parent['mean']) / (parent['rms'] + 1e-12)),
               torch.clamp(torch.log(std / (parent['std_floor'] + 1e-12)), -5, 5) / 5,
               torch.log1p(rms(w) / (parent['rms_floor'] + 1e-12)) / 5,
               w.new_tensor(math.sqrt(q) if spec.factor == 'B' else 0.),
               (gd.abs() > 5 * gdr).to(w.dtype).mean(),
               (gr.abs() > 5 * grr).to(w.dtype).mean(),
               emald, emalr, emacos, emaupdate, dld, dlr, w.new_tensor(1.),
               w.new_tensor(float(spec.factor == 'A')), w.new_tensor(float(spec.factor == 'B')),
               w.new_tensor(math.log1p(spec.rank) / math.log(129)), w.new_tensor(math.log(spec.scale) / 5),
               *[w.new_tensor(float(spec.role == r)) for r in ['single', 'stable', 'plastic']],
               w.new_tensor(float(pair_trainable))]
    result = torch.stack(values)
    assert result.shape == (48,), result.shape
    hist = {'ld': emald, 'lr': emalr, 'cos': emacos, 'update': emaupdate,
            'prev_ld': ld, 'prev_lr': lr}
    return result, hist


def prepare(parameters, gd, gr, state, specs, parent_stats, ld, lrtn, K, q,
            retain_weight=.25, clip_norm=1., layers=36):
    total_sq = sum((gd[k] + retain_weight * gr[k]).square().sum() for k in parameters)
    clip = (clip_norm / (total_sq.sqrt() + 1e-12)).clamp(max=1.)
    step = state['step'] + 1
    prepared = {}
    for name, w in parameters.items():
        d, r = gd[name] * clip, gr[name] * clip
        md, mr, v = state['moments'][name]
        md = .9 * md + .1 * d
        mr = .9 * mr + .1 * retain_weight * r
        v = .999 * v + .001 * (d + retain_weight * r).square()
        denominator = (v / (1 - .999 ** step) + 1e-16).sqrt() + 1e-8
        ud = md / (1 - .9 ** step) / denominator
        ur = mr / (1 - .9 ** step) / denominator
        spec = specs[name]
        f, history = features(spec, w, d, r, ud, ur, ld, lrtn,
                              state['history'].get(name), parent_stats[name], step - 1, K, q,
                              spec.pair_name in parameters, layers)
        prepared[name] = {'w': w, 'gd': d, 'gr': r, 'ud': ud, 'ur': ur,
                          'moments': (md, mr, v), 'features': f, 'history': history,
                          'parent': parent_stats[name],
                          'r': rms(ud + ur) - 1e-12,
                          'normalizers': {'ud': rms(ud), 'ur': rms(ur), 'total': rms(ud + ur),
                                          'gd': rms(d), 'gr': rms(r),
                                          'rootv': ((v + 1e-24).mean() + 1e-24).sqrt()}}
    return prepared, {'clip_factor': clip, 'unclipped_grad_norm': total_sq.sqrt(), 'step': step}


def coordinate_inputs(item, begin, end):
    w, gd, gr, ud, ur = [item[k].flatten()[begin:end] for k in ['w', 'gd', 'gr', 'ud', 'ur']]
    parent = item['parent']
    full = item
    normalizers = full['normalizers']
    rootv = (full['moments'][2].flatten()[begin:end] + 1e-24).sqrt()
    local = [torch.tanh((w - parent['mean']) / (parent['std_floor'] + 1e-12)),
             torch.tanh(ud / (normalizers['ud'] + 1e-12)),
             torch.tanh(ur / (normalizers['ur'] + 1e-12)),
             torch.tanh((ud + ur) / (normalizers['total'] + 1e-12)),
             torch.tanh(gd / (normalizers['gd'] + 1e-12)),
             torch.tanh(gr / (normalizers['gr'] + 1e-12)),
             torch.tanh(torch.log(rootv / (normalizers['rootv'] + 1e-12) + 1e-12)),
             torch.tanh(gd * gr / (normalizers['gd'] * normalizers['gr'] + 1e-24))]
    return torch.stack(local, -1)


def factor_delta(item, phi, eta, begin=None, end=None):
    alpha, context = phi(item['features'])
    ud, ur = item['ud'], item['ur']
    if begin is not None:
        ud, ur = ud.flatten()[begin:end], ur.flatten()[begin:end]
    delta = -eta * (alpha[0] * ud + alpha[1] * ur)
    if phi.architecture == 'X':
        first, last = (0, item['w'].numel()) if begin is None else (begin, end)
        e = phi.coordinate(coordinate_inputs(item, first, last), context)
        delta = delta + eta * .1 * item['r'] * e.reshape(delta.shape)
    return delta


def apply_prepared(prepared, phi, eta, step, chunk=262144):
    updated, moments, histories = {}, {}, {}
    for name, item in prepared.items():
        if phi.architecture == 'X':
            pieces = []
            for begin in range(0, item['w'].numel(), chunk):
                end = min(begin + chunk, item['w'].numel())
                if torch.is_grad_enabled():
                    from torch.utils.checkpoint import checkpoint
                    # Recompute only the coordinate map. Its input dict holds
                    # shared full-factor statistics; their adjoints add across
                    # all chunks before a single support Hessian-vector path.
                    def coordinate_chunk(values, first=begin, last=end):
                        return factor_delta(values, phi, eta, first, last)
                    pieces.append(checkpoint(coordinate_chunk, item, use_reentrant=False,
                                              preserve_rng_state=False))
                else:
                    pieces.append(factor_delta(item, phi, eta, begin, end))
            delta = torch.cat(pieces).reshape_as(item['w'])
        else:
            delta = factor_delta(item, phi, eta)
        updated[name] = item['w'] + delta
        moments[name] = item['moments']
        histories[name] = {**item['history'],
                           'last_update': rms(delta) / (item['parent']['rms_floor'] + 1e-12)}
    return updated, {'moments': moments, 'history': histories, 'step': step}


def transition(parameters, gd, gr, state, specs, parent_stats, ld, lrtn, phi,
               eta, K, q, retain_weight=.25, clip_norm=1., layers=36, chunk=262144):
    prepared, diag = prepare(parameters, gd, gr, state, specs, parent_stats, ld, lrtn,
                              K, q, retain_weight, clip_norm, layers)
    updated, next_state = apply_prepared(prepared, phi, eta, diag['step'], chunk)
    return updated, next_state, diag


def h1_replay(prepared, phi, eta, query_gradients, chunk=262144):
    """Recompute each coordinate head graph and SUM its phi contribution.

    All support/state/features are detached by the caller. No chunk retains a
    transformer graph; no mean across chunks or parameters changes the gradient.
    """
    for name, item in prepared.items():
        if phi.architecture == 'L':
            surrogate = (query_gradients[name].detach() * factor_delta(item, phi, eta)).sum()
            surrogate.backward()
        else:
            for begin in range(0, item['w'].numel(), chunk):
                end = min(begin + chunk, item['w'].numel())
                delta = factor_delta(item, phi, eta, begin, end)
                surrogate = (query_gradients[name].detach().flatten()[begin:end] * delta).sum()
                surrogate.backward()
