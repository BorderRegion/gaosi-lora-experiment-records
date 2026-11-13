"""O4 FP32 persistent CPU master; explicit merges only at round boundaries."""
import hashlib
from pathlib import Path
import time

import torch

from .model import NativeLoRA
from .runtime import atomic_save


class MergeMaster:
    def __init__(self, model, checkpoint=None):
        self.merges = 0
        self.rank_sum = 0
        if checkpoint:
            state = checkpoint if isinstance(checkpoint, dict) else torch.load(checkpoint, map_location='cpu', weights_only=False)
            self.weights = state['weights']
            self.merges, self.rank_sum = state['merges'], state['rank_sum']
        else:
            # Initialize ONCE. Subsequent merges accumulate on these FP32
            # values, never on the repeatedly rounded BF16 working weights.
            self.weights = {k: v.detach().cpu().float().clone() for k, v in model.named_parameters()
                            if k not in model.specs}
        self.apply(model)

    @torch.no_grad()
    def apply(self, model):
        for name, p in model.named_parameters():
            if name not in model.specs:
                p.copy_(self.weights[name].to(p))

    @torch.no_grad()
    def merge(self, model, reset=True):
        started = time.time()
        rounding_sq, merged_sq = 0., 0.
        for name, module in model.named_modules():
            if not isinstance(module, NativeLoRA):
                continue
            master = self.weights[name + '.weight']
            for branch in module.branches.values():
                a, b = branch.A.detach().cpu(), branch.B.detach().cpu()
                for begin in range(0, master.shape[0], 512):
                    end = min(begin + 512, master.shape[0])
                    master[begin:end].add_(b[begin:end] @ a, alpha=branch.scale)
            working = master.to(module.weight.dtype)
            rounding_sq += float((working.float() - master).square().sum())
            merged_sq += float(master.square().sum())
            module.weight.copy_(working.to(module.weight.device))
        self.merges += 1
        self.rank_sum += model.rank
        if reset:
            for name, p in model.adapter().items():
                if model.specs[name].factor == 'A':
                    torch.nn.init.kaiming_uniform_(p, a=5 ** .5)
                else:
                    p.zero_()
        return {'merge_seconds': time.time() - started, 'merge_count': self.merges,
                'cumulative_rank_upper_bound': self.rank_sum,
                'working_BF16_rounding_sq': rounding_sq, 'FP32_master_sq': merged_sq,
                'adapter_reset': reset, 'KV_cache_discarded': True}

    def save(self, path, base_revision):
        atomic_save(self.state_dict(base_revision), path)

    def state_dict(self, base_revision):
        return {'weights': self.weights, 'merges': self.merges, 'rank_sum': self.rank_sum,
                'base_revision': base_revision, 'dtype': 'float32'}

    def digest(self):
        h = hashlib.sha256()
        for key, value in self.weights.items():
            h.update(key.encode())
            h.update(value.contiguous().view(torch.uint8).numpy().tobytes())
        return h.hexdigest()
