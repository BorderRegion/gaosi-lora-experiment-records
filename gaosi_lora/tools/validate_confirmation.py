"""Independent-seed entry/runtime checks and explicitly simulated full schedules.

All data/checkpoints generated below are isolated fixtures. No formal effect,
MUSA compatibility, or completed independent training is inferred from them.
"""
import argparse
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

import confirmation_queue as queue
import gaosi.confirmation as confirmation
import gaosi.meta as meta
from gaosi.continual import check_config as check_continual
from gaosi.reflow import check_freezes, load_source
from gaosi.model import AdapterModel
from gaosi.scale import sha256
from device_guard import uses_musa, DEFAULT_LOCK
from validate_meta_resume import same_tree


def write(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2))
    return str(p)


def reject(call, fragment=None):
    try:
        call()
    except (ValueError, RuntimeError) as error:
        if fragment:
            assert fragment in str(error), str(error)
    else:
        raise AssertionError('Invalid independent-seed entry was accepted')


def data_file(label):
    path = f'data/synthetic/{label}.jsonl'
    values = [{'world_id': label, 'fact_id': f'{label}:binding{i}',
               'sample_id': f'{label}:{role}:{i}', 'role': role,
               'prompt': 'alpha beta', 'answer': ' '.join(['gamma'] * 64)}
              for role in ['support', 'query'] for i in range(2)]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(v) + '\n' for v in values))
    return path


