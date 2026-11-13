"""Tiny CPU checks for organization stages, stock Adam nights, and O4 resume."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
import gaosi.continual as continual
from gaosi.data import FactStream, LanguageStream, route_batches, rows, token_count
from gaosi.model import AdapterModel
from gaosi.noise import canonical_noise, split_parent_single_to_dual
from gaosi.runtime import atomic_save
from gaosi.transition import Phi


def stock_night(model, data, q, seed):
    model.set_stage('night')
    noisy, _ = canonical_noise(model.adapter_cpu(), model.specs, q, seed)
    model.load_adapter(noisy)
    params = list(model.adapter(True).values())
    optimizer = torch.optim.Adam(params, lr=.001, foreach=False)
    task = FactStream(data, model.tokenizer, seed, 64)
    language = LanguageStream('data/language/inner.jsonl', model.tokenizer, 64, 17)
    for _ in range(2):
        optimizer.zero_grad(set_to_none=True)
        for stream, mass in [(task, 1.), (language, .25)]:
            batches, tokens = route_batches(stream, 'cpu', 8)
            for batch in batches:
                (mass * token_count(batch) / tokens * model(batch)).backward()
        torch.nn.utils.clip_grad_norm_(params, 1.)
        optimizer.step()
    return model.adapter_cpu()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/continual_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(780)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_continual_') as directory:
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
        ids = sorted({r['fact_id'] for r in source})[:65]
        files = []
        for label, selected in [('parent', ids[:1]), ('world_0', ids[1:33]), ('world_1', ids[33:65])]:
            p = root / (label + '.jsonl')
            p.write_text(''.join(json.dumps(r) + '\n' for r in source if r['fact_id'] in selected))
            files.append(str(p))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, value in model.adapter().items():
                if name.endswith('.B'):
                    value.normal_(0, .01)
        adapter = model.adapter_cpu()
        del model
        phi = Phi('L')
        data = rows(files[0], 'support')
        for organization in ['O1', 'O2', 'O3', 'O4']:
            model = AdapterModel(str(modelpath), 2, 'all7', organization, 'cpu')
            initial = split_parent_single_to_dual(adapter, 17) if organization == 'O3' else adapter
            initial, _ = canonical_noise(initial, model.specs, 0., 17)
            model.load_adapter(initial)
            language = LanguageStream('data/language/inner.jsonl', model.tokenizer, 64, 17)
            continual.day(model, data, language, 2, .001, 17, 64, 8, lambda **kw: None)
            after_day = model.adapter_cpu()
            frozen = [name for name in initial if
                      (organization == 'O2' and model.specs[name].factor == 'A') or
                      (organization == 'O3' and model.specs[name].role == 'stable')]
            for name in frozen:
                assert torch.equal(initial[name], after_day[name])
            assert any(not torch.equal(initial[name], after_day[name]) for name in initial)
            tests[organization + '_day_respects_its_trainable_set'] = 'PASS'
            reference = AdapterModel(str(modelpath), 2, 'all7', organization, 'cpu')
            reference.load_adapter(after_day)
            expected = stock_night(reference, data, .03, 91)
            language = LanguageStream('data/language/inner.jsonl', model.tokenizer, 64, 17)
            continual.night(model, data, language, phi, 2, .001, .03, 91, 64, 8,
                            lambda **kw: None, method='standard_adam')
            for name, value in model.adapter_cpu().items():
                torch.testing.assert_close(value, expected[name], atol=2e-6, rtol=2e-6)
            tests[organization + '_night_matches_independent_stock_Adam'] = 'PASS'
            if organization == 'O3':
                for role in ['stable', 'plastic']:
                    assert any(not torch.equal(after_day[name], model.adapter_cpu()[name])
                               for name in after_day if model.specs[name].role == role)
                tests['O3_both_branches_change_during_noisy_night'] = 'PASS'
            del model, reference
        parent_dir = root / 'fixture_parent'
        parent_dir.mkdir()
        parent = parent_dir / 'latest.pt'
        parent_config = dict(model=str(modelpath), rank=2, targets='all7', scale=1.,
                             organization='O1', base_revision='CPU_RANDOM_FIXTURE')
        atomic_save({'adapter': adapter, 'config': parent_config}, parent)
        (parent_dir / 'result.json').write_text(json.dumps({
            'acquisition_gate_passed': True, 'scope': 'RANDOM CPU FIXTURE ONLY'}))
        freeze = root / 'freeze.json'
        freeze.write_text(json.dumps({'status': 'FROZEN', 'scope': 'CPU FIXTURE ONLY'}))
        config = dict(out='', freeze_manifest=str(freeze), parent=str(parent),
                      parent_data=files[0], worlds=files[1:], rounds=2, seed=17, replay_seed=912,
                      architecture='L', night_method='standard_adam', q_apply=.03,
                      seq=64, effective_tokens=128, day_steps=2, night_steps=2,
                      day_lr=.001, night_lr=.001, device='cpu', language_eval='data/language/dev.jsonl',
                      language_eval_tokens=16, probe_worlds=[], probe_seed=37)
        results = {}
        for organization in ['O1', 'O2', 'O3', 'O4']:
            c = dict(config, out=str(root / organization), organization=organization)
            continual.train(c)
            saved = torch.load(root / organization / 'latest.pt', map_location='cpu', weights_only=False)
            events = [json.loads(s) for s in (root / organization / 'events.jsonl').read_text().splitlines()]
            replays = [e for e in events if e['event'] == 'night_complete']
            assert len(replays[0]['replay_ids']) == 33 and len(replays[1]['replay_ids']) == 65
            assert set(replays[0]['replay_ids']) < set(replays[1]['replay_ids'])
            assert saved['next_round'] == 2
            results[organization] = (saved, replays)
            tests[organization + '_two_round_runner_uses_only_current_and_past_bindings'] = 'PASS'
        for _, replays in results.values():
            assert [e['replay_ids'] for e in replays] == [e['replay_ids'] for e in results['O1'][1]]
        tests['all_organizations_share_identical_replay_reservoirs'] = 'PASS'
        original_save = continual.atomic_save

        def interrupt(value, path):
            original_save(value, path)
            if value.get('next_round') == 1:
                raise InterruptedError('Simulated interruption at saved round boundary')

        c = dict(config, out=str(root / 'O4_resume'), organization='O4')
        continual.atomic_save = interrupt
        try:
            try:
                continual.train(c)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected simulated interruption')
        finally:
            continual.atomic_save = original_save
        continual.train(c, resume=True)
        resumed = torch.load(root / 'O4_resume/latest.pt', map_location='cpu', weights_only=False)
        expected = results['O4'][0]
        for section in ['adapter', 'weights']:
            a = resumed['adapter'] if section == 'adapter' else resumed['base_master']['weights']
            b = expected['adapter'] if section == 'adapter' else expected['base_master']['weights']
            for name in a:
                torch.testing.assert_close(a[name], b[name], atol=0, rtol=0)
        assert resumed['base_master']['merges'] == 3
        assert resumed['base_master']['rank_sum'] == 6
        tests['O4_round_resume_preserves_FP32_master_and_reset_adapter_bitwise'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU random tiny model, two-round fixtures only; not MUSA or eight-round results',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
