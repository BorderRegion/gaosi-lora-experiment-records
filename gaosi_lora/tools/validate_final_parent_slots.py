"""CPU proof of matched R exposure in all/retain-only final parent fitting."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.data import FactStream, LanguageStream, route_batches, rows, token_count
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save, seed_all
from gaosi.transition import Phi
import gaosi.train as fitting


def independent(config):
    seed_all(config['seed'])
    model = AdapterModel(config['model'], 2, 'all7', 'O1', 'cpu')
    data = rows(config['data'], 'support')
    targets = set(config['target_ids'])
    streams = {'R': FactStream([r for r in data if r['fact_id'] not in targets], model.tokenizer, 17, 64),
               'language': LanguageStream('data/language/inner.jsonl', model.tokenizer, 64, 17)}
    routes = [('R', .5)]
    if config['parent_mode'] == 'all':
        streams['F'] = FactStream([r for r in data if r['fact_id'] in targets], model.tokenizer, 17, 64)
        routes += [('F', .5)]
    routes += [('language', .25)]
    params = list(model.adapter(True).values())
    opt = torch.optim.Adam(params, lr=.001, foreach=False)
    for _ in range(2):
        opt.zero_grad(set_to_none=True)
        for route, mass in routes:
            batches, count = route_batches(streams[route], 'cpu', 8)
            for batch in batches:
                (mass * token_count(batch) / count * model(batch)).backward()
        torch.nn.utils.clip_grad_norm_(params, 1.)
        opt.step()
    return model.adapter_cpu()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    p.add_argument('--out', default='checks/final_parent_slots_cpu.json')
    args = p.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(942)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_final_slots_') as directory:
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
        ids = sorted({r['fact_id'] for r in source})[:2]
        datafile = root / 'fixture_final_slots.jsonl'
        datafile.write_text(''.join(json.dumps(r) + '\n' for r in source if r['fact_id'] in ids))
        retained = root / 'fixture_retain_query.jsonl'
        retained.write_text(''.join(json.dumps(r) + '\n' for r in source if r['fact_id'] == ids[0]))
        phi = root / 'phi.pt'
        atomic_save({'phi': Phi('L').state_dict()}, phi)
        frozen = root / 'frozen.json'
        frozen.write_text(json.dumps({'status': 'FROZEN', 'stopping_rule_frozen': True,
                                     'phi': str(phi), 'phi_sha256': hashlib.sha256(phi.read_bytes()).hexdigest(),
                                     'scope': 'CPU RANDOM FIXTURE ONLY'}))
        template = dict(out='', model=str(modelpath), base_revision='CPU_RANDOM_FIXTURE', rank=2,
                        targets='all7', organization='O1', scale=1., device='cpu', data=str(datafile),
                        language_inner='data/language/inner.jsonl', language_dev='data/language/dev.jsonl',
                        seq=64, microbatch=1, effective_tokens=8, retain_weight=.25, lr=.001,
                        ba_ratio=1., seed=17, steps=2, eval_steps=[2], generate=True,
                        language_eval_tokens=16, save_every=1, parent_layout='selective',
                        target_ids=[ids[1]], phi_freeze_manifest=str(frozen))
        results = {}
        for mode in ['all', 'retain_only']:
            c = dict(template, out=str(root / mode), parent_mode=mode)
            if mode == 'retain_only':
                c['query_data'] = str(retained)
            expected = independent(c)
            fitting.train(c)
            saved = torch.load(root / mode / 'latest.pt', map_location='cpu', weights_only=False)
            for name, value in expected.items():
                torch.testing.assert_close(saved['adapter'][name], value, atol=2e-6, rtol=2e-6)
            results[mode] = saved
            tests[mode + '_fixed_slot_parent_matches_independent_Adam'] = 'PASS'
        assert results['all']['fact_stream'] == results['retain_only']['fact_stream']
        assert results['all']['language_stream'] == results['retain_only']['language_stream']
        assert results['retain_only']['target_stream'] is None
        tests['retain_only_omits_F_preserving_R_and_language_samples_and_mass'] = 'PASS'
        original_save = fitting.atomic_save

        def interrupt(value, path):
            original_save(value, path)
            if value.get('step') == 1:
                raise InterruptedError('Fixture interruption')

        c = dict(template, out=str(root / 'resume'), parent_mode='all')
        fitting.atomic_save = interrupt
        try:
            try:
                fitting.train(c)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected interruption')
        finally:
            fitting.atomic_save = original_save
        fitting.train(c, resume=True)
        resumed = torch.load(root / 'resume/latest.pt', map_location='cpu', weights_only=False)
        for name, value in results['all']['adapter'].items():
            torch.testing.assert_close(resumed['adapter'][name], value, atol=0, rtol=0)
        assert resumed['target_stream'] == results['all']['target_stream']
        assert resumed['fact_stream'] == results['all']['fact_stream']
        tests['fixed_slot_parent_resume_preserves_both_streams_bitwise'] = 'PASS'
        bad = dict(c, out=str(root / 'missing_freeze'))
        del bad['phi_freeze_manifest']
        try:
            fitting.train(bad)
        except ValueError as error:
            assert 'saved phi freeze' in str(error)
        else:
            raise AssertionError('Final knowledge must not be trained before phi freeze')
        tests['final_parent_before_phi_freeze_rejected'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU tiny random fixtures; actual final knowledge remains untrained', 'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
