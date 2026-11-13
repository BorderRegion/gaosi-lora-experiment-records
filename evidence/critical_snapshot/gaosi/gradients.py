"""Stable outer-phi clipping; no change to LLM or HVP precision."""
import math

import torch


@torch.no_grad()
def stable_phi_clip_(parameters, max_norm=1.):
    """Apply the standard global L2 clip using CPU FP64 scalar arithmetic.

    Phi is small. Reduce its completed FP32 gradients on CPU, and normalize
    there before converting back to FP32. This also handles norms larger than
    the FP32 range without underflowing the scale on the device.
    """
    if max_norm <= 0 or not math.isfinite(max_norm):
        raise ValueError('max_norm must be finite and positive')
    gradients = [p.grad for p in parameters if p.grad is not None]
    if any(g.dtype != torch.float32 for g in gradients):
        raise ValueError('Production phi gradients must be FP32')
    cpu = [g.detach().cpu().double() for g in gradients]
    if not all(bool(torch.isfinite(g).all()) for g in cpu):
        raise RuntimeError('Nonfinite phi gradient elements; preserved without clipping')
    norm = math.sqrt(sum(float(g.square().sum()) for g in cpu))
    coefficient = min(1., max_norm / (norm + 1e-6))
    if coefficient < 1.:
        for original, value in zip(gradients, cpu):
            original.copy_((value * coefficient).to(device=original.device, dtype=original.dtype))
    return torch.tensor(norm, dtype=torch.float64)
