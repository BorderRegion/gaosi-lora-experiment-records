"""Exercise actual meta training across warm starts and interrupted episodes.

Random CPU fixtures verify state plumbing only, never knowledge acquisition.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save
import gaosi.meta as meta


def same_tree(actual, expected):
    if isinstance(expected, torch.Tensor):
        torch.testing.assert_close(actual, expected, atol=0, rtol=0)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            same_tree(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for a, b in zip(actual, expected):
            same_tree(a, b)
    else:
        assert actual == expected


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    p.add_argument('--out', default='checks/meta_resume_cpu.json')
    args = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(821)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_meta_resume_') as directory:
        root = Path(directory)
        modelpath = root / 'tiny_qwen'
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        tiny = Qwen2ForCausalLM(Qwen2Config(
            vocab_size=len(tokenizer), hidden_size=8, intermediate_size=16, num_hidden_layers=1,
            num_attention_heads=1, num_key_value_heads=1, head_dim=8, attention_dropout=0.,
            eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(modelpath)
        tokenizer.save_pretrained(modelpath)
        del tiny
        source = rows('data/synthetic/dev_parent.jsonl')
        ids = sorted({r['fact_id'] for r in source})[:2]
        datafile = root / 'fixture_parent.jsonl'
        datafile.write_text(''.join(json.dumps(r) + '\n' for r in source if r['fact_id'] in ids))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, parameter in model.adapter().items():
                if name.endswith('.B'):
                    parameter.normal_(0, .01)
        parent = root / 'parent.pt'
        atomic_save({'adapter': model.adapter_cpu(), 'acquisition_gate_passed': True,
                     'scope': 'RANDOM CPU TEST FIXTURE ONLY'}, parent)
        del model
        manifest = root / 'worlds.json'
        manifest.write_text(json.dumps({'worlds': []}))
        config = {'out': str(root / 'full'), 'model': str(modelpath), 'rank': 2,
                  'targets': 'all7', 'organization': 'O1', 'device': 'cpu', 'scale': 1.,
                  'architecture': 'L', 'seed': 17, 'parents': [str(parent)],
                  'parent_data': [str(datafile)], 'episode_K': [2, 2, 2],
                  'known_warm_episodes': 3, 'q_cycle': [0., .01, .03],
                  'meta_lr': .0003, 'inner_lr': .001, 'seq': 64, 'effective_tokens': 8,
                  'retain_weight': .25, 'retain_fact_support': True, 'penalty': 1.,
                  'chunk': 1024, 'horizon': 'H1', 'world_manifest': str(manifest),
                  'base_revision': 'random_cpu_test_fixture'}
        original_save = meta.atomic_save
        warm_path = root / 'warm.pt'

        def capture_warm(value, path):
            original_save(value, path)
            if value.get('next_episode') == 1:
                original_save(value, warm_path)

        meta.atomic_save = capture_warm
        try:
            meta.train(config)
        finally:
            meta.atomic_save = original_save
        full = torch.load(root / 'full/latest.pt', map_location='cpu', weights_only=False)
        warm = torch.load(warm_path, map_location='cpu', weights_only=False)
        assert warm['next_episode'] == 1 and warm['inner_updates'] == 2
        partial = dict(config, out=str(root / 'warm_branch'), start_episode=1,
                       initial_phi=str(warm_path), load_initial_meta_optimizer=True)
        meta.train(partial)
        branch = torch.load(root / 'warm_branch/latest.pt', map_location='cpu', weights_only=False)
        for key in ['phi', 'meta_optimizer', 'next_episode', 'inner_updates']:
            same_tree(branch[key], full[key])
        tests['warm_branch_restores_phi_optimizer_episode_and_data_schedule_bitwise'] = 'PASS'
        bad = dict(partial, out=str(root / 'wrong_episode'), start_episode=2)
        try:
            meta.train(bad)
        except ValueError as error:
            assert 'matching warm episode' in str(error)
        else:
            raise AssertionError('Mismatched warm boundary must fail')
        tests['mismatched_warm_episode_rejected'] = 'PASS'
        interrupted = dict(partial, out=str(root / 'interrupted'))

        def interrupt(value, path):
            original_save(value, path)
            if value.get('next_episode') == 2:
                raise InterruptedError('CPU fixture interruption after complete episode')

        meta.atomic_save = interrupt
        try:
            try:
                meta.train(interrupted)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected fixture interruption')
        finally:
            meta.atomic_save = original_save
        meta.train(interrupted, resume=True)
        resumed = torch.load(root / 'interrupted/latest.pt', map_location='cpu', weights_only=False)
        for key in ['phi', 'meta_optimizer', 'next_episode', 'inner_updates']:
            same_tree(resumed[key], full[key])
        tests['warm_branch_interrupted_resume_matches_uninterrupted_bitwise'] = 'PASS'
        alternate = dict(partial, out=str(root / 'alternate_H2'), horizon='EXACT_H2_tail')
        meta.train(alternate)
        switched = torch.load(root / 'alternate_H2/latest.pt', map_location='cpu', weights_only=False)
        assert switched['next_episode'] == 3 and switched['inner_updates'] == 6
        assert all(int(state['step']) == 3 for state in switched['meta_optimizer']['state'].values())
        tests['alternate_horizon_retains_shared_warm_meta_optimizer_steps'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU random tiny Qwen integration; no MUSA or effect claim',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
