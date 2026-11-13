"""Exact batchwise VJP/HVP recomputation; no first-order approximation.

Save adapter inputs and deterministic token batches, not all transformer
activations across every accumulated support/query microbatch. The reverse
pass sums all coordinate/statistic adjoints before each LLM HVP is evaluated.
"""
from __future__ import annotations

import torch
from torch.func import functional_call

from .data import token_count


class _RecomputedSupport(torch.autograd.Function):
    @staticmethod
    def forward(ctx, model, names, batches, *parameters):
        ctx.model, ctx.names, ctx.batches = model, names, batches
        ctx.save_for_backward(*parameters)
        count = sum(token_count(b) for b in batches)
        ctx.count = count
        mean = parameters[0].new_zeros(())
        gradients = [torch.zeros_like(p) for p in parameters]
        for batch in batches:
            with torch.enable_grad():
                work = tuple(p.detach().requires_grad_(True) for p in parameters)
                loss = functional_call(model, dict(zip(names, work)), (batch,), strict=False)
                loss = loss * token_count(batch) / count
                gs = torch.autograd.grad(loss, work, create_graph=True)
            mean = mean + loss.detach()
            for i, g in enumerate(gs):
                gradients[i].add_(g.detach())
            del work, loss, gs
        return (mean, *gradients)

    @staticmethod
    def backward(ctx, loss_adjoint, *gradient_adjoints):
        # For each batch, derivative of L is g, derivative of g is H.
        # Return loss_adjoint*g + H.T@gradient_adjoints exactly, summing over
        # microbatches. No dependence on phi or adapter factors is detached.
        original = ctx.saved_tensors
        if original and original[0].device.type == 'musa':
            torch.musa.synchronize()
        output = [torch.zeros_like(p) for p in original]
        for batch in ctx.batches:
            with torch.enable_grad():
                work = tuple(p.detach().requires_grad_(True) for p in original)
                loss = functional_call(ctx.model, dict(zip(ctx.names, work)), (batch,), strict=False)
                loss = loss * token_count(batch) / ctx.count
                gradients = torch.autograd.grad(loss, work, create_graph=True)
                product = sum((g * v).sum() for g, v in zip(gradients, gradient_adjoints)
                              if v is not None)
                if loss_adjoint is not None:
                    product = product + loss_adjoint * loss
                hessian_vectors = torch.autograd.grad(product, work, allow_unused=True)
                if hasattr(ctx.model, 'compute_counters'):
                    ctx.model.compute_counters['hessian_vector_products'] += 1
            for i, h in enumerate(hessian_vectors):
                if h is not None:
                    output[i].add_(h)
        if original and original[0].device.type == 'musa':
            torch.musa.synchronize()
        return (None, None, None, *output)


def recomputed_support(model, parameters, batches):
    loss, *values = _RecomputedSupport.apply(model, tuple(parameters), tuple(batches), *parameters.values())
    return loss, dict(zip(parameters, values))


class _RecomputedLoss(torch.autograd.Function):
    @staticmethod
    def forward(ctx, model, names, batches, *parameters):
        ctx.model, ctx.names, ctx.batches = model, names, batches
        ctx.save_for_backward(*parameters)
        count = sum(token_count(b) for b in batches)
        ctx.count = count
        total = parameters[0].new_zeros(())
        for batch in batches:
            total = total + functional_call(model, dict(zip(names, parameters)), (batch,), strict=False) * token_count(batch) / count
        return total

    @staticmethod
    def backward(ctx, loss_adjoint):
        output = [torch.zeros_like(p) for p in ctx.saved_tensors]
        for batch in ctx.batches:
            with torch.enable_grad():
                work = tuple(p.detach().requires_grad_(True) for p in ctx.saved_tensors)
                loss = functional_call(ctx.model, dict(zip(ctx.names, work)), (batch,), strict=False)
                grads = torch.autograd.grad(loss * token_count(batch) / ctx.count, work)
            for i, g in enumerate(grads):
                output[i].add_(loss_adjoint * g)
        return (None, None, None, *output)


def recomputed_loss(model, parameters, batches):
    return _RecomputedLoss.apply(model, tuple(parameters), tuple(batches), *parameters.values())
