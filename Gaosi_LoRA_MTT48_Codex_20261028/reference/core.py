"""Gaosi-LoRA L1: executable SMALL-TENSOR reference, not an LLM trainer.

No CUDA dependency. Frozen base tensors stay in the computational graph.
LoRA convention: A [rank, in], B [out, rank], delta = scale * B @ A.
"""
from __future__ import annotations
import math
from typing import Mapping
import torch
from torch import nn
from torch.nn import functional as F


def lora_delta(A: torch.Tensor, B: torch.Tensor, scale: float = 1.0) -> torch.Tensor:
    if A.ndim != 2 or B.ndim != 2 or A.shape[0] != B.shape[1]:
        raise ValueError("A must be [r,in] and B [out,r]")
    return scale * (B @ A)


def effective_sqnorm(terms: list[tuple[torch.Tensor, torch.Tensor, float]]) -> torch.Tensor:
    """Exact squared Frobenius norm of sum(s*B*A), without a dense out×in matrix."""
    if not terms:
        raise ValueError("need at least one low-rank term")
    total = terms[0][0].new_zeros(())
    for Ai, Bi, si in terms:
        for Aj, Bj, sj in terms:
            total = total + (si * sj) * ((Bi.T @ Bj) * (Ai @ Aj.T)).sum()
    return total  # small negative roundoff may be clamped ONLY before sqrt/logging


@torch.no_grad()
def canonicalize(A: torch.Tensor, B: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """A.T=Q R -> Ac=Q.T, Bc=B R.T. Same B@A, no rank truncation.

    Noise/episode boundary only, NEVER inside a differentiable inner window.
    CPU FP32/FP64 is valid when MUSA has no QR; report that boundary transfer.
    """
    if A.shape[0] > A.shape[1]:
        raise ValueError("thin QR convention requires rank <= input width")
    Q, R = torch.linalg.qr(A.T, mode="reduced")
    sign = torch.where(R.diag() < 0, -torch.ones_like(R.diag()), torch.ones_like(R.diag()))
    Q = Q * sign.unsqueeze(0)
    R = sign.unsqueeze(1) * R
    return Q.T.contiguous(), (B @ R.T).contiguous()


@torch.no_grad()
def subspace_noise(A: torch.Tensor, B: torch.Tensor, q: float,
                   scale: float = 1.0, z: torch.Tensor | None = None
                   ) -> tuple[torch.Tensor, torch.Tensor]:
    """Canonical effective-adapter noise, C=s*Bc, Cq=sqrt(1-q)C+sqrt(q)rms(C)Z.

    Gaussian in the learned input subspace, NOT iid Gaussian in all base weights.
    In expectation preserves ||C||_F², not arbitrary distributions or abilities.
    Zero adapter remains zero: do not invent knowledge destruction at zero B.
    """
    if not 0 <= q <= 1 or scale <= 0:
        raise ValueError("q must be in [0,1], scale > 0")
    Ac, Bc = canonicalize(A, B)
    C = scale * Bc
    z = torch.randn_like(C) if z is None else z.to(C)
    if z.shape != C.shape:
        raise ValueError("noise shape must be [out,rank]")
    rms = C.square().mean().sqrt()
    Cq = math.sqrt(1-q) * C + math.sqrt(q) * rms * z
    return Ac, Cq / scale


@torch.no_grad()
def factor_noise(A: torch.Tensor, B: torch.Tensor, p: float,
                 za: torch.Tensor | None = None, zb: torch.Tensor | None = None
                 ) -> tuple[torch.Tensor, torch.Tensor]:
    """A/B factor VP-like RMS corruption; NOT effective-weight VP.

    Always compare after the SAME canonicalization for all groups. p is NOT q.
    """
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0,1]")
    A, B = canonicalize(A, B)
    za = torch.randn_like(A) if za is None else za.to(A)
    zb = torch.randn_like(B) if zb is None else zb.to(B)
    return (math.sqrt(1-p)*A + math.sqrt(p)*A.square().mean().sqrt()*za,
            math.sqrt(1-p)*B + math.sqrt(p)*B.square().mean().sqrt()*zb)


