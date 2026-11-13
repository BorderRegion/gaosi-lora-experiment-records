from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import time

import torch
from torch import nn
from torch.nn import functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

TARGETS = {
    'all7': {'q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'},
    'attention4': {'q_proj', 'k_proj', 'v_proj', 'o_proj'},
    'qv': {'q_proj', 'v_proj'},
}


class Branch(nn.Module):
    def __init__(self, rows, cols, rank, device, scale=1.):
        super().__init__()
        self.A = nn.Parameter(torch.empty(rank, cols, device=device, dtype=torch.float32))
        self.B = nn.Parameter(torch.zeros(rows, rank, device=device, dtype=torch.float32))
        nn.init.kaiming_uniform_(self.A, a=math.sqrt(5))
        self.scale = float(scale)

    def forward(self, x):
        return self.scale * F.linear(F.linear(x.to(self.A.dtype), self.A), self.B)


class NativeLoRA(nn.Module):
    def __init__(self, linear, rank, organization='O1', scale=1.):
        super().__init__()
        self.weight = linear.weight
        self.bias = linear.bias
        rows, cols = self.weight.shape
        roles = ['stable', 'plastic'] if organization == 'O3' else ['single']
        self.branches = nn.ModuleDict({role: Branch(rows, cols, rank // len(roles),
                                      self.weight.device, scale) for role in roles})
        self.enabled = True

    def forward(self, x):
        result = F.linear(x, self.weight, self.bias)
        if self.enabled:
            # Each branch has FP32 persistent parameters, even with BF16 base.
            # Casts retain the full activation/adapter derivative graph.
            low = sum(branch(x) for branch in self.branches.values())
            result = result + low.to(result.dtype)
        return result


@dataclass(frozen=True)
class FactorSpec:
    name: str
    layer: int
    module: str
    factor: str
    role: str
    rank: int
    scale: float
    pair_name: str