def setup(steps, model_path='SIMULATED_MODEL_NOT_LOADED', adapter=None):
    for path in ['configs', 'records', 'runs', 'checks', 'data/language']:
        Path(path).mkdir(parents=True, exist_ok=True)
    for part in ['inner', 'meta_query', 'final']:
        p = Path(f'data/language/{part}.jsonl')
        p.write_text(json.dumps({'document_id': part, 'text': ' '.join(['alpha', 'beta', 'gamma'] * 400)}) + '\n')
    worlds = []
    for split, count in [('meta', 64), ('dev', 16), ('final', 32)]:
        for index in range(count):
            label = f'{split}_{index:02d}'
            file = data_file(label)
            worlds.append({'world_id': label, 'split': split,
                           'kind': 'fact' if index < count // 2 else 'affine_integer',
                           'file': Path(file).name})
    manifest = write('data/synthetic/manifest.json', {'worlds': worlds})
    calibration = {'status': 'CALIBRATION_FROZEN', 'formal_data_used': False,
                   'world_manifest': manifest, 'architecture': 'L', 'organization': 'O1',
                   'rank': 2, 'targets': 'all7', 'scale': 1., 'recovery_K': 32,
                   'recovery_lr': .0003, 'q_medium': .7, 'q_light': .5,
                   'capacity_lr': .0003, 'development_parent': 'SHARED_DEV_FIXTURE',
                   'stopping_rule_frozen': True, 'ordinary_recovery_validated': True}
    write('records/calibration_frozen.json', calibration)
    write('records/confirmation_policy_v1.json', queue.POLICY)
    torch.save({'phi': {}, 'scope': 'SIMULATED_PRIMARY_ONLY'}, 'runs/primary_phi.pt')
    original = dict(calibration, status='FROZEN', phi='runs/primary_phi.pt',
                    phi_sha256=sha256('runs/primary_phi.pt'), organization_plan='SIMULATED_PRIMARY_PLAN')
    write('records/phi_frozen_O1.json', original)
    write('records/all_phi_frozen.json', {'manifests': {'O1': 'records/phi_frozen_O1.json'}})
    selected = {'architecture': 'L', 'core_inner_steps': steps}
    write('records/phi_architecture_selected.json', selected)

    def parent(label, mode='all', targets=None, history=None, data=None):
        config = {'model': model_path, 'base_revision': 'RANDOM_CPU_FIXTURE', 'rank': 2,
                  'targets': 'all7', 'organization': 'O4' if history else 'O1',
                  'scale': 1., 'seq': 512, 'data': data or data_file(label),
                  'parent_layout': 'selective', 'parent_mode': mode, 'target_ids': targets or []}
        if history:
            config['historical_base'] = history
        file = Path('runs') / label / 'latest.pt'
        file.parent.mkdir(parents=True, exist_ok=True)
        torch.save({'adapter': adapter or {'fixture': torch.tensor([.125])}, 'config': config,
                    'base_digest': 'CPU_FIXTURE_DIGEST', 'acquisition_gate_passed': True}, file)
        result = {'status': 'PASS', 'checkpoint': str(file), 'config': config,
                  'acquisition_gate_passed': True, 'base_unchanged': True,
                  'scope': 'RANDOM_UNTRAINED_FIXTURE_NOT_ACQUIRED_FACTS'}
        write(file.parent / 'result.json', result)
        return result

    parents = [parent('meta_parent_a'), parent('meta_parent_b')]
    core = {'model': model_path, 'base_revision': 'RANDOM_CPU_FIXTURE', 'rank': 2,
            'targets': 'all7', 'scale': 1., 'organization': 'O1', 'device': 'cpu',
            'architecture': 'L', 'seed': 17, 'parents': [r['checkpoint'] for r in parents],
            'parent_data': [r['config']['data'] for r in parents],
            'episode_K': [8] * 64 + [16] * 128, 'known_warm_episodes': 64,
            'q_cycle': [0., .5, .7], 'meta_lr': .0003, 'inner_lr': .0003,
            'seq': 512, 'effective_tokens': 512, 'retain_weight': .25,
            'retain_fact_support': True, 'penalty': 1., 'chunk': 1024,
            'horizon': 'EXACT_H2_tail', 'world_manifest': manifest}
    write('configs/phi_mix_O1_L_H2_core_s17.json', core)
    histories, currents = [], []
    for i in range(2):
        path = f'runs/history_{i}/master.pt'
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({'scope': 'SIMULATED_O4_MASTER'}, path)
        info = {'status': 'PASS', 'master': path, 'historical_bindings': parents[i]['config']['data']}
        write(path + '.json', info)
        histories.append(info)
        currents.append(parent(f'meta_o4_current_{i}', history=path, data=parents[1-i]['config']['data']))
    write('records/o4_parents_ready.json', {'status': 'PASS', 'formal_data_used': False,
                                           'parents': currents, 'histories': histories})
    known = data_file('final_parent')
    targets = ['final_parent:binding0']
    all_parent = parent('final_all', targets=targets, data=known)
    retain = parent('final_retain', mode='retain_only', targets=targets, data=known)
    ready = {'parents': {'all': all_parent, 'retain_only': retain}, 'target_ids': targets,
             'matched_R_exposure': True, 'phi_frozen_before_training': True}
    write('records/final_parents_ready.json', ready)
    edata = data_file('final_entity')
    etargets = ['final_entity:binding0']
    entity = {'request': {'data': edata, 'target_ids': etargets,
                          'taught_ids': etargets, 'untaught_ids': ['final_entity:binding1']},
              'parents': {'all': parent('entity_all', targets=etargets, data=edata),
                          'retain_only': parent('entity_retain', mode='retain_only', targets=etargets, data=edata)}}
    write('records/entity_reflow_parents_ready_v1.json', entity)
    factor = {'formal_data_used': False, 'selection_K': 32, 'selected_lr': .0001, 'selected_ratio': 4}
    write('records/factor_recovery_frozen_v1.json', factor)
    rates = {'status': 'REFLOW_LRS_FROZEN', 'formal_data_used': False, 'recovery_K': 32,
             'development_parent': 'SHARED_DEV_FIXTURE', 'lrs': [.0001, .0003]}
    write('records/reflow_lrs_frozen_v1.json', rates)
    final = [f'data/synthetic/{w["file"]}' for w in worlds if w['split'] == 'final' and w['kind'] == 'fact']
    stream, probes = final[:8], final[8:12]
    source_files = [known, *stream, *probes, 'data/language/inner.jsonl', 'data/language/final.jsonl']
    plan = {'status': 'FROZEN_CONTINUAL_SCHEDULE', 'phi_frozen_before_selection': True,
            'worlds': stream, 'probe_worlds': probes, 'parent': all_parent['checkpoint'],
            'parent_data': known, 'day_steps': 32, 'night_steps': 16, 'day_lr': .0003,
            'night_lr': .0003, 'seed': 17, 'replay_seed': 1712, 'probe_seed': 37,
            'seq': 512, 'effective_tokens': 512, 'language_eval': 'data/language/final.jsonl',
            'language_eval_tokens': 4096, 'measure_inference': True, 'inference_repetitions': 5,
            'data_sha256': {p: sha256(p) for p in source_files}}
    outcomes = []
    for org in ['O1', 'O2', 'O3', 'O4']:
        for method in ['clean_Adam', 'noise_Adam', 'clean_phi', 'noise_phi']:
            run = f'runs/pilot_{org}_{method}'
            Path(run).mkdir()
            # O2 has the largest *negative* effect: selection must include it.
            value = .5 + ({'O1': 0, 'O2': -.2, 'O3': .05, 'O4': .1}[org] if method.endswith('phi') else 0)
            events = []
            for i in range(1, 9):
                metric = {'candidate_accuracy': value, 'strict_EM': value}
                events += [dict(event='history_evaluation', round=i, **metric),
                           dict(event='new_night_evaluation', round=i, metrics=metric)]
            Path(run, 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
            config = dict(plan, organization=org, rounds=8, phase='formal', device='cpu',
                          architecture='L', night_method='phi' if method.endswith('phi') else 'standard_adam',
                          freeze_manifest='records/phi_frozen_O1.json', schedule_manifest='PILOT_ONLY',
                          q_apply=.7 if method.startswith('noise') else 0.)
            config.pop('data_sha256')
            if method.endswith('phi'):
                config['phi'] = 'runs/primary_phi.pt'
            outcomes.append({'organization': org, 'method': method, 'run': run, 'completed_rounds': 8,
                             'result': {'status': 'PASS', 'config': config}})
    pilot = {'status': 'SIXTEEN_EIGHT_ROUND_PILOTS_COMPLETE', 'outcomes': outcomes,
             'plan': plan, 'base_reference': {'scope': 'SIMULATED_COMMON_BASE_REFERENCE'}}
    write('records/continual_pilot_ready_v1.json', pilot)
    write('checks/confirmation_cpu.json', {'status': 'PASS', 'scope': 'SIMULATED_GATE_FOR_ISOLATED_QUEUE_TEST'})
    return selected, original, pilot, core


def dispatch(config, resume=False):
    path = f'configs/{Path(config["out"]).name}.json'
    previous = sys.argv
    sys.argv = ['gaosi.confirmation', '--config', path] + (['--resume'] if resume else [])
    try:
        confirmation.main()
    finally:
        sys.argv = previous


def simulation(steps, second):
    selected, original, pilot, core = setup(steps)
    calls = []

    def simulated_job(name, command, resultfile):
        config = json.loads(Path(command[-1]).read_text())
        confirmation.check_config(config)  # Actual frozen-entry/data checks.
        action = config['confirmation_action']
        calls.append(copy.deepcopy(config))
        out = Path(config['out'])
        out.mkdir(parents=True)
        write(out / 'config.json', config)
        checkpoint = str(out / 'latest.pt')
        result = {'status': 'PASS', 'checkpoint': checkpoint, 'scope': 'SIMULATED_QUEUE_ONLY'}
        payload = {'config': config, 'base_digest': 'CPU_FIXTURE_DIGEST'}
        if action == 'meta':
            result['inner_updates'] = sum(config['episode_K'])
            payload.update(inner_updates=result['inner_updates'], phi={})
            # Deliberately omit result.config, as the production meta runner does.
        else:
            result['config'] = config
            if action == 'recover':
                payload.update(step=config['K'], parameters={'fixture': torch.tensor([.25])})
                result.update(base_unchanged=True, coverage=512, pool=512, final={'fixture': True})
                torch.save({'parent': config['parent'], 'noise_seed': 1729,
                            'q': config['q_apply'], 'scope': 'SIMULATED_INITIAL'}, out / 'initial.pt')
            elif action == 'reflow':
                frozen = check_freezes(config)
                parent = torch.load(config['parent'], weights_only=False, map_location='cpu')
                _, digest = load_source(config, parent['config'], 'CPU_FIXTURE_DIGEST', frozen)
                result.update(initial={'fixture': 0}, final={'fixture': 80},
                              coverage=448, pool=448, source_sha256=digest)
                (out / 'events.jsonl').write_text(''.join(json.dumps({'event': 'evaluation', 'step': k,
                    'metrics': {'scope': 'SIMULATED_NO_EFFECT_METRIC'}}) + '\n' for k in [0, 20, 80]))
            else:
                check_continual(config)
                result['completed_rounds'] = 8
        torch.save(payload, checkpoint)
        write(resultfile, result)
        return result

    with patch.object(queue, 'run_job', simulated_job):
        for seed in [29, 43]:
            freezes = queue.train_seed(seed, selected, original, second)
            queue.evaluate_seed(seed, freezes, json.loads(Path('records/final_parents_ready.json').read_text()),
                                original, json.loads(Path('records/factor_recovery_frozen_v1.json').read_text()),
                                json.loads(Path('records/entity_reflow_parents_ready_v1.json').read_text()),
                                json.loads(Path('records/reflow_lrs_frozen_v1.json').read_text()))
            queue.continual_seed(seed, freezes, pilot, second)
    assert len(calls) == 256
    for seed in [29, 43]:
        own = [c for c in calls if c['confirmation_seed'] == seed]
        assert {a: sum(c['confirmation_action'] == a for c in own)
                for a in ['meta', 'recover', 'reflow', 'continual']} == {'meta': 4, 'recover': 100, 'reflow': 16, 'continual': 8}
        training = [c for c in own if c['confirmation_action'] == 'meta']
        assert sorted(sum(c['episode_K']) for c in training) == [512, 512, steps, steps + 512]
        assert {c['organization'] for c in training} == {'O1', second}
        recoveries = [c for c in own if c['confirmation_action'] == 'recover']
        assert all(c['K'] == 32 and c['noise_seed'] == 1729 and c['data_seed'] == seed for c in recoveries)
        # Within each dose, all nine tasks/methods share the same damaged bytes.
        for q in [0., .7]:
            main = [c for c in recoveries if 'main_' in c['out'] and c['q_apply'] == q]
            initial = str(Path(main[0]['out']) / 'initial.pt')
            assert len(main) == 45 and all(c['initial_adapter'] == initial for c in main[1:])
        selective = [c for c in recoveries if 'selective_' in c['out']]
        assert {(c['retain_subset_fraction'], c['method'], c['D']) for c in selective} == {
            (r, m, d) for r in [1., .25] for m in ['standard_adam', 'phi'] for d in ['plus', 'minus']}
        cont = [c for c in own if c['confirmation_action'] == 'continual']
        assert all(c['replay_seed'] == seed * 100 + 12 and c['probe_seed'] == 37
                   and c['rounds'] == 8 and c['measure_inference'] for c in cont)
        assert len({tuple(c['worlds']) for c in cont}) == 1
        assert set(cont[0]['worlds']) == set(pilot['plan']['worlds'])
        for label in ['recovery', 'continual']:
            assert Path(f'records/confirmation_{label}_ready_s{seed}_v1.json').exists()
    assert calls[0]['seed'] == 29
    return calls


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='checks/confirmation_cpu.json')
    args = parser.parse_args()
    original_directory = Path.cwd()
    output = Path(args.out).resolve()
    torch.set_num_threads(2)
    tests = {}
    with tempfile.TemporaryDirectory(prefix='gaosi_confirmation_cpu_') as directory, ExitStack() as runtime_patches:
        root = Path(directory)
        try:
            for steps, second in [(2560, 'O4'), (3584, 'O3')]:
                folder = root / f'grid{steps}'
                folder.mkdir()
                os.chdir(folder)
                calls = simulation(steps, second)
                tests[f'simulated_complete_two_seed_grid_{steps}_512_and_{second}_paired'] = 'PASS'
                tests[f'simulated_true_K32_100_recoveries_16_reflows_8_continual_per_seed_{steps}'] = 'PASS'
            # Selection reads preserved sixteen-arm event streams, including negatives.
            pilot = json.loads(Path('records/continual_pilot_ready_v1.json').read_text())
            assert queue.select_second(pilot)['organization'] == 'O2'
            tests['negative_pilot_effect_can_select_second_organization'] = 'PASS'
            reject(lambda: queue.select_second(dict(pilot, outcomes=pilot['outcomes'][:-1])), 'Incomplete')
            bad = copy.deepcopy(pilot)
            bad['outcomes'][0]['completed_rounds'] = 7
            reject(lambda: queue.select_second(bad), 'eight-round')
            events_path = Path(pilot['outcomes'][0]['run']) / 'events.jsonl'
            original_events = events_path.read_text()
            events_path.write_text(original_events + original_events.splitlines()[0] + '\n')
            reject(lambda: queue.select_second(pilot), 'measurement once')
            events_path.write_text(original_events)
            for row in pilot['outcomes']:
                p = Path(row['run']) / 'events.jsonl'
                events = [json.loads(s) for s in p.read_text().splitlines()]
                for event in events:
                    metric = event['metrics'] if 'metrics' in event else event
                    metric.update(candidate_accuracy=.5, strict_EM=.5)
                p.write_text(''.join(json.dumps(e) + '\n' for e in events))
            assert queue.select_second(pilot)['organization'] == 'O2'
            tests['incomplete_duplicate_measurements_rejected_and_ties_prefer_O2'] = 'PASS'
            # Whole dispatcher, including its prerequisite gates and final records.
            # Existing completed fixture results are replayed, never real model work.
            def replay_job(name, command, resultfile):
                result = json.loads(Path(resultfile).read_text())
                confirmation.check_config(json.loads(Path(command[-1]).read_text()))
                return result
            # Already simulated O3 above; freeze the corresponding exploratory choice.
            with patch.object(queue, 'select_second', return_value={'organization': 'O3', 'scope': 'SIMULATED_SELECTION'}), patch.object(queue, 'run_job', replay_job):
                queue.main()
            summary = json.loads(Path('records/confirmation_ready_v1.json').read_text())
            assert summary['seeds'] == [29, 43] and not summary['overall_experiment_completion']
            tests['dispatcher_completion_records_do_not_claim_overall_experiment_complete'] = 'PASS'
            # Tampering is rejected even if an altered config is re-declared in isolation.
            c = next(c for c in calls if c['confirmation_action'] == 'meta')
            frozen_entry = json.loads(Path(c['confirmation_entry_manifest']).read_text())
            def redeclare(mutate, fragment):
                new = copy.deepcopy(c)
                mutate(new)
                record = dict(frozen_entry, config=new)
                write(c['confirmation_entry_manifest'], record)
                reject(lambda: confirmation.check_config(new), fragment)
                write(c['confirmation_entry_manifest'], frozen_entry)
            redeclare(lambda v: v.update(initial_phi='runs/primary_phi.pt'), 'fresh meta optimizer')
            redeclare(lambda v: v.update(meta_lr=.001), 'frozen meta_lr')
            redeclare(lambda v: v.update(episode_K=[8]), 'schedule changed')
            redeclare(lambda v: v.update(parent_data=list(reversed(v['parent_data']))), 'stay paired')
            tests['seed17_warm_start_changed_rates_budgets_and_unpaired_parents_rejected'] = 'PASS'
            modified = copy.deepcopy(c)
            modified['seed'] = 43
            reject(lambda: confirmation.check_config(modified), 'frozen entry')
            token_path = c['parent_data'][0]
            original_data = Path(token_path).read_text()
            Path(token_path).write_text(original_data + '\n')
            reject(lambda: confirmation.check_config(c), 'input bytes changed')
            Path(token_path).write_text(original_data)
            tests['entry_config_and_input_bytes_are_frozen'] = 'PASS'
            # Update declared hashes to exercise permission checks, rather than
            # merely testing that an input checksum changes.
            meta_source = c['parent_data'][0]
            saved_source = Path(meta_source).read_text()
            final_rows = [dict(json.loads(line), world_id='final_FORBIDDEN')
                          for line in saved_source.splitlines()]
            Path(meta_source).write_text(''.join(json.dumps(row) + '\n' for row in final_rows))
            changed = copy.deepcopy(frozen_entry)
            changed['data_sha256'][meta_source] = sha256(meta_source)
            write(c['confirmation_entry_manifest'], changed)
            reject(lambda: confirmation.check_config(c), 'meta-world rows')
            Path(meta_source).write_text(saved_source)
            write(c['confirmation_entry_manifest'], frozen_entry)
            tests['final_world_rows_rejected_even_with_redeclared_input_hashes'] = 'PASS'
            recovery = next(v for v in calls if v['confirmation_action'] == 'recover')
            rpath = recovery['confirmation_entry_manifest']
            rentry = json.loads(Path(rpath).read_text())
            changed_config = dict(recovery, K=64)
            write(rpath, dict(rentry, config=changed_config))
            reject(lambda: confirmation.check_config(changed_config), 'actual primary K')
            write(rpath, rentry)
            tests['K64_endpoint_cannot_replace_true_primary_K32_confirmation'] = 'PASS'
            # Freeze validation must precede reading even the data hashes.
            fpath = recovery['freeze_manifest']
            ftext = Path(fpath).read_text()
            frozen = json.loads(ftext)
            frozen['status'] = 'NOT_FROZEN'
            write(fpath, frozen)
            missing_data = recovery['data']
            missing_text = Path(missing_data).read_text()
            Path(missing_data).unlink()
            reject(lambda: confirmation.check_config(recovery), 'intact frozen phi')
            Path(missing_data).write_text(missing_text)
            Path(fpath).write_text(ftext)
            tests['even_stock_Adam_validates_phi_freeze_before_opening_task_data'] = 'PASS'
            musa_config = dict(c, device='musa')
            write('configs/musa_lock_fixture.json', musa_config)
            assert uses_musa(['/usr/bin/python', '-m', 'gaosi.confirmation', '--config', 'configs/musa_lock_fixture.json'])
            assert DEFAULT_LOCK == '/tmp/gaosi_musa_device0.lock'
            assert not uses_musa(['/usr/bin/python', 'tools/confirmation_queue.py'])
            tests['only_confirmation_leaf_uses_existing_shared_device_lock'] = 'PASS'

            # Real CPU Qwen training through the actual confirmation entry, with
            # deliberate interruptions after 1/2 episodes of the full schedule.
            numeric = root / 'numeric'
            numeric.mkdir()
            os.chdir(numeric)
            tokenizer_impl = Tokenizer(WordLevel({'[UNK]': 0, '[EOS]': 1, 'alpha': 2, 'beta': 3, 'gamma': 4}, unk_token='[UNK]'))
            tokenizer_impl.pre_tokenizer = Whitespace()
            tokenizer = PreTrainedTokenizerFast(tokenizer_object=tokenizer_impl,
                                                unk_token='[UNK]', eos_token='[EOS]', pad_token='[EOS]')
            # Transformers 5.13 infers Qwen2Tokenizer from the model config
            # when reloading the generic TokenizersBackend and yields no tokens
            # for this WordLevel fixture. Supply that explicit fixture tokenizer
            # at the loader boundary; model/transition/autograd remain production.
            assert tokenizer.encode('alpha beta gamma', add_special_tokens=False) == [2, 3, 4]
            runtime_patches.enter_context(patch('gaosi.model.AutoTokenizer.from_pretrained', return_value=tokenizer))
            model_path = str(numeric / 'tiny_qwen')
            torch.manual_seed(930)
            tiny = Qwen2ForCausalLM(Qwen2Config(vocab_size=5, hidden_size=8, intermediate_size=16,
                                               num_hidden_layers=1, num_attention_heads=1,
                                               num_key_value_heads=1, head_dim=8,
                                               eos_token_id=1, pad_token_id=1, attention_dropout=0.))
            tiny.save_pretrained(model_path)
            tokenizer.save_pretrained(model_path)
            del tiny
            model = AdapterModel(model_path, 2, 'all7', 'O1', 'cpu')
            with torch.no_grad():
                for name, value in model.adapter().items():
                    if name.endswith('.B'):
                        value.normal_(0, .01)
            adapter = model.adapter_cpu()
            del model
            selected, original, pilot, core = setup(2560, model_path, adapter)
            real_save, real_phi = meta.atomic_save, meta.Phi
            initializations, fresh_optimizer_sizes, captures = {}, {}, {}
            stop_at, active_seed = 1, 29
            real_adam_step = torch.optim.Adam.step
            def capture_phi(*args, **kwargs):
                value = real_phi(*args, **kwargs)
                initializations.setdefault(active_seed, copy.deepcopy(value.state_dict()))
                return value
            def capture_optimizer(optimizer, *args, **kwargs):
                fresh_optimizer_sizes.setdefault(active_seed, len(optimizer.state))
                return real_adam_step(optimizer, *args, **kwargs)
            def interrupt_save(value, path):
                real_save(value, path)
                if value.get('next_episode') == stop_at:
                    raise InterruptedError('Deliberate tiny CPU fixture stop at complete episode')
            def numerical_job(name, command, resultfile):
                config = json.loads(Path(command[-1]).read_text())
                captures[active_seed] = config
                dispatch(config)
                raise AssertionError('Full production training must not run in a CPU fixture')
            with patch.object(meta, 'Phi', capture_phi), patch.object(meta, 'atomic_save', interrupt_save), patch.object(torch.optim.Adam, 'step', capture_optimizer), patch.object(queue, 'run_job', numerical_job):
                for seed in [29, 43]:
                    active_seed = seed
                    try:
                        queue.train_seed(seed, selected, original, 'O2')
                    except InterruptedError:
                        pass
                    else:
                        raise AssertionError('Expected numerical fixture interruption')
            assert fresh_optimizer_sizes == {29: 0, 43: 0}
            assert not torch.equal(initializations[29]['net.0.weight'], initializations[43]['net.0.weight'])
            for state in initializations.values():
                assert torch.count_nonzero(state['net.4.weight']) == 0 and torch.count_nonzero(state['net.4.bias']) == 0
            checkpoints = {seed: torch.load(Path(captures[seed]['out']) / 'latest.pt', map_location='cpu', weights_only=False) for seed in [29, 43]}
            assert all(v['next_episode'] == 1 and v['inner_updates'] == 8 for v in checkpoints.values())
            assert any(not torch.equal(checkpoints[29]['phi'][k], checkpoints[43]['phi'][k]) for k in checkpoints[29]['phi'])
            tests['real_two_seed_Qwen_H2_entries_initialize_distinct_phi_zero_heads_fresh_Adam'] = 'PASS'
            tests['real_first8_updates_checkpoint_config_contains_independent_seed_and_meta_order'] = 'PASS'
            # Resume one exact entry; compare its second episode to an uninterrupted repeat.
            active_seed, stop_at = 29, 2
            with patch.object(meta, 'atomic_save', interrupt_save):
                try:
                    dispatch(captures[29], resume=True)
                except InterruptedError:
                    pass
            resumed = torch.load(Path(captures[29]['out']) / 'latest.pt', map_location='cpu', weights_only=False)
            entry_manifest = json.loads(Path(captures[29]['confirmation_entry_manifest']).read_text())
            with patch.object(meta, 'atomic_save', interrupt_save), patch.object(queue, 'run_job', numerical_job):
                try:
                    queue.entry('numeric_uninterrupted_seed29', captures[29], 'meta', 29,
                                list(entry_manifest['artifact_sha256']), list(entry_manifest['data_sha256']), [])
                except InterruptedError:
                    pass
            uninterrupted = torch.load('runs/numeric_uninterrupted_seed29/latest.pt', map_location='cpu', weights_only=False)
            for key in ['phi', 'meta_optimizer', 'next_episode', 'inner_updates']:
                same_tree(resumed[key], uninterrupted[key])
            tests['real_interrupted_confirmation_resume_matches_phi_optimizer_and_episode_bitwise'] = 'PASS'
            # Verify meta-only validation reads no final task content.
            cfg = json.loads(Path('configs/confirm_phi_mix_O1_L_core_s43_v1.json').read_text())
            final_file = Path('data/synthetic/final_00.jsonl')
            final_file.unlink()
            confirmation.check_config(cfg)
            tests['meta_entry_never_reads_final_world_files'] = 'PASS'
        finally:
            os.chdir(original_directory)
    result = {'status': 'PASS', 'scope': 'Real random tiny CPU H2 seed/init/optimizer/resume checks plus explicitly simulated full scheduling; no production MUSA/3B or formal effect evidence',
              'checks': tests, 'count': len(tests)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
