"""Compare full rollout plumbing to an independent direct K=2 query graph."""
import argparse
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from torch.func import functional_call
from transformers import Qwen2Config, Qwen2ForCausalLM
from gaosi.model import AdapterModel, FactorSpec, NativeLoRA
from gaosi.meta import rollout
from gaosi.transition import Phi, detach_tree, leaf_parameters, scalar_stats, transition, zero_state


class Batches:
    def __init__(self, batches):
        self.batches, self.cursor = batches, 0

    def batch(self, device):
        result = self.batches[self.cursor % len(self.batches)]
        self.cursor += 1
        return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='checks/meta_rollout_cpu.json')
    args = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(1991)
    q = Qwen2Config(vocab_size=31, hidden_size=8, intermediate_size=16, num_hidden_layers=1,
                    num_attention_heads=1, num_key_value_heads=1, head_dim=8, attention_dropout=0.)
    q._attn_implementation = 'eager'
    model = AdapterModel.__new__(AdapterModel)
    torch.nn.Module.__init__(model)
    model.llm = Qwen2ForCausalLM(q).double().eval()
    model.llm.requires_grad_(False)
    model._specs = {}
    module = model.llm.model.layers[0].self_attn.q_proj
    model.llm.model.layers[0].self_attn.q_proj = NativeLoRA(module, 2).double()
    with torch.no_grad():
        model.llm.model.layers[0].self_attn.q_proj.branches.single.B.normal_(0, .03)
    for factor in ['A', 'B']:
        key = 'llm.model.layers.0.self_attn.q_proj.branches.single.' + factor
        model.specs[key] = FactorSpec(key, 0, 'q_proj', factor, 'single', 2, 1., key[:-1] + ('B' if factor == 'A' else 'A'))
    values = model.adapter_cpu()
    stats = {k: scalar_stats(v) for k, v in values.items()}
    batches = []
    for index in range(9):
        ids = torch.randint(0, 31, (1, 6))
        batches.append({'input_ids': ids, 'labels': ids, 'attention_mask': torch.ones_like(ids)})
    queries = {'task': [batches[6]], 'language': [batches[7]], 'keep': [batches[8]]}
    reference = {'language': torch.tensor(-100.), 'keep': torch.tensor(-100.)}
    tests = {}
    for architecture in ['L', 'X']:
        phi = Phi(architecture).double()
        with torch.no_grad():
            phi.net[-1].weight.normal_(0, .01)
            if architecture == 'X':
                phi.coord[-1].weight.normal_(0, .01)
        work = leaf_parameters(values)
        state = zero_state(work)
        for step in range(2):
            losses = [functional_call(model, work, (batches[step * 3 + i],), strict=False)
                      for i in range(3)]
            ld, lr = losses[0], .5 * losses[1] + .5 * losses[2]
            gd = dict(zip(work, torch.autograd.grad(ld, tuple(work.values()), create_graph=True)))
            gr = dict(zip(work, torch.autograd.grad(lr, tuple(work.values()), create_graph=True)))
            work, state, _ = transition(work, gd, gr, state, model.specs, stats, ld, lr,
                                        phi, .001, 2, .03, layers=1, chunk=17)
        direct = sum(functional_call(model, work, (batches[i],), strict=False) for i in [6, 7, 8]) + 199.8
        direct_grad = torch.autograd.grad(direct, tuple(phi.parameters()))
        streams = {name: Batches([batches[i], batches[i + 3]])
                   for name, i in [('task', 0), ('language', 1), ('keep', 2)]}
        phi.zero_grad(set_to_none=True)
        observed, _, diag = rollout(model, values, zero_state(values), phi, streams, stats, 2, .03,
                                    .001, effective_tokens=5, retain_weight=.25,
                                    horizon='EXACT_H2_tail', queries=queries, reference=reference, chunk=17)
        for key in work:
            torch.testing.assert_close(observed[key], work[key], atol=1e-11, rtol=1e-9)
        for parameter, expected in zip(phi.parameters(), direct_grad):
            torch.testing.assert_close(parameter.grad, expected, atol=1e-9, rtol=1e-6)
        assert abs(diag['objective'] - float(direct.detach())) < 1e-8
        assert all(s.cursor == 2 for s in streams.values())
        tests[architecture + '_full_rollout_three_support_routes_matches_direct_query_graph'] = 'PASS'
    output = {'status': 'PASS', 'scope': 'CPU tiny Qwen rollout plumbing, not MUSA or method effect', 'tests': tests}
    Path(args.out).write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
