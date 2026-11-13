"""CPU integration checks of the independent meta evidence wrapper.

Random tiny models validate state plumbing and equality of ordinary updates,
never MUSA correctness, acquisition or method quality.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from transformers import AutoTokenizer, Qwen2Config, Qwen2ForCausalLM
from gaosi.gradients import stable_phi_clip_
from gaosi.runtime import atomic_save
from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.merge import MergeMaster
import gaosi.meta as meta
from train_guarded_meta import guarded_train, check_original_entry


def same_tree(a, b):
    if torch.is_tensor(a):
        assert a.dtype == b.dtype and torch.equal(a, b)
    elif isinstance(a, dict):
        assert a.keys() == b.keys()
        for key in a:
            same_tree(a[key], b[key])
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        for x, y in zip(a, b):
            same_tree(x, y)
    else:
        assert a == b


def fixture_manifest():
    return {'config': 'fixture', 'pinned_files_sha256': {'fixture': 'fixture'},
            'raw_norm_stop_before_update': 1e6}


def failure_fixture(root, kind):
    torch.manual_seed(18)
    phi = torch.nn.Linear(2, 1)
    optimizer = torch.optim.Adam(phi.parameters(), lr=.0003, foreach=False)
    output = root / kind
    output.mkdir()
    config = {'out': str(output), 'episode_K': [2, 2]}
    starting = {n: p.detach().clone() for n, p in phi.named_parameters()}
    steps, clips = [], []

    def rollout(*args, **kwargs):
        if kind == 'rollout_failure':
            raise RuntimeError('fixture rollout failure')
        if kind == 'mutated_phi':
            with torch.no_grad():
                phi.bias.add_(1)

    def clip(*args, **kwargs):
        clips.append(True)
        returned = stable_phi_clip_(*args, **kwargs)
        if kind == 'clip_nonfinite':
            phi.bias.grad.fill_(float('inf'))
        return returned

    def train(current):
        for episode in range(2):
            active.rollout(None, None, None, phi)
            value = {'huge': 1e25, 'nonfinite': float('nan')}.get(kind, 2.)
            for p in phi.parameters():
                p.grad = torch.full_like(p, value)
            if kind == 'missing':
                phi.bias.grad = None
            active.stable_phi_clip_(phi.parameters(), 1.)
            optimizer.step()
            steps.append(episode)
            atomic_save({'config': current, 'next_episode': episode + 1,
                         'inner_updates': 2 * (episode + 1)}, output / 'latest.pt')

    active = SimpleNamespace(train=train, rollout=rollout, stable_phi_clip_=clip)
    try:
        guarded_train(active, torch, atomic_save, config, fixture_manifest(), output)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Failure fixture unexpectedly updated')
    assert active.rollout is rollout and active.stable_phi_clip_ is clip
    assert not steps and len(clips) == (1 if kind == 'clip_nonfinite' else 0)
    assert not optimizer.state
    if kind != 'mutated_phi':
        same_tree(starting, dict(phi.named_parameters()))
    if kind == 'rollout_failure':
        assert not list(output.glob('gradient_episode_*.pt'))
    else:
        row = json.loads((output / 'raw_gradients.jsonl').read_text())
        artifact = torch.load(output / 'gradient_episode_000.pt', weights_only=False)
        assert row['error'] and not row['update_authorized_by_guard']
        if kind == 'mutated_phi':
            assert not artifact['phi_unchanged_during_rollout']
            assert not torch.equal(artifact['starting_phi']['bias'], artifact['ending_phi_before_update']['bias'])
        if kind != 'clip_nonfinite':
            assert 'clipped_gradient' not in artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokenizer', default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if Path(args.out).exists():
        raise ValueError('Preserve previous validator results')
    torch.set_num_threads(2)
    tests = []
    with tempfile.TemporaryDirectory(prefix='gaosi_guarded_meta_') as directory:
        root = Path(directory)
        for kind in ['huge', 'nonfinite', 'missing', 'mutated_phi', 'clip_nonfinite', 'rollout_failure']:
            failure_fixture(root, kind)
            tests.append(kind + '_stops_before_optimizer_update_and_restores_hooks')
        for entry in ['scale', 'confirmation']:
            module = __import__('gaosi.' + entry, fromlist=['check_config'])
            with patch.object(module, 'check_config', side_effect=ValueError('original checker ran')):
                try:
                    check_original_entry({}, entry)
                except ValueError as error:
                    assert str(error) == 'original checker ran'
                else:
                    raise AssertionError('Original entry check bypassed')
            tests.append(entry + '_original_validation_retained')
        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        torch.manual_seed(821)
        tiny = Qwen2ForCausalLM(Qwen2Config(
            vocab_size=len(tokenizer), hidden_size=8, intermediate_size=16, num_hidden_layers=1,
            num_attention_heads=1, num_key_value_heads=1, head_dim=8, attention_dropout=0.,
            eos_token_id=tokenizer.eos_token_id, pad_token_id=tokenizer.eos_token_id))
        modelpath = root / 'tiny_qwen'
        tiny.save_pretrained(modelpath)
        tokenizer.save_pretrained(modelpath)
        del tiny
        data = rows('data/synthetic/meta_parent_a.jsonl')
        ids = sorted({r['fact_id'] for r in data})[:2]
        datafile = root / 'meta_fixture.jsonl'
        datafile.write_text(''.join(json.dumps(r) + '\n' for r in data if r['fact_id'] in ids))
        model = AdapterModel(str(modelpath), 2, 'all7', 'O1', 'cpu')
        with torch.no_grad():
            for name, parameter in model.adapter().items():
                if name.endswith('.B'):
                    parameter.normal_(0, .01)
        parent = root / 'parent.pt'
        atomic_save({'adapter': model.adapter_cpu(), 'acquisition_gate_passed': True,
                     'scope': 'RANDOM CPU FIXTURE; no acquisition claim'}, parent)
        del model
        worlds = root / 'worlds.json'
        worlds.write_text(json.dumps({'worlds': []}))
        config = {'model': str(modelpath), 'rank': 2, 'targets': 'all7', 'organization': 'O1',
                  'device': 'cpu', 'scale': 1., 'architecture': 'L', 'seed': 17,
                  'parents': [str(parent)], 'parent_data': [str(datafile)], 'episode_K': [2, 2, 2],
                  'known_warm_episodes': 10, 'q_cycle': [0., .01, .03], 'meta_lr': .0003,
                  'inner_lr': .001, 'seq': 64, 'effective_tokens': 8, 'retain_weight': .25,
                  'retain_fact_support': True, 'penalty': 1., 'chunk': 1024,
                  'horizon': 'H1', 'world_manifest': str(worlds), 'base_revision': 'random_cpu_fixture'}
        warm = root / 'captured_warm'
        warm.mkdir()

        def run_pair(label, settings, capture=False):
            baseline = dict(settings, out=str(root / (label + '_bare')))
            observed = dict(settings, out=str(root / (label + '_guarded')))
            original = meta.atomic_save

            def save(value, path):
                original(value, path)
                if capture and 'next_episode' in value:
                    original(value, warm / f"{value['next_episode']}.pt")

            with patch.object(meta, 'atomic_save', side_effect=save):
                meta.train(baseline)
            guarded_train(meta, torch, atomic_save, observed, fixture_manifest(), Path(observed['out']))
            left, right = [torch.load(Path(c['out']) / 'latest.pt', map_location='cpu', weights_only=False)
                           for c in [baseline, observed]]
            for key in ['phi', 'meta_optimizer', 'next_episode', 'inner_updates']:
                same_tree(left[key], right[key])
            assert all(torch.isfinite(t).all() for t in right['phi'].values())
            ledger = [json.loads(line) for line in (Path(observed['out']) / 'raw_gradients.jsonl').read_text().splitlines()]
            start = settings.get('start_episode', 0)
            assert [r['episode'] for r in ledger] == list(range(start, len(settings['episode_K'])))
            assert all(r['update_authorized_by_guard'] and not r.get('error') for r in ledger)
            if settings.get('initial_phi'):
                assert (Path(observed['out']) / f'before_episode_{start:03d}.pt').read_bytes() == Path(settings['initial_phi']).read_bytes()
            tests.append(label + '_phi_and_adam_state_byte_equal_to_original_trainer')
            return right

        run_pair('fresh_L_H1', config, capture=True)
        warm1 = str(warm / '1.pt')
        partial = dict(config, start_episode=1, initial_phi=warm1, load_initial_meta_optimizer=True)
        run_pair('partial_commonwarm_H1', partial)
        run_pair('partial_commonwarm_H2', dict(partial, horizon='EXACT_H2_tail'))
        run_pair('extension_absolute_episode3', dict(config, episode_K=[2] * 5, start_episode=3,
                 initial_phi=str(warm / '3.pt'), load_initial_meta_optimizer=True))
        adaptation = dict(config, episode_K=[2, 2], initial_phi=str(warm / '3.pt'),
                          load_initial_meta_optimizer=True, horizon='EXACT_H2_tail')
        for organization in ['O1', 'O2', 'O3']:
            run_pair('reset_index_adaptation_' + organization, dict(adaptation, organization=organization))
        historical_data = root / 'historical_fixture.jsonl'
        historical_id = sorted({r['fact_id'] for r in data})[2]
        historical_data.write_text(''.join(json.dumps(r) + '\n' for r in data if r['fact_id'] == historical_id))
        o4model = AdapterModel(str(modelpath), 2, 'all7', 'O4', 'cpu')
        o4model.load_adapter(torch.load(parent, weights_only=False)['adapter'])
        master = MergeMaster(o4model)
        master.merge(o4model)
        historical_base = root / 'historical_base.pt'
        master.save(historical_base, 'random_cpu_fixture')
        Path(str(historical_base) + '.json').write_text(json.dumps({
            'status': 'PASS', 'historical_bindings': str(historical_data),
            'scope': 'RANDOM CPU FIXTURE ONLY'}))
        # The current nonzero adapter and acquisition flag are artificial test
        # inputs; this fixture validates wrapper plumbing, never acquisition.
        o4parent = root / 'current_o4_fixture.pt'
        atomic_save({'adapter': torch.load(parent, weights_only=False)['adapter'],
                     'acquisition_gate_passed': True,
                     'config': {'organization': 'O4', 'historical_base': str(historical_base),
                                'data': str(datafile)}, 'scope': 'RANDOM CPU FIXTURE ONLY'}, o4parent)
        del master, o4model
        master_sha = __import__('hashlib').sha256(historical_base.read_bytes()).hexdigest()
        run_pair('reset_index_adaptation_O4', dict(adaptation, organization='O4',
                 parents=[str(o4parent)], historical_bases=[str(historical_base)],
                 historical_data=[str(historical_data)]))
        assert __import__('hashlib').sha256(historical_base.read_bytes()).hexdigest() == master_sha
        tests.append('O4_historical_FP32_master_unchanged')
        run_pair('fresh_X_H2', dict(config, architecture='X', horizon='EXACT_H2_tail', episode_K=[2, 2]))
        bad = dict(partial, out=str(root / 'wrong_boundary'), start_episode=2)
        try:
            guarded_train(meta, torch, atomic_save, bad, fixture_manifest(), Path(bad['out']))
        except ValueError as error:
            assert 'matching warm episode' in str(error)
        else:
            raise AssertionError('Wrong warm boundary accepted')
        tests.append('mismatched_warm_boundary_rejected')
    result = {'status': 'PASS', 'scope': 'CPU tiny random Qwen and failure fixtures only',
              'method_validated': False, 'MUSA_training_launched': False, 'tests': tests,
              'limitations': ['Full scale/confirmation entry semantics use their existing validators.',
                             'CPU equality does not establish MUSA numerical correctness.']}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open('x') as handle:
        handle.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
