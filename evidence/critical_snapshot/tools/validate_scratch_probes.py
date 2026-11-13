"""Exercise common rank16 probes and verify exact main-state/RNG isolation."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.continual import scratch_probes
from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.runtime import rng_state, seed_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/scratch_probes_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    seed_all(391)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_scratch_probe_') as directory:
        root = Path(directory)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        modelpath = root / 'tiny'
        tiny = Qwen2ForCausalLM(Qwen2Config(
            vocab_size=len(tokenizer), hidden_size=8, intermediate_size=16, num_hidden_layers=1,
            num_attention_heads=1, num_key_value_heads=1, head_dim=8, attention_dropout=0.,
            eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(modelpath)
        tokenizer.save_pretrained(modelpath)
        del tiny
        source = rows('data/synthetic/dev_parent.jsonl')
        binding = sorted({row['fact_id'] for row in source})[0]
        task = root / 'task.jsonl'
        task.write_text(''.join(json.dumps(row) + '\n' for row in source if row['fact_id'] == binding))
        for organization in ['O1', 'O2', 'O3', 'O4']:
            model = AdapterModel(str(modelpath), 2, 'all7', organization, 'cpu')
            model.set_stage('day')
            before = {name: value.detach().cpu().clone() for name, value in model.named_parameters()}
            flags = {name: value.requires_grad for name, value in model.named_parameters()}
            specs = dict(model.specs)
            initial_rng = rng_state()
            result = scratch_probes(model, [str(task)], root / organization, 17, 64, 8, .001)
            assert result[0]['rank'] == 16
            assert [item['steps'] for item in result[0]['curves']] == [1, 4, 16]
            for name, value in model.named_parameters():
                torch.testing.assert_close(value.detach().cpu(), before[name], atol=0, rtol=0)
                assert value.requires_grad == flags[name]
            assert model.specs == specs
            after_rng = rng_state()
            assert initial_rng['python'] == after_rng['python']
            assert torch.equal(initial_rng['torch'], after_rng['torch'])
            for a, b in zip(initial_rng['numpy'], after_rng['numpy']):
                assert np.array_equal(a, b)
            tests[organization + '_rank16_steps_1_4_16_preserve_main_weights_flags_specs_and_rng'] = 'PASS'
            del model
    result = {'status': 'PASS', 'scope': 'CPU random tiny model only; not a MUSA capacity conclusion', 'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
