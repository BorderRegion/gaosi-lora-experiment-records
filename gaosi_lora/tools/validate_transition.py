"""Independent production-transition derivatives on a real tiny Qwen.

CPU FP64 is a numerical reference, not proof of MUSA/full-model readiness.
"""
import argparse
import json
from pathlib import Path
import sys
import types
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from torch.func import functional_call
from transformers import Qwen2Config, Qwen2ForCausalLM
from transformers.models.qwen2 import modeling_qwen2

from gaosi.model import AdapterModel, FactorSpec, NativeLoRA, TARGETS
from gaosi.recompute import recomputed_loss, recomputed_support
from gaosi.transition import (Phi, zero_state, scalar_stats, transition, prepare,
                              apply_prepared, detach_tree, h1_replay, leaf_parameters)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='checks/production_transition_cpu.json')
    a = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(1701)
    config = Qwen2Config(vocab_size=41, hidden_size=16, intermediate_size=32,
                         num_hidden_layers=2, num_attention_heads=2,
                         num_key_value_heads=1, head_dim=8,
                         attention_dropout=0., max_position_embeddings=64,
                         tie_word_embeddings=False)
    config._attn_implementation = 'eager'
    model = Qwen2ForCausalLM(config).double().eval()
    model.requires_grad_(False)
    # An explicit mathematical FP64 reference only: upstream RMSNorm/attention
    # force FP32 even when the model is double. Production MUSA retains the
    # upstream operators; they receive separate real-model backend probes.
    def rmsnorm_fp64(self, x):
        return self.weight * x * torch.rsqrt(x.square().mean(-1, keepdim=True) + self.variance_epsilon)
    for module in model.modules():
        if isinstance(module, modeling_qwen2.Qwen2RMSNorm):
            module.forward = types.MethodType(rmsnorm_fp64, module)

    def attention_fp64(module, query, key, value, attention_mask, scaling, dropout=0., **kwargs):
        key = modeling_qwen2.repeat_kv(key, module.num_key_value_groups)
        value = modeling_qwen2.repeat_kv(value, module.num_key_value_groups)
        weights = query @ key.transpose(2, 3) * scaling
        if attention_mask is not None:
            weights = weights + attention_mask
        weights = weights.softmax(-1)
        return (weights @ value).transpose(1, 2).contiguous(), weights
    modeling_qwen2.eager_attention_forward = attention_fp64
    specs = {}
    for name, module in list(model.named_modules()):
        if name.split('.')[-1] in TARGETS['all7'] and isinstance(module, torch.nn.Linear):
            parent, _, child = name.rpartition('.')
            layer = NativeLoRA(module, 2).double()
            with torch.no_grad():
                layer.branches.single.B.normal_(0., .02)
            setattr(model.get_submodule(parent), child, layer)
            for factor in ['A', 'B']:
                full = name + '.branches.single.' + factor
                specs[full] = FactorSpec(full, int(name.split('.')[2]), child, factor,
                                         'single', 2, 1., full[:-1] + ('B' if factor == 'A' else 'A'))
    params = {k: v for k, v in model.named_parameters() if k in specs}
    wrapper = AdapterModel.__new__(AdapterModel)
    torch.nn.Module.__init__(wrapper)
    wrapper.llm = model
    def token_batch(ids):
        return {'input_ids': ids, 'labels': ids, 'attention_mask': torch.ones_like(ids)}

    def rec_loss(work, batch):
        return recomputed_loss(wrapper, {'llm.' + k: v for k, v in work.items()}, [token_batch(batch)])

    def rec_support(work, batch):
        value, gradients = recomputed_support(wrapper, {'llm.' + k: v for k, v in work.items()}, [token_batch(batch)])
        return value, {k.removeprefix('llm.'): v for k, v in gradients.items()}
    parent_stats = detach_tree({k: scalar_stats(v) for k, v in params.items()})
    data = [torch.randint(0, 41, (1, 9)) for _ in range(5)]

    def loss(work, batch):
        logits = functional_call(model, work, (), {'input_ids': batch,
                                                   'use_cache': False}, strict=False).logits
        # Transformers' stock loss casts logits to FP32, which would destroy
        # the accuracy of this explicitly FP64 finite-difference reference.
        return torch.nn.functional.cross_entropy(logits[:, :-1].reshape(-1, config.vocab_size),
                                                  batch[:, 1:].reshape(-1))

    output = {'scope': 'CPU FP64 production transition on tiny Qwen RMSNorm/SiLU/eager attention',
              'reference_precision_changes': ['RMSNorm and attention softmax retain FP64 instead of upstream forced FP32',
                                               'cross entropy retains FP64; real MUSA backend is checked separately'],
              'tests': {}, 'layers': 2, 'adapter_parameters': sum(v.numel() for v in params.values())}
    for architecture in ['L', 'X']:
        phi = Phi(architecture).double()
        # Nonzero heads exercise history and coordinate/context dependencies,
        # rather than only testing the initially constant update map.
        with torch.no_grad():
            phi.net[-1].weight.normal_(0., .01)
            phi.net[-1].bias.normal_(0., .01)
            if architecture == 'X':
                phi.coord[-1].weight.normal_(0., .01)
        original_phi = {k: v.detach().clone() for k, v in phi.named_parameters()}
        directions = {k: torch.zeros_like(v) for k, v in phi.named_parameters()}
        directions['net.4.bias'][:2] = torch.tensor([.7, -.3], dtype=torch.float64)
        if architecture == 'X':
            directions['coord.2.bias'].fill_(.2)
            directions['net.4.bias'][2:] = .05

        def objective(nsteps=2, recompute=False):
            work = leaf_parameters(params)
            state = zero_state(work)
            for step in range(nsteps):
                if recompute:
                    ld, gd = rec_support(work, data[2 * step])
                    lr, gr = rec_support(work, data[2 * step + 1])
                else:
                    ld, lr = loss(work, data[2 * step]), loss(work, data[2 * step + 1])
                    gdv = torch.autograd.grad(ld, tuple(work.values()), create_graph=True)
                    grv = torch.autograd.grad(lr, tuple(work.values()), create_graph=True)
                    gd, gr = dict(zip(work, gdv)), dict(zip(work, grv))
                work, state, _ = transition(work, gd, gr, state, specs, parent_stats,
                                            ld, lr, phi, .003, 2, .03, layers=2, chunk=17)
            return rec_loss(work, data[-1]) if recompute else loss(work, data[-1])

        J = objective()
        gradient = torch.autograd.grad(J, tuple(phi.parameters()), allow_unused=True)
        recomputed_J = objective(recompute=True)
        recomputed_grad = torch.autograd.grad(recomputed_J, tuple(phi.parameters()), allow_unused=True)
        torch.testing.assert_close(recomputed_J, J, atol=1e-12, rtol=1e-10)
        for direct, replayed in zip(gradient, recomputed_grad):
            if direct is not None:
                torch.testing.assert_close(replayed, direct, atol=1e-11, rtol=1e-9)
        output['tests'][architecture + '_exact_batchwise_recomputation'] = {'status': 'PASS'}
        analytic = sum(float((g * directions[k]).sum()) for (k, p), g in zip(phi.named_parameters(), gradient)
                       if g is not None)
        epsilon = 1e-4
        values = []
        for sign in [1, -1]:
            with torch.no_grad():
                for key, p in phi.named_parameters():
                    p.copy_(original_phi[key] + sign * epsilon * directions[key])
            values.append(float(objective().detach()))
        with torch.no_grad():
            for key, p in phi.named_parameters():
                p.copy_(original_phi[key])
        finite = (values[0] - values[1]) / (2 * epsilon)
        relative = abs(analytic - finite) / max(abs(analytic), abs(finite), 1e-12)
        assert relative < .002, (architecture, analytic, finite, relative)
        output['tests'][architecture + '_exact_H2'] = {'status': 'PASS', 'analytic': analytic,
                                                      'finite_difference': finite, 'relative_error': relative}

        # Independent H1 direct query autograd versus chunkwise chain replay.
        work = leaf_parameters(params)
        ld, lr = loss(work, data[0]), loss(work, data[1])
        gd = dict(zip(work, torch.autograd.grad(ld, tuple(work.values()))))
        gr = dict(zip(work, torch.autograd.grad(lr, tuple(work.values()))))
        with torch.no_grad():
            prepared, diag = prepare(work, gd, gr, zero_state(work), specs, parent_stats,
                                      ld, lr, 2, .03, layers=2)
        nextwork, _ = apply_prepared(prepared, phi, .003, 1, chunk=17)
        J = loss(nextwork, data[-1])
        gq = dict(zip(nextwork, torch.autograd.grad(J, tuple(nextwork.values()), retain_graph=True)))
        exact = torch.autograd.grad(J, tuple(phi.parameters()), allow_unused=True)
        phi.zero_grad(set_to_none=True)
        h1_replay(prepared, phi, .003, gq, chunk=17)
        errors = []
        for p, g in zip(phi.parameters(), exact):
            if g is None:
                continue
            torch.testing.assert_close(p.grad, g, atol=1e-11, rtol=1e-9)
            errors.append(float((p.grad - g).abs().max()))
        output['tests'][architecture + '_H1_independent_chain'] = {'status': 'PASS', 'max_abs_error': max(errors)}

        # Initial phi exactly reproduces the stated ridge Adam definition.
        zero_phi = Phi(architecture).double()
        nextwork, _ = apply_prepared(prepared, zero_phi, .003, 1, chunk=17)
        for key, item in prepared.items():
            torch.testing.assert_close(nextwork[key], item['w'] - .003 * (item['ud'] + item['ur']))
        output['tests'][architecture + '_initial_Adam_identity'] = {'status': 'PASS'}
    output['status'] = 'PASS'
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