class AdapterModel(nn.Module):
    """Single loss entry for ordinary fitting, recovery, and functional meta use."""
    def __init__(self, path, rank=32, targets='all7', organization='O1',
                 device='musa', dtype=torch.bfloat16, scale=1.):
        super().__init__()
        self.llm = AutoModelForCausalLM.from_pretrained(
            path, dtype=dtype, local_files_only=True, attn_implementation='eager')
        self.llm.to(device)
        self.llm.requires_grad_(False)
        self.llm.config.use_cache = False
        self.llm.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.tokenizer.padding_side = 'right'
        self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        self.organization = organization
        self.path = path
        self.targets = targets
        self.rank = rank
        self.device_name = device
        self.compute_counters = {'loss_forwards': 0, 'loss_backwards': 0,
                                 'input_tokens': 0, 'supervised_tokens': 0,
                                 'hessian_vector_products': 0}
        self._specs = {}
        for name, module in list(self.llm.named_modules()):
            if name.split('.')[-1] not in TARGETS[targets] or not isinstance(module, nn.Linear):
                continue
            parent_name, _, child = name.rpartition('.')
            replacement = NativeLoRA(module, rank, organization, scale)
            setattr(self.llm.get_submodule(parent_name), child, replacement)
            depth = int(re.search(r'layers\.(\d+)', name).group(1))
            for role, branch in replacement.branches.items():
                for factor in ['A', 'B']:
                    full = f'llm.{name}.branches.{role}.{factor}'
                    pair = full[:-1] + ('B' if factor == 'A' else 'A')
                    self._specs[full] = FactorSpec(full, depth, child, factor, role,
                                                  branch.A.shape[0], scale, pair)
        if not self._specs:
            raise RuntimeError('No LoRA modules found')
        self.set_stage('night')

    @property
    def specs(self):
        return self._specs

    def adapter(self, trainable_only=False):
        return {k: p for k, p in self.named_parameters()
                if k in self.specs and (not trainable_only or p.requires_grad)}

    def set_stage(self, stage):
        for name, p in self.named_parameters():
            if name in self.specs:
                spec = self.specs[name]
                p.requires_grad_(not (self.organization == 'O2' and spec.factor == 'A')
                                 and not (self.organization == 'O3' and stage == 'day'
                                          and spec.role == 'stable'))
            else:
                p.requires_grad_(False)

    def forward(self, batch):
        if hasattr(self, 'compute_counters'):
            self.compute_counters['loss_forwards'] += 1
            self.compute_counters['input_tokens'] += int(batch['attention_mask'].sum().detach().cpu())
            self.compute_counters['supervised_tokens'] += int(batch['labels'][:, 1:].ne(-100).sum().detach().cpu())
        attention_mask = batch['attention_mask']
        if 'segment_ids' in batch:
            segments = batch['segment_ids']
            length = segments.shape[1]
            causal = torch.ones(length, length, dtype=torch.bool, device=segments.device).tril()
            same = segments.unsqueeze(2).eq(segments.unsqueeze(1))
            allowed = same & causal & segments.unsqueeze(1).ge(0)
            mask = torch.zeros(allowed.shape, dtype=next(self.llm.parameters()).dtype,
                               device=segments.device).masked_fill(~allowed,
                               torch.finfo(next(self.llm.parameters()).dtype).min)
            attention_mask = {'full_attention': mask.unsqueeze(1)}
        outputs = self.llm.model(input_ids=batch['input_ids'],
                                 attention_mask=attention_mask,
                                 position_ids=batch.get('position_ids'), use_cache=False)
        hidden = outputs.last_hidden_state[:, :-1]
        target = batch['labels'][:, 1:]
        mask = target.ne(-100)
        selected = hidden[mask]
        # Avoid allocating logits for unsupervised prompt tokens. This is
        # precisely the same answer-token CE as the ordinary causal LM loss.
        logits = self.llm.lm_head(selected)
        logits = logits if logits.dtype == torch.float64 else logits.float()
        loss = F.cross_entropy(logits, target[mask], reduction='sum') / mask.sum()
        if loss.requires_grad and hasattr(self, 'compute_counters'):
            def count_backward(gradient):
                self.compute_counters['loss_backwards'] += 1
                return gradient
            loss.register_hook(count_backward)
        return loss

    @torch.no_grad()
    def load_adapter(self, state):
        current = self.adapter()
        if set(current) != set(state):
            raise ValueError('Adapter key mismatch')
        for name, value in state.items():
            current[name].copy_(value.to(current[name]))

    def adapter_cpu(self):
        return {k: v.detach().cpu().clone() for k, v in self.adapter().items()}

    def add_scratch(self, rank=16):
        """Extra common-capacity probe, removed without modifying main state."""
        added = []
        for name, module in self.named_modules():
            if not isinstance(module, NativeLoRA):
                continue
            module.branches['scratch'] = Branch(module.weight.shape[0], module.weight.shape[1],
                                                rank, module.weight.device)
            template_name = next(k for k in self.specs if k.startswith(name + '.branches.'))
            template = self.specs[template_name]
            for factor in ['A', 'B']:
                key = name + '.branches.scratch.' + factor
                self.specs[key] = FactorSpec(key, template.layer, template.module, factor,
                                             'scratch', rank, 1., key[:-1] + ('B' if factor == 'A' else 'A'))
                added.append(key)
        for name, p in self.named_parameters():
            p.requires_grad_(name in added)
        return added

    def remove_scratch(self):
        for module in self.modules():
            if isinstance(module, NativeLoRA) and 'scratch' in module.branches:
                del module.branches['scratch']
        for key in [k for k, v in self.specs.items() if v.role == 'scratch']:
            del self.specs[key]
        self.set_stage('night')

    @torch.no_grad()
    def base_digest(self):
        digest = hashlib.sha256()
        for name, p in self.named_parameters():
            if name in self.specs:
                continue
            digest.update(name.encode())
            digest.update(p.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
        return digest.hexdigest()

    def summary(self):
        return {'path': self.path, 'rank': self.rank, 'targets': self.targets,
                'organization': self.organization, 'adapter_parameters': sum(p.numel() for p in self.adapter().values()),
                'trainable_parameters': sum(p.numel() for p in self.adapter(True).values()),
                'base_dtype': str(next(self.llm.parameters()).dtype),
                'adapter_dtypes': sorted({str(p.dtype) for p in self.adapter().values()}),
                'attention': self.llm.config._attn_implementation, 'use_cache': False,
                'actual_device': str(next(self.llm.parameters()).device)}


def synchronize(device='musa'):
    if str(device).startswith('musa'):
        torch.musa.synchronize()
    elif str(device).startswith('cuda'):
        torch.cuda.synchronize()


def memory(device='musa'):
    import psutil
    result = {'cpu_rss_bytes': psutil.Process().memory_info().rss}
    if str(device).startswith('musa'):
        result.update(allocated_bytes=torch.musa.memory_allocated(),
                      reserved_bytes=torch.musa.memory_reserved(),
                      peak_allocated_bytes=torch.musa.max_memory_allocated(),
                      peak_reserved_bytes=torch.musa.max_memory_reserved())
    return result
