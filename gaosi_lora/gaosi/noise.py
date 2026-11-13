"""CPU FP32 boundary QR and independent, paired noise samples."""
import math
import time

import torch


def effective_squared(A, B, scale=1.):
    return scale ** 2 * ((B.T @ B) * (A @ A.T)).sum()


def composed_squared(terms):
    total = torch.zeros((), dtype=torch.float64)
    for Ai, Bi, si in terms:
        for Aj, Bj, sj in terms:
            total += si * sj * ((Bi.double().T @ Bj.double()) * (Ai.double() @ Aj.double().T)).sum()
    return float(total)


@torch.no_grad()
def canonical_noise(state, specs, q, seed, scheme='subspace'):
    started = time.time()
    generator = torch.Generator(device='cpu').manual_seed(seed)
    result, records = {}, []
    groups = {}
    for name, spec in specs.items():
        groups.setdefault(name.rsplit('.branches.', 1)[0], []).append(name)
        if spec.factor != 'A':
            continue
        a, b = state[name].detach().cpu().float(), state[spec.pair_name].detach().cpu().float()
        Q, R = torch.linalg.qr(a.T, mode='reduced')
        sign = torch.where(R.diag() < 0, -torch.ones_like(R.diag()), torch.ones_like(R.diag()))
        Q, R = Q * sign.unsqueeze(0), sign.unsqueeze(1) * R
        ac, bc = Q.T.contiguous(), (b @ R.T).contiguous()
        c = spec.scale * bc
        if scheme == 'subspace':
            z = torch.randn(c.shape, generator=generator)
            bn = (math.sqrt(1 - q) * c + math.sqrt(q) * c.square().mean().sqrt() * z) / spec.scale
            an = ac
        elif scheme == 'factor':
            an = math.sqrt(1 - q) * ac + math.sqrt(q) * ac.square().mean().sqrt() * torch.randn(ac.shape, generator=generator)
            bn = math.sqrt(1 - q) * bc + math.sqrt(q) * bc.square().mean().sqrt() * torch.randn(bc.shape, generator=generator)
        elif scheme == 'shrink':
            an, bn = ac, math.sqrt(1 - q) * bc
        elif scheme == 'uniform':
            z = (torch.rand(c.shape, generator=generator) * 2 - 1) * math.sqrt(3)
            an, bn = ac, (math.sqrt(1 - q) * c + math.sqrt(q) * c.square().mean().sqrt() * z) / spec.scale
        else:
            raise ValueError(scheme)
        result[name], result[spec.pair_name] = an, bn
        denominator = float(effective_squared(a, b, spec.scale))
        err = composed_squared([(ac, bc, spec.scale), (a, b, -spec.scale)])
        damage = composed_squared([(an, bn, spec.scale), (ac, bc, -spec.scale)])
        records.append({'name': name, 'clean_squared': denominator,
                        'noisy_squared': float(effective_squared(an, bn, spec.scale)),
                        'canonical_squared_error': err, 'damage_squared': damage,
                        'min_abs_R_diagonal': float(R.diag().abs().min()),
                        'relative_damage': math.sqrt(max(damage, 0.) / max(denominator, 1e-30))})
    # Both branches contribute cross terms in O3. No per-branch expectation is
    # incorrectly promoted into an energy-preserving composed update claim.
    clean_total, noisy_total, diff_total = 0., 0., 0.
    for names in groups.values():
        clean_terms, noisy_terms = [], []
        for name in names:
            spec = specs[name]
            if spec.factor == 'A':
                clean_terms.append((state[name].cpu(), state[spec.pair_name].cpu(), spec.scale))
                noisy_terms.append((result[name], result[spec.pair_name], spec.scale))
        clean_total += composed_squared(clean_terms)
        noisy_total += composed_squared(noisy_terms)
        diff_total += composed_squared(noisy_terms + [(a, b, -s) for a, b, s in clean_terms])
    diagnostics = {'q': q, 'scheme': scheme, 'noise_seed': seed, 'QR_device': 'cpu',
                   'boundary_seconds': time.time() - started, 'factors': records,
                   'composed_clean_squared_raw': clean_total,
                   'composed_noisy_squared_raw': noisy_total,
                   'composed_damage_squared_raw': diff_total,
                   'composed_relative_damage': math.sqrt(max(diff_total, 0.) / max(clean_total, 1e-30))}
    return result, diagnostics


@torch.no_grad()
def split_parent_single_to_dual(single_state, seed=17):
    result = {}
    generator = torch.Generator().manual_seed(seed)
    for name, a in single_state.items():
        if not name.endswith('.single.A'):
            continue
        b = single_state[name[:-1] + 'B']
        # Split the canonical rank-r parent, before independently canonicalizing
        # the resulting two branches at the common episode boundary.
        a, b = a.detach().cpu().float(), b.detach().cpu().float()
        Q, R = torch.linalg.qr(a.T, mode='reduced')
        sign = torch.where(R.diag() < 0, -torch.ones_like(R.diag()), torch.ones_like(R.diag()))
        a = (Q * sign.unsqueeze(0)).T.contiguous()
        b = (b @ (sign.unsqueeze(1) * R).T).contiguous()
        rank = a.shape[0]
        permutation = torch.randperm(rank, generator=generator)
        for role, idx in [('stable', permutation[:rank // 2]), ('plastic', permutation[rank // 2:])]:
            result[name.replace('.single.A', f'.{role}.A')] = a[idx].clone()
            result[name.replace('.single.A', f'.{role}.B')] = b[:, idx].clone()
    return result
