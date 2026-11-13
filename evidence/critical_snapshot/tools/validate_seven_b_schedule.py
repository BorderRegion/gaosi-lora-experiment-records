"""Cross-width CPU integration and data/decision checks; no 7B effect claim."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM

from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save
from gaosi.transition import Phi
from gaosi.scale import sha256, check_config
import gaosi.scale as scale
import gaosi.meta as meta
import gaosi.recover as recovery
import seven_b_queue as queue
from seven_b_queue import (entry, select_worlds, validate_isolation, dev_score,
                           needs_extension, backend_unavailable, POLICY)
from device_guard import uses_musa
from validate_recovery import direct_adam
from validate_meta_resume import same_tree


def reject(call, fragment=None):
    try:
        call()
    except (ValueError, RuntimeError) as error:
        if fragment:
            assert fragment in str(error), str(error)
    else:
        raise AssertionError('Invalid scale entry was accepted')


def dispatch(config, resume=False):
    file = Path(config['out']).with_suffix('.json')
    file.write_text(json.dumps(config))
    previous = sys.argv
    sys.argv = ['gaosi.scale', '--config', str(file)] + (['--resume'] if resume else [])
    try:
        scale.main()
    finally:
        sys.argv = previous


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/seven_b_schedule_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(7041)
    tests = {}
    # entry() writes in records; isolate all generated declarations as well.
    original_directory = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='gaosi_scale_cpu_') as directory:
        root = Path(directory)
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        source = rows('data/synthetic/dev_parent.jsonl')
        source_ids = sorted({r['fact_id'] for r in source})[:2]
        (root / 'data').mkdir()
        (root / 'data/language').symlink_to(original_directory / 'data/language', target_is_directory=True)
        os.chdir(root)

        def make_data(label):
            data = []
            for row in source:
                if row['fact_id'] in source_ids:
                    value = dict(row, world_id=label)
                    value['fact_id'] = label + ':' + row['fact_id']
                    value['sample_id'] = label + ':' + row['sample_id']
                    data.append(value)
            path = root / (label + '.jsonl')
            path.write_text(''.join(json.dumps(r) + '\n' for r in data))
            return str(path)

        meta_a, meta_b, dev, new = [make_data(s) for s in
                                    ['meta_a', 'meta_b', 'dev_known', 'meta_new']]
        world_manifest = root / 'worlds.json'
        world_manifest.write_text(json.dumps({'worlds': [
            {'world_id': 'meta_new', 'kind': 'fact', 'split': 'meta', 'file': new},
            {'world_id': 'final_DO_NOT_READ', 'kind': 'fact', 'split': 'final',
             'file': str(root / 'missing_final_DO_NOT_READ.jsonl')}]}))
        shapes = {}

        def make_model(width, data):
            path = root / f'tiny{width}'
            tiny = Qwen2ForCausalLM(Qwen2Config(
                vocab_size=len(tokenizer), hidden_size=width, intermediate_size=width * 2,
                num_hidden_layers=1, num_attention_heads=1, num_key_value_heads=1, head_dim=width,
                attention_dropout=0., eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.eos_token_id))
            tiny.save_pretrained(path)
            tokenizer.save_pretrained(path)
            del tiny
            model = AdapterModel(str(path), 2, 'all7', 'O1', 'cpu')
            with torch.no_grad():
                for name, parameter in model.adapter().items():
                    if name.endswith('.B'):
                        parameter.normal_(0, .01)
            adapter = model.adapter_cpu()
            shapes[width] = [tuple(t.shape) for t in adapter.values()]
            config = {'model': str(path), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
                      'scale': 1., 'seq': 128, 'data': data, 'base_revision': 'CPU_RANDOM_ONLY'}
            parent = root / f'parent{width}/latest.pt'
            atomic_save({'adapter': adapter, 'config': config, 'base_digest': model.base_digest(),
                         'acquisition_gate_passed': True}, parent)
            (parent.parent / 'result.json').write_text(json.dumps(
                {'acquisition_gate_passed': True, 'scope': 'RANDOM CPU TEST FIXTURE ONLY'}))
            del model
            return str(parent), config, adapter

        small, small_config, _ = make_model(8, meta_a)
        large, large_config, adapter = make_model(12, dev)
        assert shapes[8] != shapes[12]
        tests['fixtures_use_different_transformer_and_adapter_tensor_widths'] = 'PASS'
        upstream = []
        for architecture in ['L', 'X']:
            phi = root / f'zero_phi_{architecture}.pt'
            atomic_save({'phi': Phi(architecture).state_dict()}, phi)
            freeze = root / f'freeze_{architecture}.json'
            freeze.write_text(json.dumps({'status': 'FROZEN', 'stopping_rule_frozen': True,
                                         'formal_data_used': False, 'organization': 'O1',
                                         'architecture': architecture, 'phi': str(phi),
                                         'phi_sha256': sha256(phi), 'scope': 'CPU RANDOM FIXTURE ONLY'}))
            upstream.append(str(freeze))
        data_files = [dev, 'data/language/inner.jsonl', 'data/language/dev.jsonl']
        template = {'freeze_manifest': upstream[0], 'phase': 'formal', 'parent': large,
                    'data_seed': 17, 'noise_seed': 1729, 'device': 'cpu', 'q_apply': .03,
                    'noise_scheme': 'subspace', 'architecture': 'L', 'method': 'standard_adam',
                    'data': dev, 'target_ids': [], 'task_layout': 'all', 'retain_data': dev,
                    'retain_subset_fraction': 1., 'subset_seed': 91, 'D': 'plus',
                    'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 16,
                    'inner_lr': .001, 'effective_tokens': 16, 'retain_weight': .25,
                    'K': 2, 'eval_steps': [1, 2]}

        def fixture_entry(label, config, action='recover', sources=None, freezes=None):
            # Use the same production declaration function, with temp output paths.
            c = entry('scale_cpu_fixture', config, action, freezes or [upstream[0]], sources or data_files)
            # The production paths above are only an isolated test declaration.
            c.update(out=str(root / label), scale_entry_manifest=str(root / (label + '_entry.json')))
            plan = json.loads(Path('records/seven_b_entries/scale_cpu_fixture.json').read_text())
            plan['config'] = c
            Path(c['scale_entry_manifest']).write_text(json.dumps(plan))
            Path('records/seven_b_entries/scale_cpu_fixture.json').unlink()
            return c

        ordinary = fixture_entry('ordinary', template)
        expected = direct_adam(ordinary, large_config, adapter)
        dispatch(ordinary)
        saved = torch.load(root / 'ordinary/latest.pt', map_location='cpu', weights_only=False)
        for name, value in expected.items():
            torch.testing.assert_close(saved['parameters'][name], value, atol=2e-6, rtol=2e-6)
        tests['scale_CLI_ordinary_recovery_matches_independent_stock_Adam'] = 'PASS'
        initial = str(root / 'ordinary/initial.pt')
        paired = fixture_entry('paired', dict(template, method='paired_adam', initial_adapter=initial))
        dispatch(paired)
        paired_saved = torch.load(root / 'paired/latest.pt', map_location='cpu', weights_only=False)
        for architecture, freeze in zip(['L', 'X'], upstream):
            phi = json.loads(Path(freeze).read_text())['phi']
            c = fixture_entry('frozen_' + architecture,
                              dict(template, architecture=architecture, freeze_manifest=freeze,
                                   method='phi', phi=phi, initial_adapter=initial), freezes=[freeze])
            before = Path(phi).read_bytes()
            dispatch(c)
            actual = torch.load(Path(c['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            for name in actual['parameters']:
                assert torch.equal(actual['parameters'][name], paired_saved['parameters'][name])
            assert Path(phi).read_bytes() == before
            tests[f'frozen_{architecture}_on_wider_model_matches_paired_Adam_bitwise_and_phi_unchanged'] = 'PASS'
        for key, value in [('K', 64), ('inner_lr', .01), ('q_apply', .7),
                           ('data_seed', 18), ('parent', small), ('method', 'phi')]:
            reject(lambda: check_config(dict(ordinary, **{key: value})), 'settings')
        tests['actual_horizon_LR_dose_parent_method_and_stream_seed_are_frozen'] = 'PASS'
        text = Path(dev).read_text()
        Path(dev).write_text(text + '\n')
        reject(lambda: check_config(ordinary), 'bytes changed')
        Path(dev).write_text(text)
        tests['data_byte_changes_are_rejected_before_model_loading'] = 'PASS'
        freeze = Path(upstream[0])
        original_freeze = freeze.read_text()
        broken = json.loads(original_freeze)
        broken['phi_sha256'] = 'incorrect'
        freeze.write_text(json.dumps(broken))
        entry_file = Path(ordinary['scale_entry_manifest'])
        saved_entry = entry_file.read_text()
        e = json.loads(saved_entry)
        e['upstream_freezes'][str(freeze)] = sha256(freeze)
        e['config']['data'] = 'missing_DO_NOT_READ.jsonl'
        bad_config = e['config']
        entry_file.write_text(json.dumps(e))
        reject(lambda: check_config(bad_config), 'intact')
        freeze.write_text(original_freeze)
        entry_file.write_text(saved_entry)
        tests['even_Adam_validates_trained_phi_before_attempting_task_access'] = 'PASS'

        # A source trained on one width transfers phi + optimizer to another.
        warm = {'out': str(root / 'source_meta'), 'model': small_config['model'], 'rank': 2,
                'targets': 'all7', 'organization': 'O1', 'device': 'cpu', 'scale': 1.,
                'architecture': 'L', 'seed': 17, 'parents': [small], 'parent_data': [meta_a],
                'episode_K': [2], 'known_warm_episodes': 0, 'q_cycle': [.03],
                'meta_lr': .0003, 'inner_lr': .001, 'seq': 128, 'effective_tokens': 16,
                'retain_weight': .25, 'retain_fact_support': True, 'penalty': 1., 'chunk': 1024,
                'horizon': 'EXACT_H2_tail', 'world_manifest': str(world_manifest),
                'base_revision': 'CPU_RANDOM_ONLY'}
        meta.train(warm)
        warm_file = root / 'source_meta/latest.pt'
        local_sources = [meta_a, new, str(world_manifest),
                         'data/language/inner.jsonl', 'data/language/meta_query.jsonl']
        local = dict(warm, model=large_config['model'], parents=[large], parent_data=[meta_a],
                     episode_K=[2, 2], initial_phi=str(warm_file), start_episode=0,
                     load_initial_meta_optimizer=True)
        c = fixture_entry('local', local, 'meta', sources=local_sources)
        dispatch(c)
        local_saved = torch.load(root / 'local/latest.pt', map_location='cpu', weights_only=False)
        assert local_saved['next_episode'] == 2 and local_saved['inner_updates'] == 4
        assert all(int(v['step']) == 3 for v in local_saved['meta_optimizer']['state'].values())
        events = [json.loads(line) for line in (root / 'local/events.jsonl').read_text().splitlines()]
        starts = [e for e in events if e['event'] == 'episode_start']
        assert [e['mode'] for e in starts] == ['known', 'new']
        assert all(e['retain_route'] == 'half_language_half_known_facts' for e in starts)
        tests['cross_width_local_exact_H2_uses_known_new_and_three_support_routes_with_source_optimizer'] = 'PASS'
        tests['final_world_metadata_is_present_but_its_missing_file_is_never_opened_by_local_training'] = 'PASS'
        extended = dict(local, initial_phi=str(root / 'local/latest.pt'), start_episode=2,
                        episode_K=[2, 2, 2])
        extension = fixture_entry('extension', extended, 'meta', sources=local_sources)
        dispatch(extension)
        actual = torch.load(root / 'extension/latest.pt', map_location='cpu', weights_only=False)
        assert actual['inner_updates'] == 6 and actual['next_episode'] == 3
        assert all(int(v['step']) == 4 for v in actual['meta_optimizer']['state'].values())
        tests['local_budget_extension_continues_meta_optimizer_and_episode_order'] = 'PASS'
        interrupted = fixture_entry('interrupted', extended, 'meta', sources=local_sources)
        original_save = meta.atomic_save

        def interrupt(value, path):
            original_save(value, path)
            if value.get('next_episode') == 3:
                raise InterruptedError('CPU fixture checkpoint boundary')

        meta.atomic_save = interrupt
        try:
            try:
                dispatch(interrupted)
            except InterruptedError:
                pass
            else:
                raise AssertionError('Fixture did not interrupt')
        finally:
            meta.atomic_save = original_save
        dispatch(interrupted, resume=True)
        resumed = torch.load(root / 'interrupted/latest.pt', map_location='cpu', weights_only=False)
        for key in ['phi', 'meta_optimizer', 'next_episode', 'inner_updates']:
            same_tree(resumed[key], actual[key])
        tests['scale_CLI_interrupted_meta_resume_matches_uninterrupted_bitwise'] = 'PASS'
        # Re-sign a fixture declaration to ensure row permissions are checked.
        data = Path(meta_a).read_text()
        Path(meta_a).write_text(data.replace('"world_id": "meta_a"', '"world_id": "dev_a"'))
        p = Path(c['scale_entry_manifest'])
        declaration = json.loads(p.read_text())
        declaration['data_sha256'][meta_a] = sha256(meta_a)
        p.write_text(json.dumps(declaration))
        reject(lambda: check_config(c), 'meta world IDs')
        Path(meta_a).write_text(data)
        tests['development_rows_cannot_be_relabelled_as_meta_by_a_filename'] = 'PASS'

        selection = {'worlds': [
            {'world_id': f'{split}_{kind}_{i:02d}', 'split': split, 'kind': kind,
             'file': f'{split}_{kind}_{i}.jsonl'}
            for split in ['meta', 'dev', 'final'] for kind in ['fact', 'affine_integer']
            for i in range(6)]}
        chosen = select_worlds(selection, 'final', 4)
        assert len(chosen) == 8 and len(set(file for _, file in chosen)) == 8
        assert all('final_' in file for _, file in chosen)
        assert chosen == select_worlds(selection, 'final', 4)
        tests['eight_formal_worlds_have_fixed_four_fact_four_rule_selection'] = 'PASS'
        counts = validate_isolation({'meta': [meta_a, meta_b], 'dev': dev}, {'meta': [new]})
        assert len(counts) == 4
        reject(lambda: validate_isolation({'meta': [meta_a, meta_b], 'dev': dev}, {'meta': [meta_a]}),
               'overlap')
        source_text = Path(new).read_text()
        values = [json.loads(line) for line in source_text.splitlines()]
        Path(new).write_text(''.join(json.dumps(r) + '\n' for r in values if r['role'] == 'support'))
        reject(lambda: validate_isolation({'meta': [meta_a], 'dev': dev}, {'meta': [new]}), 'support/query')
        Path(new).write_text(source_text)
        tests['parent_meta_dev_worlds_are_disjoint_and_missing_query_bindings_rejected'] = 'PASS'
        metrics = []
        for group, score, count in [('known', 0., 2), ('fact', 1., 4), ('affine_integer', .5, 4)]:
            for _ in range(count):
                metrics.append((group, {'final': {'task': {'candidate_accuracy': score, 'strict_EM': score},
                                                 'keep': {'nll': 1.}, 'language': {'nll': 1.}},
                                        'clean': {'keep': {'nll': 1.}, 'language': {'nll': 1.}}}))
        assert dev_score(metrics) == .5
        scores = {'mixed_2048': .4, 'mixed_2560': .411, 'clean_2048': .5, 'clean_2560': .5}
        assert needs_extension(scores)
        scores['mixed_2560'] = .4
        assert not needs_extension(scores)
        assert POLICY['formal_data_can_select_settings'] is False
        tests['late_extension_is_development_only_with_equal_family_weight_and_matched_clean_mix_budget'] = 'PASS'
        assert backend_unavailable({'status': 'FAIL', 'error': 'RuntimeError: MUSA out of memory'}) == 'H2_UNAVAILABLE_MEMORY'
        assert backend_unavailable({'status': 'FAIL', 'error': 'derivative for op is not implemented'}) == 'H2_UNAVAILABLE_BACKEND'
        reject(lambda: backend_unavailable({'status': 'FAIL', 'error': 'incorrect tensor shape'}), 'Unclassified')
        tests['only_documented_memory_or_higher_order_backend_failures_allow_H1_fallback'] = 'PASS'
        configfile = root / 'device.json'
        configfile.write_text(json.dumps({'device': 'musa'}))
        assert uses_musa(['/usr/bin/python', '-m', 'gaosi.scale', '--config', str(configfile)])
        configfile.write_text(json.dumps({'device': 'cpu'}))
        assert not uses_musa(['/usr/bin/python', '-m', 'gaosi.scale', '--config', str(configfile)])
        tests['scale_recovery_and_meta_share_existing_MUSA_mutex'] = 'PASS'

        # Exercise the entire real dispatcher with fake compute outputs. The
        # real entry validation still runs; every task row is a renamed dev
        # fixture. This checks scheduling, not model effects or acquisition.
        fixture_worlds = [{'world_id': 'meta_new', 'kind': 'fact', 'split': 'meta', 'file': new}]
        for split, count in [('dev', 2), ('final', 4)]:
            for kind in ['fact', 'affine_integer']:
                for i in range(count):
                    label = f'{split}_{kind}_fixture_{i}'
                    fixture_worlds.append({'world_id': label, 'kind': kind, 'split': split,
                                           'file': make_data(label)})
        world_manifest.write_text(json.dumps({'worlds': fixture_worlds}))
        pipeline_freezes = {}
        for key, kind in [('O1', 'mixed'), ('O1_clean', 'clean')]:
            f = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'formal_data_used': False,
                 'organization': 'O1', 'architecture': 'L', 'training_noise': kind,
                 'phi': str(warm_file), 'phi_sha256': sha256(warm_file), 'recovery_K': 32,
                 'rank': 2, 'targets': 'all7', 'scale': 1., 'recovery_lr': .001,
                 'q_light': .01, 'q_medium': .03, 'horizon': 'EXACT_H2_tail',
                 'world_manifest': str(world_manifest), 'total_training_inner_steps': 3072}
            file = root / f'pipeline_{key}_freeze.json'
            file.write_text(json.dumps(f));pipeline_freezes[key] = str(file)
        Path('records/all_phi_frozen.json').write_text(json.dumps(
            {'status': 'ALL_PHI_FROZEN', 'manifests': pipeline_freezes}))
        parent_result = {'checkpoint': large, 'config': dict(large_config, device='musa', seed=17,
                         base_revision='d149729398750b98c0af14eb82c78cfe92750796')}
        (Path(large).parent / 'result.json').write_text(json.dumps(parent_result))
        Path('records/seven_b_early_ready_v1.json').write_text(json.dumps(
            {'status': 'EARLY_7B_ORDINARY_RECOVERY_COMPLETE', 'parent': large}))
        Path('records/parents_ready.json').write_text(json.dumps(
            [{'config': {'data': meta_a.replace('meta_a', 'meta_parent_a')}},
             {'config': {'data': meta_b.replace('meta_b', 'meta_parent_b')}}]))
        # Match the production meta-parent names while retaining distinct IDs.
        for source_file, destination in [(meta_a, meta_a.replace('meta_a', 'meta_parent_a')),
                                         (meta_b, meta_b.replace('meta_b', 'meta_parent_b'))]:
            Path(destination).write_text(Path(source_file).read_text())
        Path('checks').mkdir(exist_ok=True)
        Path('checks/seven_b_schedule_cpu.json').write_text(json.dumps(
            {'status': 'PASS', 'scope': 'dispatcher prerequisite fixture, not model results'}))
        Path('configs').mkdir(exist_ok=True)
        generated, fitted = [], []

        def fake_fit(name, config):
            config = dict(config, out='runs/' + name)
            folder = Path(config['out']);folder.mkdir(parents=True)
            (folder / 'latest.pt').write_bytes(b'CPU parent checkpoint placeholder')
            (folder / 'events.jsonl').write_text(json.dumps({'event': 'evaluation', 'n': 512}) + '\n')
            fitted.append(config)
            return {'config': config, 'checkpoint': str(folder / 'latest.pt'),
                    'acquisition_gate_passed': 'meta_a_256_' not in name,
                    'base_unchanged': True, 'fact_stream': {'covered': list(range(512))}}

        def fake_execute(name, config, action, upstream, data_files):
            c = queue.entry(name, config, action, upstream, data_files)
            generated.append(c)
            folder = Path(c['out']);folder.mkdir(parents=True)
            (folder / 'config.json').write_text(json.dumps(c))
            if action == 'meta':
                (folder / 'latest.pt').write_bytes(('CPU phi placeholder ' + name).encode())
                if sum(c['episode_K']) == 2560:
                    (folder / 'phi_2048.pt').write_bytes(('CPU phi 2048 ' + name).encode())
                return {'checkpoint': str(folder / 'latest.pt'), 'inner_updates': sum(c['episode_K']),
                        'base_unchanged': True}
            if not c.get('initial_adapter'):
                (folder / 'initial.pt').write_bytes(('CPU initial ' + str(c['q_apply'])).encode())
            score = .52 if 'dev_mixed_2560_' in name else .5
            metrics = {'task': {'candidate_accuracy': score, 'strict_EM': score},
                       'keep': {'nll': 1.}, 'language': {'nll': 1.}}
            return {'config': c, 'clean': copy.deepcopy(metrics), 'initial': copy.deepcopy(metrics),
                    'final': copy.deepcopy(metrics), 'coverage': 2, 'pool': 2,
                    'base_unchanged': True, 'compute_counters': {'fixture': True}}

        previous_argv = sys.argv
        sys.argv = ['seven_b_queue']
        try:
            with patch.object(queue, 'fit_parent', fake_fit), patch.object(queue, 'execute', fake_execute), \
                 patch.object(queue, 'profile', return_value={'status': 'PASS'}):
                queue.main()
        finally:
            sys.argv = previous_argv
        result = json.loads(Path('records/seven_b_scale_ready_v1.json').read_text())
        assert result['status'] == 'SEVEN_B_FROZEN_AND_LOCAL_PILOT_COMPLETE'
        assert result['local_budget']['matched_extension'] and result['local_budget']['local_inner_updates'] == 3584
        assert len(fitted) == 4 and [c['steps'] for c in fitted] == [256, 256, 512, 512]
        assert len(result['outcomes']) == 128
        assert {r['method'] for r in result['outcomes']} == {'standard_adam', 'paired_adam', 'phi_mix', 'phi_clean'}
        assert all(c['K'] == 32 for c in generated if c['scale_action'] == 'recover')
        local_configs = [c for c in generated if c['scale_action'] == 'meta']
        assert [sum(c['episode_K']) for c in local_configs] == [512, 512, 2560, 2560, 3584, 3584]
        assert [c['start_episode'] for c in local_configs] == [0, 0, 64, 64, 192, 192]
        formal_entries = [c for c in generated if c['scale_action'] == 'recover' and c['phase'] == 'formal']
        for q in [0., .03]:
            initials = {c['initial_adapter'] for c in formal_entries if c['q_apply'] == q and c.get('initial_adapter')}
            assert len(initials) == 1
        tests['full_dispatcher_keeps_all_128_pilot_arms_and_true_frozen_horizon'] = 'PASS'
        tests['either_parent_gate_failure_extends_both_7B_meta_parents_with_matching_coverage'] = 'PASS'
        tests['matched_512_2560_3584_schedule_preserves_episode_boundaries_and_one_initial_per_dose'] = 'PASS'
    os.chdir(original_directory)
    result = {'status': 'PASS', 'scope': 'Random CPU models of widths8/12 and renamed dev fixtures only; not real7B or phi effects',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
