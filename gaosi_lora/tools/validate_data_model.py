"""Check masking/packing and organization equivalence before effect studies."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import Qwen2Config, Qwen2ForCausalLM
from gaosi.model import AdapterModel, FactorSpec, NativeLoRA
from gaosi.data import FactStream, rows, collate
from types import SimpleNamespace
from gaosi.noise import split_parent_single_to_dual, canonical_noise


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='checks/data_model_contract.json')
    a = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(17)
    qwen = Qwen2ForCausalLM(Qwen2Config(vocab_size=41, hidden_size=16, intermediate_size=32,
                                      num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
                                      head_dim=8, attention_dropout=0.)).eval()
    wrapper = AdapterModel.__new__(AdapterModel)
    torch.nn.Module.__init__(wrapper)
    wrapper.llm = qwen
    batch = {'input_ids': torch.randint(0, 41, (2, 16)),
             'attention_mask': torch.ones(2, 16, dtype=torch.long)}
    batch['labels'] = batch['input_ids'].clone()
    batch['labels'][0, :9] = -100
    batch['labels'][1, :6] = -100
    expected = qwen(**batch, use_cache=False).loss
    actual = wrapper(batch)
    torch.testing.assert_close(actual, expected, atol=1e-6, rtol=1e-6)
    gradient_a = torch.autograd.grad(actual, qwen.lm_head.weight)[0]
    gradient_b = torch.autograd.grad(qwen(**batch, use_cache=False).loss, qwen.lm_head.weight)[0]
    torch.testing.assert_close(gradient_a, gradient_b, atol=1e-6, rtol=1e-6)
    tests = {'masked_CE_and_gradient_match_stock_Qwen': 'PASS'}
    seqs = [torch.randint(0, 41, (9,)).tolist(), torch.randint(0, 41, (7,)).tolist()]
    labels = [[-100] * 4 + seqs[0][4:], [-100] * 3 + seqs[1][3:]]
    single_losses = [qwen(input_ids=torch.tensor([x]), labels=torch.tensor([y]), use_cache=False).loss
                     for x, y in zip(seqs, labels)]
    counts = [sum(i != -100 for i in y[1:]) for y in labels]
    reference = sum(l * n for l, n in zip(single_losses, counts)) / sum(counts)
    packed = collate([(seqs[0] + seqs[1], labels[0] + labels[1], [0] * 9 + [1] * 7)],
                     SimpleNamespace(pad_token_id=0), 'cpu')
    actual = wrapper(packed)
    torch.testing.assert_close(actual, reference, atol=1e-6, rtol=1e-6)
    ga = torch.autograd.grad(actual, qwen.lm_head.weight)[0]
    gb = torch.autograd.grad(reference, qwen.lm_head.weight)[0]
    torch.testing.assert_close(ga, gb, atol=1e-6, rtol=1e-6)
    tests['independent_packed_loss_and_gradient_equal_isolated_samples'] = 'PASS'
    wrapper._specs = {}
    for name, module in list(qwen.named_modules()):
        if name.split('.')[-1] == 'q_proj':
            parent, _, child = name.rpartition('.')
            layer = NativeLoRA(module, 2)
            setattr(qwen.get_submodule(parent), child, layer)
            for factor in ['A', 'B']:
                key = 'llm.' + name + '.branches.single.' + factor
                wrapper.specs[key] = FactorSpec(key, int(name.split('.')[2]), child, factor,
                                                'single', 2, 1., key[:-1] + ('B' if factor == 'A' else 'A'))
    wrapper.organization = 'O1'
    before_adapter = wrapper.adapter_cpu()
    with torch.no_grad():
        before_scratch = wrapper(batch)
    added = wrapper.add_scratch(2)
    assert set(wrapper.adapter(True)) == set(added)
    with torch.no_grad():
        torch.testing.assert_close(before_scratch, wrapper(batch))
    optimizer = torch.optim.Adam(list(wrapper.adapter(True).values()), lr=.01)
    optimizer.zero_grad()
    wrapper(batch).backward()
    optimizer.step()
    wrapper.remove_scratch()
    assert all(torch.equal(v, before_adapter[k]) for k, v in wrapper.adapter_cpu().items())
    with torch.no_grad():
        torch.testing.assert_close(before_scratch, wrapper(batch))
    tests['common_scratch_add_train_remove_preserves_native_adapter'] = 'PASS'
    state = {'llm.model.layers.0.self_attn.q_proj.branches.single.A': torch.randn(4, 12),
             'llm.model.layers.0.self_attn.q_proj.branches.single.B': torch.randn(8, 4)}
    dual = split_parent_single_to_dual(state)
    prefix = 'llm.model.layers.0.self_attn.q_proj.branches.'
    before = state[prefix + 'single.B'] @ state[prefix + 'single.A']
    after = sum(dual[prefix + role + '.B'] @ dual[prefix + role + '.A'] for role in ['stable', 'plastic'])
    torch.testing.assert_close(before, after, atol=3e-6, rtol=3e-6)
    specs = {name: FactorSpec(name, 0, 'q_proj', name[-1], name.split('.')[-2], 2, 1.,
                              name[:-1] + ('B' if name[-1] == 'A' else 'A')) for name in dual}
    output, diagnostics = canonical_noise(dual, specs, 0., 991)
    clean = sum(output[prefix + role + '.B'] @ output[prefix + role + '.A'] for role in ['stable', 'plastic'])
    torch.testing.assert_close(before, clean, atol=3e-6, rtol=3e-6)
    # Same initial bytes across methods given the common data/noise seed.
    first, _ = canonical_noise(dual, specs, .03, 991)
    second, _ = canonical_noise(dual, specs, .03, 991)
    assert all(torch.equal(first[k], second[k]) for k in first)
    tests.update(O3_canonical_split_equivalence='PASS', O3_composed_q0_equivalence='PASS',
                 paired_noise_byte_identity='PASS')
    manifest = json.loads(Path('data/synthetic/manifest.json').read_text())
    for parent in manifest['parents'].values():
        assert len(parent['target_ids']) == 64 and len(parent['retain_ids']) == 448
        assert not set(parent['target_ids']).intersection(parent['retain_ids'])
    documents = {}
    for pool in ['inner', 'meta_query', 'dev', 'final']:
        ids = {x['document_id'] for x in rows(f'data/language/{pool}.jsonl')}
        assert all(not ids.intersection(other) for other in documents.values())
        documents[pool] = ids
    tests.update(target_retain_partition='PASS', language_document_disjointness='PASS')
    result = {'status': 'PASS', 'scope': 'CPU math/data checks only, not effect evidence',
              'tests': tests, 'article_counts': {k: len(v) for k, v in documents.items()}}
    Path(a.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
