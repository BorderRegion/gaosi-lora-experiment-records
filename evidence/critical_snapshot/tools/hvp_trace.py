"""Read-only statistics and bounded failure captures around the real HVP."""
from pathlib import Path
import json

import torch

from gaosi import recompute
from gaosi.runtime import atomic_save


def install_sync_only():
    original = recompute._RecomputedSupport.backward

    def synchronized(ctx, loss_adjoint, *adjoints):
        torch.musa.synchronize()
        result = original(ctx, loss_adjoint, *adjoints)
        torch.musa.synchronize()
        return result

    recompute._RecomputedSupport.backward = staticmethod(synchronized)


def install(root):
    root = Path(root)
    original = recompute._RecomputedSupport.backward
    calls, captures = 0, 0

    def stats(values):
        values = [v.detach() for v in values if isinstance(v, torch.Tensor)]
        finite = bool(torch.stack([v.isfinite().all() for v in values]).all().cpu())
        maximum = float(torch.stack([v.abs().amax() for v in values]).amax().cpu()) if finite else None
        return {'finite': finite, 'max_abs': maximum}

    def cpu(value):
        if isinstance(value, torch.Tensor):
            return value.detach().cpu().clone()
        if isinstance(value, (tuple, list)):
            return [cpu(v) for v in value]
        if isinstance(value, dict):
            return {k: cpu(v) for k, v in value.items()}
        return value

    def traced(ctx, loss_adjoint, *adjoints):
        nonlocal calls, captures
        incoming = stats([loss_adjoint, *adjoints])
        output = original(ctx, loss_adjoint, *adjoints)
        outgoing = stats(output[3:])
        row = {'event': 'support_hvp_trace', 'call': calls, 'batches': len(ctx.batches),
               'incoming': incoming, 'outgoing': outgoing}
        if captures < 4 and (not incoming['finite'] or not outgoing['finite']
                             or incoming['max_abs'] > 1e6 or outgoing['max_abs'] > 1e6):
            file = root / f'hvp_capture_{calls}.pt'
            atomic_save({'parameters': dict(zip(ctx.names, cpu(ctx.saved_tensors))),
                         'batches': cpu(ctx.batches), 'loss_adjoint': cpu(loss_adjoint),
                         'gradient_adjoints': dict(zip(ctx.names, cpu(adjoints))),
                         'output': dict(zip(ctx.names, cpu(output[3:])))}, file)
            row['capture'] = str(file)
            captures += 1
        with (root / 'hvp_trace.jsonl').open('a') as handle:
            handle.write(json.dumps(row, allow_nan=False) + '\n')
        print(json.dumps(row, allow_nan=False), flush=True)
        calls += 1
        return output

    recompute._RecomputedSupport.backward = staticmethod(traced)
    return original
