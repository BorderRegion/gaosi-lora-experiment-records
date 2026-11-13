"""CPU checks of real O4 parent fitting and O2/O3/O4 meta entry points.

Random fixtures exercise state/derivative plumbing and never prove acquisition.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.data import FactStream, LanguageStream, route_batches, rows, token_count
from gaosi.merge import MergeMaster
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save, seed_all
import gaosi.meta as meta
import gaosi.train as fitting


def direct(config):
    seed_all(config['seed'])
    model = AdapterModel(config['model'], 2, 'all7', 'O4', 'cpu')
    MergeMaster(model, config['historical_base'])
    streams = {
        'task': FactStream(rows(config['data'], 'support'), model.tokenizer, config['seed'], 64),
        'keep': FactStream(rows(config['retain_data'], 'support'), model.tokenizer, config['seed'], 64),
        'language': LanguageStream(config['language_inner'], model.tokenizer, 64, config['seed']),
    }
    optimizer = torch.optim.Adam(list(model.adapter(True).values()), lr=config['lr'], foreach=False)
    for _ in range(config['steps']):
        optimizer.zero_grad(set_to_none=True)
        for route, mass in [('task', 1.), ('language', .5 * config['retain_weight']),
                            ('keep', .5 * config['retain_weight'])]:
            batches, tokens = route_batches(streams[route], 'cpu', config['effective_tokens'])
            for batch in batches:
                (mass * token_count(batch) / tokens * model(batch)).backward()
        torch.nn.utils.clip_grad_norm_(list(model.adapter(True).values()), 1.)
        optimizer.step()
    return model.adapter_cpu()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/organization_parents_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(481)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_org_parent_') as directory:
        root = Path(directory)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        modelpath = root / 'tiny_qwen'
        tiny = Qwen2ForCausalLM(Qwen2Config(
            vocab_size=len(tokenizer), hidden_size=8, intermediate_size=16, num_hidden_layers=1,
            num_attention_heads=1, num_key_value_heads=1, head_dim=8, attention_dropout=0.,
            eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(modelpath)
        tokenizer.save_pretrained(modelpath)
        del tiny
        source = rows('data/synthetic/dev_parent.jsonl')
        ids = sorted({row['fact_id'] for row in source})[:2]
        datafiles = []
        for label, binding in zip(['historical', 'current'], ids):
            file = root / (label + '.jsonl')
            file.write_text(''.join(json.dumps(row) + '\n' for row in source if row['fact_id'] == binding))
            datafiles.append(str(file))
        historical, current = datafiles
        model = AdapterModel(str(modelpath), 2, 'all7', 'O4', 'cpu')
        with torch.no_grad():
            for name, value in model.adapter().items():
                if name.endswith('.B'):
                    value.normal_(0, .01)
        original = model.adapter_cpu()
        master = MergeMaster(model)
        master.merge(model)
        masterfile = root / 'historical.pt'
        master.save(masterfile, 'CPU_RANDOM_FIXTURE')
        Path(str(masterfile) + '.json').write_text(json.dumps({
            'status': 'PASS', 'historical_bindings': historical, 'scope': 'CPU RANDOM FIXTURE ONLY'}))
        master_digest = master.digest()
        del master, model
        config = dict(out=str(root / 'fit'), model=str(modelpath), base_revision='CPU_RANDOM_FIXTURE',
                      rank=2, targets='all7', organization='O4', scale=1., device='cpu',
                      data=current, retain_data=historical, historical_base=str(masterfile),
                      language_inner='data/language/inner.jsonl', language_dev='data/language/dev.jsonl',
                      seq=64, microbatch=1, effective_tokens=8, retain_weight=.25,
                      lr=.001, ba_ratio=1., seed=17, steps=2, eval_steps=[1, 2],
                      generate=True, language_eval_tokens=16, save_every=1)
        expected = direct(config)
        fitting.train(config)
        full = torch.load(root / 'fit/latest.pt', map_location='cpu', weights_only=False)
        for name, value in expected.items():
            torch.testing.assert_close(full['adapter'][name], value, atol=2e-6, rtol=2e-6)
        assert full['keep_stream']['covered']
        assert full['initial_keep'] is not None
        tests['O4_parent_half_language_half_history_matches_independent_stock_Adam'] = 'PASS'
        original_save = fitting.atomic_save

        def interrupt(value, path):
            original_save(value, path)
            if value.get('step') == 1:
                raise InterruptedError('Simulated parent interruption after atomic checkpoint')

        partial = dict(config, out=str(root / 'resume'))
        fitting.atomic_save = interrupt
        try:
            try:
                fitting.train(partial)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected fixture interruption')
        finally:
            fitting.atomic_save = original_save
        fitting.train(partial, resume=True)
        resumed = torch.load(root / 'resume/latest.pt', map_location='cpu', weights_only=False)
        for name, value in full['adapter'].items():
            torch.testing.assert_close(resumed['adapter'][name], value, atol=0, rtol=0)
        assert resumed['keep_stream'] == full['keep_stream']
        assert resumed['initial_keep'] == full['initial_keep']
        tests['O4_parent_resume_preserves_retention_stream_reference_and_updates_bitwise'] = 'PASS'
        ordinary = root / 'ordinary.pt'
        atomic_save({'adapter': original, 'acquisition_gate_passed': True,
                     'scope': 'RANDOM CPU FIXTURE ONLY'}, ordinary)
        # Acquisition is explicitly a random test fixture. No production gate
        # is changed and these metadata never leave the temporary directory.
        parentfile = root / 'current_fixture.pt'
        atomic_save(dict(full, acquisition_gate_passed=True, scope='RANDOM CPU FIXTURE ONLY'), parentfile)
        manifest = root / 'worlds.json'
        manifest.write_text(json.dumps({'worlds': [{'split': 'meta', 'file': current, 'kind': 'fact'}]}))
        template = dict(out='', model=str(modelpath), base_revision='CPU_RANDOM_FIXTURE',
                        rank=2, targets='all7', scale=1., organization='O1', device='cpu',
                        architecture='L', seed=17, parents=[str(ordinary)], parent_data=[current],
                        episode_K=[2, 2], known_warm_episodes=0, q_cycle=[0., .03],
                        meta_lr=.0003, inner_lr=.001, seq=64, effective_tokens=8,
                        retain_weight=.25, retain_fact_support=True, penalty=1., chunk=1024,
                        horizon='EXACT_H2_tail', world_manifest=str(manifest))
        for organization in ['O2', 'O3', 'O4']:
            c = dict(template, out=str(root / organization), organization=organization)
            if organization == 'O4':
                c.update(parents=[str(parentfile)], historical_bases=[str(masterfile)],
                         historical_data=[historical])
            meta.train(c)
            result = json.loads((root / organization / 'result.json').read_text())
            assert result['status'] == 'PASS' and result['inner_updates'] == 4
            tests[organization + '_real_known_and_new_meta_episodes_exact_H2'] = 'PASS'
        bad = dict(c, out=str(root / 'bad'), historical_bases=[str(root / 'unrelated.pt')])
        try:
            meta.train(bad)
        except ValueError as error:
            assert 'declared historical base/data' in str(error)
        else:
            raise AssertionError('Unpaired historical/current O4 parent must fail')
        tests['O4_unpaired_historical_base_is_rejected'] = 'PASS'
        restored = AdapterModel(str(modelpath), 2, 'all7', 'O4', 'cpu')
        assert MergeMaster(restored, masterfile).digest() == master_digest
        tests['historical_FP32_master_not_mutated_by_parent_or_meta_training'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU random tiny Qwen only; no MUSA/acquisition/effect evidence',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