class LoRALinear(nn.Module):
    """Native functional-friendly LoRA. Inject AFTER choosing base dtype.

    This class deliberately does not auto-merge in eval()/train().
    All parameters except A/B are frozen, but input derivatives are preserved.
    Production can use a verified PEFT version with identical semantics instead.
    """
    def __init__(self, weight: torch.Tensor, rank: int, scale: float = 1.0,
                 bias: torch.Tensor | None = None, adapter_dtype=torch.float32):
        super().__init__()
        out_dim, in_dim = weight.shape
        if rank <= 0 or rank > min(in_dim, out_dim):
            raise ValueError("invalid rank")
        self.weight = nn.Parameter(weight.detach().clone(), requires_grad=False)
        self.bias = None if bias is None else nn.Parameter(bias.detach().clone(), requires_grad=False)
        self.A = nn.Parameter(torch.empty(rank, in_dim, device=weight.device, dtype=adapter_dtype))
        self.B = nn.Parameter(torch.zeros(out_dim, rank, device=weight.device, dtype=adapter_dtype))
        nn.init.kaiming_uniform_(self.A, a=math.sqrt(5))
        self.scale = float(scale)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        base = F.linear(x.to(self.weight.dtype), self.weight, self.bias)
        low = F.linear(F.linear(x.to(self.A.dtype), self.A), self.B)
        return base + self.scale * low.to(base.dtype)


class AdapterPhi(nn.Module):
    """48-feature shared factor controller, optional coordinate residual."""
    def __init__(self, coordinate: bool = False):
        super().__init__()
        self.coordinate = coordinate
        self.net = nn.Sequential(nn.Linear(48,256), nn.SiLU(),
                                 nn.Linear(256,256), nn.SiLU(),
                                 nn.Linear(256,18 if coordinate else 2))
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)
        if coordinate:
            self.coord = nn.Sequential(nn.Linear(24,32), nn.SiLU(), nn.Linear(32,1))
            nn.init.zeros_(self.coord[-1].weight)
            nn.init.zeros_(self.coord[-1].bias)

    def forward(self, features: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor | None]:
        raw = self.net(features)
        alpha = torch.exp(math.log(4) * torch.tanh(raw[..., :2]))
        return alpha, (raw[..., 2:] if self.coordinate else None)

    def residual(self, local8: torch.Tensor, context16: torch.Tensor) -> torch.Tensor:
        if not self.coordinate:
            raise ValueError("coordinate head disabled")
        context = context16.expand(*local8.shape[:-1], 16)
        return torch.tanh(self.coord(torch.cat((local8, context), dim=-1))).squeeze(-1)


def paired_adam_step(w, gd, gr, state, raw_coeff, step: int, lr=0.02,
                     wr=0.25, beta1=0.9, beta2=0.999, eps=1e-8, ridge=1e-12):
    """Pure differentiable single-tensor reference. No clipping in this toy.

    Production ridge=1e-16; FP64 toy may use 1e-12 for finite difference stability.
    raw_coeff is [2]; forward zero coefficients equal the paired baseline.
    """
    md, mr, v = state
    md = beta1*md + (1-beta1)*gd
    mr = beta1*mr + (1-beta1)*wr*gr
    g = gd + wr*gr
    v = beta2*v + (1-beta2)*g.square()
    den = (v/(1-beta2**step) + ridge).sqrt() + eps
    ud, ur = md/(1-beta1**step)/den, mr/(1-beta1**step)/den
    alpha = torch.exp(math.log(4)*torch.tanh(raw_coeff))
    return w - lr*(alpha[0]*ud + alpha[1]*ur), (md,mr,v)


def mse_forward(base, A, B, x, y):
    return (F.linear(x, base + B@A)-y).square().mean()


def toy_rollout(raw, base, A0, B0, batches, lr=0.02, ridge=1e-12):
    """Exact two-step adapter graph through a frozen base and moment states."""
    A, B = A0, B0
    state = {"A": tuple(torch.zeros_like(A) for _ in range(3)),
             "B": tuple(torch.zeros_like(B) for _ in range(3))}
    for t, (xd,yd,xr,yr) in enumerate(batches, start=1):
        ld = mse_forward(base,A,B,xd,yd)
        lrtn = mse_forward(base,A,B,xr,yr)
        gd = torch.autograd.grad(ld,(A,B),create_graph=True)
        gr = torch.autograd.grad(lrtn,(A,B),create_graph=True)
        A, state["A"] = paired_adam_step(A,gd[0],gr[0],state["A"],raw[0],t,lr=lr,ridge=ridge)
        B, state["B"] = paired_adam_step(B,gd[1],gr[1],state["B"],raw[1],t,lr=lr,ridge=ridge)
    return A, B, state
