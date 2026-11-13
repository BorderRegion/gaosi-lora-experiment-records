"""Independent stock-Adam continuation and interruption checks on dev fixtures."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM

from gaosi.data import FactStream, rows, route_batches, token_count
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save
from gaosi.transition import Phi
import gaosi.reflow as reflow
from device_guard import uses_musa
from reflow_queue import freeze_rates, entity_target_gate


def independent_adam(config, parent_config, adapter, selected_ids):
    model = AdapterModel(parent_config['model'], 2, 'all7', 'O1', 'cpu')
    model.load_adapter(adapter)
    selected = [row for row in rows(config['data'], 'support') if row['fact_id'] in selected_ids]
    stream = FactStream(selected, model.tokenizer, config['data_seed'], parent_config['seq'])
    optimizer = torch.optim.Adam(list(model.adapter(True).values()), lr=config['inner_lr'],
                                 betas=(.9, .999), eps=1e-8, foreach=False)
    for step in range(config['K']):
        optimizer.zero_grad(set_to_none=True)
        batches, total = route_batches(stream, 'cpu', config['effective_tokens'])
        for batch in batches:
            (model(batch) * token_count(batch) / total).backward()
        torch.nn.utils.clip_grad_norm_(list(model.adapter(True).values()), 1.)
        optimizer.step()
    return model.adapter_cpu(), stream.state_dict()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', default='checks/reflow_v2_cpu.json')
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(4921)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_reflow_') as directory:
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
        entity = min(row['entity'] for row in source)
        targets = sorted({row['fact_id'] for row in source if row['entity'] == entity})
        R = min(row['fact_id'] for row in source if row['entity'] != entity)
        data = [row for row in source if row['fact_id'] in set(targets + [R])]
        datafile = root / 'dev_fixture.jsonl'
        datafile.write_text(''.join(json.dumps(row) + '\n' for row in data))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, value in model.adapter().items():
                if name.endswith('.B'):
                    value.normal_(0, .01)
        adapter, digest = model.adapter_cpu(), model.base_digest()
        del model
        parent_config = {'model': str(modelpath), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
                         'scale': 1., 'seq': 128, 'data': str(datafile), 'base_revision': 'RANDOM_CPU_FIXTURE',
                         'parent_layout': 'selective', 'parent_mode': 'all', 'target_ids': targets}
        parent = root / 'parent/latest.pt'
        atomic_save({'adapter': adapter, 'config': parent_config, 'base_digest': digest}, parent)
        (parent.parent / 'result.json').write_text(json.dumps({
            'status': 'PASS', 'config': parent_config, 'acquisition_gate_passed': True,
            'base_unchanged': True, 'scope': 'RANDOM CPU FIXTURE ONLY, NOT ACQUISITION EVIDENCE'}))
        phi = root / 'phi.pt'
        atomic_save({'phi': Phi('L').state_dict()}, phi)
        freeze = root / 'freeze.json'
        frozen = {'status': 'FROZEN', 'stopping_rule_frozen': True, 'phi': str(phi),
                  'phi_sha256': reflow.file_hash(phi), 'organization': 'O1', 'training_noise': 'mixed',
                  'formal_data_used': False, 'recovery_K': 2, 'q_medium': .7,
                  'development_parent': 'CPU_FIXTURE_DEV_PARENT'}
        freeze.write_text(json.dumps(frozen))
        ratesfile = root / 'rates.json'
        ratesfile.write_text(json.dumps({'status': 'REFLOW_LRS_FROZEN', 'formal_data_used': False,
                                        'lrs': [.001, .0003], 'recovery_K': 2,
                                        'development_parent': frozen['development_parent']}))
        selective = root / 'selective/latest.pt'
        sc = {'task_layout': 'selective', 'D': 'minus', 'data': str(datafile), 'parent': str(parent),
              'target_ids': targets, 'K': 2, 'q_apply': .7, 'method': 'standard_adam'}
        atomic_save({'parameters': adapter, 'config': sc, 'step': 2, 'base_digest': digest}, selective)
        (selective.parent / 'result.json').write_text(json.dumps({
            'status': 'PASS', 'config': sc, 'base_unchanged': True, 'scope': 'CPU_RANDOM_FIXTURE'}))
        retain = root / 'retain/latest.pt'
        rc = dict(parent_config, parent_mode='retain_only')
        atomic_save({'adapter': adapter, 'config': rc, 'base_digest': digest}, retain)
        (retain.parent / 'result.json').write_text(json.dumps({
            'status': 'PASS', 'config': rc, 'acquisition_gate_passed': True, 'base_unchanged': True,
            'scope': 'CPU_RANDOM_FIXTURE'}))
        template = {'out': '', 'freeze_manifest': str(freeze), 'lr_freeze_manifest': str(ratesfile),
                    'parent': str(parent), 'source_checkpoint': str(selective), 'source_kind': 'selective',
                    'data': str(datafile), 'target_ids': targets, 'taught_ids': [], 'mode': 'pure_R',
                    'device': 'cpu', 'data_seed': 17, 'inner_lr': .001, 'effective_tokens': 16,
                    'language_eval': 'data/language/dev.jsonl', 'language_eval_tokens': 16,
                    'K': 2, 'eval_steps': [1, 2]}
        saved = {}
        configs = {}
        for label, mode, kind in [('pureR', 'pure_R', 'selective'),
                                  ('reteach', 'reteach_two', 'selective'),
                                  ('retain', 'pure_R', 'retain_only')]:
            taught = targets[:2] if mode == 'reteach_two' else []
            c = dict(template, out=str(root / label), mode=mode, taught_ids=taught, source_kind=kind,
                     source_checkpoint=str(retain) if kind == 'retain_only' else str(selective))
            selected = set(taught) if taught else {R}
            expected, stream = independent_adam(c, parent_config, adapter, selected)
            snapshots = []
            original_save = reflow.atomic_save

            def capture(value, path):
                if value.get('step') == 0:
                    snapshots.append(value['adapter'])
                original_save(value, path)

            reflow.atomic_save = capture
            try:
                reflow.run_reflow(c)
            finally:
                reflow.atomic_save = original_save
            actual = torch.load(root / label / 'latest.pt', map_location='cpu', weights_only=False)
            for name in expected:
                torch.testing.assert_close(actual['adapter'][name], expected[name], atol=2e-6, rtol=2e-6)
                assert torch.equal(snapshots[0][name], adapter[name])
            assert actual['stream'] == stream
            result = json.loads((root / label / 'result.json').read_text())
            assert result['final']['F']['n'] == 8 and result['final']['R']['n'] == 1
            assert result['coverage'] == result['pool'] == len(selected) and result['base_unchanged']
            if taught:
                assert result['final']['taught_two']['n'] == 2 and result['final']['untaught_six']['n'] == 6
            saved[label], configs[label] = actual, c
            tests[label + '_matches_independent_fresh_stock_Adam_and_exact_source_without_QR'] = 'PASS'
        for label in ['pureR', 'reteach']:
            taught = set(configs[label]['taught_ids'])
            changed = []
            for row in data:
                row = dict(row)
                # Change F labels that are excluded from this training request,
                # including held-out queries; measured losses may change.
                if row['fact_id'] in set(targets) - taught:
                    row['answer'] = row['candidates'][(row['candidates'].index(row['answer']) + 1) % len(row['candidates'])]
                    if row['role'] == 'support':
                        row['prompt'] = 'EXCLUDED FROM TRAINING. Answer: '
                changed.append(row)
            datafile.write_text(''.join(json.dumps(row) + '\n' for row in changed))
            c = dict(configs[label], out=str(root / (label + '_heldout_changed')))
            reflow.run_reflow(c)
            changed_state = torch.load(Path(c['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            for name in saved[label]['adapter']:
                assert torch.equal(saved[label]['adapter'][name], changed_state['adapter'][name])
            datafile.write_text(''.join(json.dumps(row) + '\n' for row in data))
            tests[label + '_excluded_F_support_and_queries_do_not_affect_updates_bitwise'] = 'PASS'
        for boundary in [1, 2]:
            c = dict(configs['pureR'], out=str(root / f'interrupted_{boundary}'))
            original_save = reflow.atomic_save

            def interrupt(value, path):
                original_save(value, path)
                if value.get('step') == boundary:
                    raise InterruptedError('simulated checkpoint-before-evaluation interruption')

            reflow.atomic_save = interrupt
            try:
                try:
                    reflow.run_reflow(c)
                except InterruptedError:
                    pass
                else:
                    raise AssertionError('Interruption was not reached')
            finally:
                reflow.atomic_save = original_save
            reflow.run_reflow(c, resume=True)
            actual = torch.load(Path(c['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            for name in saved['pureR']['adapter']:
                assert torch.equal(actual['adapter'][name], saved['pureR']['adapter'][name])
            assert actual['stream'] == saved['pureR']['stream']
            events = [json.loads(line) for line in (Path(c['out']) / 'events.jsonl').read_text().splitlines()]
            assert [event['step'] for event in events if event['event'] == 'evaluation'] == [0, 1, 2]
            if boundary == 1:
                assert next(i for i,e in enumerate(events) if e['event']=='evaluation' and e['step']==1) < next(
                    i for i,e in enumerate(events) if e['event']=='reflow' and e['step']==2)
            tests[f'restart_at_{boundary}_restores_state_bitwise_and_completes_pending_evaluation'] = 'PASS'
        freeze.write_text(json.dumps(dict(frozen, phi_sha256='invalid')))
        try:
            reflow.run_reflow(dict(template, data='missing_DO_NOT_READ', parent='missing_DO_NOT_READ'))
        except ValueError as error:
            assert 'intact frozen' in str(error)
        else:
            raise AssertionError('Accepted bad phi freeze')
        freeze.write_text(json.dumps(frozen))
        tests['phi_freeze_checked_before_task_or_parent_access'] = 'PASS'
        try:
            reflow.run_reflow(dict(template, inner_lr=.002, data='missing_DO_NOT_READ'))
        except ValueError as error:
            assert 'development-frozen' in str(error)
        else:
            raise AssertionError('Accepted unselected continuation rate')
        tests['undeclared_rate_rejected_before_data_access'] = 'PASS'
        for bad in [targets[:1], targets[:3], [targets[0], R]]:
            try:
                reflow.request_pools(data, targets, 'reteach_two', bad)
            except ValueError:
                pass
            else:
                raise AssertionError('Accepted invalid two-of-eight request')
        tests['only_exact_two_of_eight_same_entity_request_is_accepted'] = 'PASS'
        predictions = [{'fact_id': key, 'rank_correct': 0, 'strict_EM': 0} for key in targets]
        assert not entity_target_gate(predictions, targets)['passed']
        assert not entity_target_gate(predictions[:7], targets)['passed']
        for row in predictions:
            row.update(rank_correct=1, strict_EM=1)
        assert entity_target_gate(predictions, targets)['passed']
        tests['entity_acquisition_checks_all_eight_F_queries_separately_from_global_accuracy'] = 'PASS'
        for device in ['cpu', 'musa']:
            c = root / f'guard_{device}.json'
            c.write_text(json.dumps({'device': device}))
            assert uses_musa(['/usr/bin/python', '-m', 'gaosi.reflow', '--config', str(c)]) == (device == 'musa')
        tests['reflow_MUSA_leaf_uses_existing_device_mutex_CPU_does_not'] = 'PASS'

        # Rate ranking has a synthetic dev grid and writes only into its own
        # temporary directory, never touching real calibration manifests.
        calibration = {'recovery_K': 2, 'development_parent': 'CPU_FIXTURE_DEV_PARENT',
                       'q_medium': .7, 'trials': {}}
        for rate, accuracy in [(.00003, .8), (.0001, .9), (.0003, .9)]:
            folder = root / f'rate_{rate}'
            folder.mkdir()
            c = {'method': 'standard_adam', 'phase': 'development', 'parent': calibration['development_parent'],
                 'data': 'data/synthetic/dev_parent.jsonl', 'q_apply': .7, 'inner_lr': rate}
            metrics = {'task': {'candidate_accuracy': accuracy, 'strict_EM': accuracy}, 'language': {'nll': 2.}}
            (folder / 'result.json').write_text(json.dumps({'status': 'PASS', 'base_unchanged': True,
                'config': c, 'clean': {'language': {'nll': 2.}}}))
            (folder / 'events.jsonl').write_text(json.dumps({'event':'evaluation','step':2,'metrics':metrics})+'\n')
            calibration['trials'][str(rate)] = str(folder)
        import reflow_queue
        original_preserve = reflow_queue.preserve_json
        captured = {}
        reflow_queue.preserve_json = lambda path, value: captured.update(value)
        try:
            record = freeze_rates(calibration)
        finally:
            reflow_queue.preserve_json = original_preserve
        assert captured == record and record['lrs'] == [.0001, .0003]
        assert not record['formal_data_used'] and not record['reflow_outcomes_used']
        tests['two_rates_chosen_only_from_dev_primary_K_with_declared_tie_rule'] = 'PASS'
    result = {'status': 'PASS', 'scope': 'Random tiny CPU/dev fixtures only, no MUSA reflow effect evidence',
              'tests': tests}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
