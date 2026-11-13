"""Independent corruption/update/resume checks on random CPU/dev fixtures."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from torch import nn
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM

from gaosi.base_boundary import perturb_base, run_boundary, check_config
from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save
from gaosi.scale import sha256
import gaosi.recover as recovery
import validate_recovery as reference
from validate_meta_resume import same_tree
from device_guard import uses_musa


def rejected(call, text):
    try:
        call()
    except ValueError as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError('Invalid boundary entry accepted')


class Toy(nn.Module):
    def __init__(self, dtype):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(640, 100, dtype=dtype), requires_grad=False)
        self.tied_weight = self.weight
        self.norm = nn.Parameter(torch.ones(100, dtype=dtype), requires_grad=False)
        self.bias = nn.Parameter(torch.zeros(100, dtype=dtype), requires_grad=False)
        self.adapter = nn.Parameter(torch.randn(2, 100))
        self.specs = {'adapter': None}
        self.register_buffer('buffer', torch.ones(7, dtype=dtype))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/base_boundary_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(2701)
    tests = {}
    for dtype in [torch.float32, torch.bfloat16]:
        toy = Toy(dtype)
        before = {k: p.detach().clone() for k, p in toy.named_parameters()}
        rng = torch.get_rng_state().clone()
        summary = perturb_base(toy, 1e-4, 2701)
        total_damage = 0.
        for name, value in toy.named_parameters():
            if name == 'adapter':
                assert torch.equal(value, before[name])
                continue
            clean = before[name].float()
            rms = math.sqrt(float(clean.double().square().sum()) / clean.numel())
            raw_seed = hashlib.sha256(('2701:' + name).encode()).digest()
            generator = torch.Generator().manual_seed(int.from_bytes(raw_seed[:8], 'little') % (2 ** 63 - 1))
            target = math.sqrt(1 - 1e-4) * clean + .01 * rms * torch.randn(clean.shape, generator=generator)
            expected = target.to(dtype)
            assert torch.equal(value, expected)
            total_damage += float((value.double() - before[name].double()).square().sum())
        assert math.isclose(total_damage, summary['actual_damage_squared'], rel_tol=1e-14)
        assert torch.equal(torch.get_rng_state(), rng)
        assert len(summary['base_parameters']) == 3 and summary['parameter_elements'] == 64200
        assert toy.weight is toy.tied_weight and torch.equal(toy.buffer, torch.ones_like(toy.buffer))
        assert not toy.bias.any()
        tests[f'{dtype}_matches_independent_full_tensor_formula_and_actual_rounded_damage'] = 'PASS'
    tests['tied_base_weights_perturbed_once_adapter_buffers_and_global_RNG_untouched'] = 'PASS'
    zero = {k: p.detach().clone() for k, p in toy.named_parameters()}
    assert perturb_base(toy, 0., 2701, 113)['actual_damage_squared'] == 0.
    assert all(torch.equal(v, zero[k]) for k, v in toy.named_parameters())
    tests['zero_dose_is_bitwise_identity_even_with_small_chunks'] = 'PASS'
    first, second = Toy(torch.bfloat16), Toy(torch.bfloat16)
    second.load_state_dict(first.state_dict())
    perturb_base(first, 1e-4, 2701, 113)
    perturb_base(second, 1e-4, 2701, 113)
    assert all(torch.equal(v, dict(second.named_parameters())[k]) for k, v in first.named_parameters())
    tests['fixed_seed_names_and_chunking_reconstruct_base_bytes_exactly'] = 'PASS'
    with tempfile.TemporaryDirectory(prefix='gaosi_boundary_cpu_') as directory:
        root = Path(directory)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        modelpath = root / 'tiny'
        tiny = Qwen2ForCausalLM(Qwen2Config(vocab_size=len(tokenizer), hidden_size=8,
            intermediate_size=16, num_hidden_layers=1, num_attention_heads=1, num_key_value_heads=1,
            head_dim=8, attention_dropout=0., eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(modelpath);tokenizer.save_pretrained(modelpath);del tiny
        original_model_hashes = {str(p): sha256(p) for p in modelpath.iterdir() if p.is_file()}
        source = rows('data/synthetic/dev_parent.jsonl')
        ids = sorted({r['fact_id'] for r in source})[:2]
        datafile = root / 'dev_fixture.jsonl'
        datafile.write_text(''.join(json.dumps(r) + '\n' for r in source if r['fact_id'] in ids))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, p in model.adapter().items():
                if name.endswith('.B'):
                    p.normal_(0, .01)
        adapter = model.adapter_cpu()
        parent_config = {'model': str(modelpath), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
                         'scale': 1., 'seq': 128, 'data': str(datafile), 'base_revision': 'RANDOM_CPU_FIXTURE'}
        parent = root / 'parent/latest.pt'
        atomic_save({'adapter': adapter, 'config': parent_config, 'base_digest': model.base_digest()}, parent)
        (parent.parent / 'result.json').write_text(json.dumps(
            {'acquisition_gate_passed': True, 'scope': 'RANDOM CPU FIXTURE ONLY, NO ACQUISITION CLAIM'}))
        del model
        calibration = root / 'calibration.json'
        calibration.write_text(json.dumps({'status': 'CALIBRATION_FROZEN', 'formal_data_used': False,
                                          'ordinary_recovery_validated': True, 'scope': 'CPU fixture'}))
        planfile = root / 'plan.json'
        template = {'freeze_manifest': str(planfile), 'phase': 'development', 'data_seed': 17,
                    'noise_seed': 1729, 'parent': str(parent), 'device': 'cpu', 'q_apply': 0.,
                    'noise_scheme': 'subspace', 'architecture': 'L', 'method': 'standard_adam',
                    'data': str(datafile), 'target_ids': [], 'task_layout': 'all',
                    'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
                    'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 16,
                    'inner_lr': .001, 'effective_tokens': 16, 'retain_weight': .25,
                    'K': 2, 'eval_steps': [1, 2], 'base_noise_seed': 2701, 'base_chunk_elements': 113}
        configs = [dict(template, out=str(root / label), base_q=q)
                   for label, q in [('zero', 0.), ('small', 1e-4), ('resumed', 1e-4)]]
        configs[1]['initial_adapter'] = str(root / 'zero/initial.pt')
        configs[2]['initial_adapter'] = configs[1]['initial_adapter']
        plan = {'status': 'DEVELOPMENT_PLAN', 'base_boundary_diagnostic': True, 'boundary_configs': configs,
                'calibration_manifest': str(calibration), 'calibration_sha256': sha256(calibration),
                'data_sha256': {p: sha256(p) for p in
                               [str(datafile), 'data/language/inner.jsonl', 'data/language/dev.jsonl']},
                'parent_sha256': sha256(parent), 'allowed_data': [str(datafile)],
                'allowed_parents': [str(parent)], 'lr_candidates': [.001], 'K_candidates': [2],
                'q_candidates': [0.], 'noise_schemes': ['subspace'], 'primary_K': 1}
        planfile.write_text(json.dumps(plan))
        outputs = []
        for config in configs[:2]:
            original_constructor = reference.AdapterModel

            def corrupted_reference(*a, **kw):
                m = original_constructor(*a, **kw)
                perturb_base(m, config['base_q'], config['base_noise_seed'], config['base_chunk_elements'])
                return m

            with patch.object(reference, 'AdapterModel', corrupted_reference):
                expected = reference.direct_adam(config, parent_config, adapter)
            run_boundary(config)
            actual = torch.load(Path(config['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            for name, value in expected.items():
                torch.testing.assert_close(actual['parameters'][name], value, atol=2e-6, rtol=2e-6)
            outputs.append(json.loads((Path(config['out']) / 'boundary_result.json').read_text()))
        tests['both_clean_and_noisy_base_repair_match_independent_stock_Adam'] = 'PASS'
        assert outputs[0]['pristine_reference'] == outputs[1]['pristine_reference']
        assert outputs[0]['pristine_base_digest'] == outputs[0]['perturbed_base_digest']
        assert outputs[1]['pristine_base_digest'] != outputs[1]['perturbed_base_digest']
        assert outputs[1]['repair']['base_unchanged']
        assert outputs[1]['repair']['base_digest'] == outputs[1]['perturbed_base_digest']
        for output in outputs:
            for values in output['loss_changes_vs_pristine_base'].values():
                assert math.isclose(values['residual'], values['injected'] - values['recovered'], abs_tol=1e-14)
        tests['common_pristine_reference_and_frozen_corrupted_base_have_unambiguous_loss_accounting'] = 'PASS'
        original_save = recovery.atomic_save

        def interrupt(value, path):
            original_save(value, path)
            if value.get('step') == 1:
                raise InterruptedError('CPU boundary fixture save before evaluation')

        with patch.object(recovery, 'atomic_save', interrupt):
            try:
                run_boundary(configs[2])
            except InterruptedError:
                pass
            else:
                raise AssertionError('Expected interruption')
        run_boundary(configs[2], resume=True)
        expected = torch.load(root / 'small/latest.pt', map_location='cpu', weights_only=False)
        resumed = torch.load(root / 'resumed/latest.pt', map_location='cpu', weights_only=False)
        for key in ['parameters', 'standard_optimizer', 'streams', 'step', 'base_digest']:
            same_tree(resumed[key], expected[key])
        events = [json.loads(s) for s in (root / 'resumed/events.jsonl').read_text().splitlines()]
        order = [(e['event'], e.get('step')) for e in events if e['event'] in ['recover', 'evaluation']]
        assert order.index(('evaluation', 1)) < order.index(('recover', 2))
        tests['resume_reconstructs_noisy_base_and_restores_optimizer_streams_pending_evaluation_bitwise'] = 'PASS'
        rejected(lambda: check_config(dict(configs[0], base_q=.5)), 'settings')
        text = datafile.read_text();datafile.write_text(text + '\n')
        rejected(lambda: check_config(configs[0]), 'data bytes')
        datafile.write_text(text)
        original = calibration.read_text();calibration.write_text(original + '\n')
        rejected(lambda: check_config(configs[0]), 'calibration bytes')
        calibration.write_text(original)
        tests['undeclared_doses_changed_data_and_changed_calibration_are_rejected'] = 'PASS'
        assert original_model_hashes == {str(p): sha256(p) for p in modelpath.iterdir() if p.is_file()}
        tests['pretrained_model_files_are_byte_identical_after_corruption_repair_and_resume'] = 'PASS'
        file = root / 'device.json';file.write_text(json.dumps({'device': 'musa'}))
        assert uses_musa(['/usr/bin/python', '-m', 'gaosi.base_boundary', '--config', str(file)])
        file.write_text(json.dumps({'device': 'cpu'}))
        assert not uses_musa(['/usr/bin/python', '-m', 'gaosi.base_boundary', '--config', str(file)])
        tests['full_base_diagnostic_reuses_single_MUSA_leaf_lock'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'Random CPU matrices/tiny model and dev fixtures only; no real3B base corruption or repair result',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2));print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
