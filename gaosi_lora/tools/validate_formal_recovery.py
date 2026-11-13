"""Tiny CPU formal-entry, paired-support and reference checks, without final data."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.references import evaluate_references
from gaosi.runtime import atomic_save
from gaosi.transition import Phi
import gaosi.recover as recovery
from validate_recovery import direct_adam


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/formal_recovery_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(1923)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_formal_') as directory:
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
        ids = sorted({row['fact_id'] for row in source})[:4]
        data = [row for row in source if row['fact_id'] in ids]
        datafile = root / 'dev_fixture.jsonl'
        datafile.write_text(''.join(json.dumps(row) + '\n' for row in data))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, value in model.adapter().items():
                if name.endswith('.B'):
                    value.normal_(0, .01)
        adapter = model.adapter_cpu()
        digest = model.base_digest()
        del model
        parents = {}
        c = {'model': str(modelpath), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
             'scale': 1., 'seq': 128, 'data': str(datafile), 'base_revision': 'CPU_RANDOM_FIXTURE',
             'target_ids': [ids[0]], 'parent_layout': 'selective'}
        for mode in ['all', 'retain_only']:
            folder = root / ('parent_' + mode)
            folder.mkdir()
            state = {name: value.clone() for name, value in adapter.items()}
            if mode == 'retain_only':
                for name, value in state.items():
                    if name.endswith('.B'):
                        value.zero_()
            file = folder / 'latest.pt'
            atomic_save({'adapter': state, 'config': dict(c, parent_mode=mode),
                         'base_digest': digest, 'fact_stream': {'fixture_R_exposures': 1}}, file)
            (folder / 'result.json').write_text(json.dumps({
                'acquisition_gate_passed': True, 'scope': 'RANDOM CPU FIXTURE ONLY, NOT ACQUISITION EVIDENCE'}))
            parents[mode] = str(file)
        phi = root / 'phi.pt'
        atomic_save({'phi': Phi('L').state_dict()}, phi)
        freeze = root / 'freeze.json'
        frozen = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'phi': str(phi),
                  'phi_sha256': hashlib.sha256(phi.read_bytes()).hexdigest(),
                  'organization': 'O1', 'training_noise': 'mixed', 'formal_data_used': False,
                  'scope': 'CPU FIXTURE ONLY'}
        freeze.write_text(json.dumps(frozen))
        template = dict(out='', freeze_manifest=str(freeze), phase='formal', parent=parents['all'],
                        data_seed=17, noise_seed=1729, device='cpu', q_apply=.03,
                        noise_scheme='subspace', architecture='L', method='standard_adam',
                        data=str(datafile), target_ids=[ids[0]], task_layout='selective',
                        retain_subset_fraction=1., subset_seed=91, D='plus', inner_lr=.001,
                        language_eval='data/language/dev.jsonl', language_eval_tokens=16,
                        effective_tokens=16, retain_weight=.25, K=2, eval_steps=[1, 2])
        saved = {}
        for fraction in [1., .25]:
            for direction in ['plus', 'minus']:
                label = f'R{round(fraction*100)}_{direction}'
                config = dict(template, out=str(root / label), retain_subset_fraction=fraction, D=direction)
                if saved:
                    config['initial_adapter'] = str(root / 'R100_plus/initial.pt')
                # Independent ordinary Adam uses an explicitly constructed pool.
                retain_ids = ids[1:].copy()
                if fraction < 1:
                    random.Random(91).shuffle(retain_ids)
                    retain_ids = retain_ids[:round(len(retain_ids) * fraction)]
                selected = set(retain_ids + [ids[0]])
                subset = root / (label + '_expected_data.jsonl')
                subset.write_text(''.join(json.dumps(row) + '\n' for row in data if row['fact_id'] in selected))
                expected = direct_adam(dict(config, data=str(subset)), c, adapter)
                recovery.run_recovery(config)
                actual = torch.load(root / label / 'latest.pt', map_location='cpu', weights_only=False)
                for name, value in expected.items():
                    torch.testing.assert_close(actual['parameters'][name], value, atol=2e-6, rtol=2e-6)
                result = json.loads((root / label / 'result.json').read_text())
                assert result['pool'] == result['coverage'] == len(retain_ids)
                assert result['final']['F']['n'] == 1 and result['final']['R']['n'] == 3
                saved[label] = actual
                tests[label + '_formal_endpoint_matches_independent_stock_Adam'] = 'PASS'
        for fraction in [100, 25]:
            plus, minus = (saved[f'R{fraction}_{direction}'] for direction in ['plus', 'minus'])
            for route in ['R', 'language']:
                assert plus['streams'][route] == minus['streams'][route]
            assert 'F' not in minus['streams']
        tests['full_and_quarter_R_keep_identical_Dplus_Dminus_exposure_and_full_queries'] = 'PASS'
        fixed = {}
        for method in ['paired_adam', 'factor_lr']:
            label = 'equal_' + method
            config = dict(template, out=str(root / label), method=method, ba_ratio=1.,
                          initial_adapter=str(root / 'R100_plus/initial.pt'))
            recovery.run_recovery(config)
            fixed[method] = torch.load(root / label / 'latest.pt', map_location='cpu', weights_only=False)
        for name in fixed['paired_adam']['parameters']:
            assert torch.equal(fixed['paired_adam']['parameters'][name], fixed['factor_lr']['parameters'][name])
        tests['factor_ratio_one_equals_paired_Adam_bitwise_for_valid_reuse'] = 'PASS'
        config = dict(template, out=str(root / 'factor_four'), method='factor_lr', ba_ratio=4.,
                      initial_adapter=str(root / 'R100_plus/initial.pt'))
        expected = direct_adam(config, c, adapter, variance_floor=1e-16)
        recovery.run_recovery(config)
        actual = torch.load(root / 'factor_four/latest.pt', map_location='cpu', weights_only=False)
        for name, value in expected.items():
            torch.testing.assert_close(actual['parameters'][name], value, atol=2e-6, rtol=2e-6)
        tests['factor_ratio_four_matches_independent_per_group_Adam_with_contract_variance_floor'] = 'PASS'
        broken = dict(frozen, phi_sha256='incorrect')
        freeze.write_text(json.dumps(broken))
        try:
            recovery.run_recovery(dict(template, out=str(root / 'invalid_phi'), method='phi', phi=str(phi)))
        except ValueError as error:
            assert 'hash' in str(error)
        else:
            raise AssertionError('Invalid formal phi hash accepted')
        tests['formal_phi_hash_mismatch_rejected'] = 'PASS'
        refconfig = dict(out=str(root / 'references'), freeze_manifest=str(freeze), parents=parents,
                         data=str(datafile), target_ids=[ids[0]], seed=17, device='cpu',
                         language_eval='data/language/dev.jsonl', language_eval_tokens=16)
        try:
            evaluate_references(dict(refconfig, out=str(root / 'bad_ref'), data='missing_DO_NOT_READ.jsonl'))
        except ValueError as error:
            assert 'intact frozen' in str(error)
        else:
            raise AssertionError('References read formal data before validating phi freeze')
        tests['references_check_freeze_before_reading_any_task_data'] = 'PASS'
        freeze.write_text(json.dumps(frozen))
        evaluate_references(refconfig)
        result = json.loads((root / 'references/result.json').read_text())
        assert result['base_unchanged'] and result['base_equals_drop_adapter']
        assert result['results']['base'] == result['results']['drop_adapter'] == result['results']['retain_only']
        assert result['results']['parent_all']['F']['n'] == 1
        assert result['results']['parent_all']['R']['n'] == 3
        tests['disabled_adapter_matches_independent_zero_output_reference_with_F_R_language'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'CPU random tiny fixtures on dev rows only; no final facts or MUSA effects',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
