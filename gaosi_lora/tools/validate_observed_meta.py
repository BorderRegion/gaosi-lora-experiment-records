"""CPU fixture checks of gradient capture, unchanged updates and stop behavior."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from gaosi.gradients import stable_phi_clip_
from gaosi.runtime import atomic_save
from train_observed_meta import observed_train


def exercise(kind):
    torch.manual_seed(18)
    phi = torch.nn.Linear(2, 1)
    reference = torch.nn.Linear(2, 1)
    reference.load_state_dict(phi.state_dict())
    optimizer = torch.optim.Adam(phi.parameters(), lr=.0003, foreach=False)
    reference_optimizer = torch.optim.Adam(reference.parameters(), lr=.0003, foreach=False)
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp)
        config = {'out': str(out), 'episode_K': [8, 8]}
        manifest = {'raw_norm_stop_before_update': 1e6, 'config': 'fixture.json',
                    'pinned_files_sha256': {'fixture.json': 'fixture'}}
        steps, clip_calls = [], []

        def rollout(*args, **kwargs):
            if kind == 'rollout_failure':
                raise RuntimeError('fixture rollout failure')
            if kind == 'mutated_phi':
                with torch.no_grad():
                    phi.bias.add_(1)

        def clip(*args, **kwargs):
            clip_calls.append(True)
            return stable_phi_clip_(*args, **kwargs)

        def train(current):
            for episode in range(2):
                meta.rollout(None, None, None, phi)
                value = {'huge': 1e25, 'nonfinite': float('nan')}.get(kind, 2.)
                for p in phi.parameters():
                    p.grad = torch.full_like(p, value)
                meta.stable_phi_clip_(phi.parameters(), 1.)
                optimizer.step()
                steps.append(episode)
                atomic_save({'next_episode': episode + 1, 'config': current}, out / 'latest.pt')

        meta = SimpleNamespace(train=train, rollout=rollout, stable_phi_clip_=clip)
        caught = None
        try:
            observed_train(meta, torch, atomic_save, config, manifest, out)
        except RuntimeError as error:
            caught = str(error)
        assert meta.rollout is rollout and meta.stable_phi_clip_ is clip
        if kind == 'normal':
            assert caught is None and steps == [0, 1] and len(clip_calls) == 2
            for _ in range(2):
                for p in reference.parameters():
                    p.grad = torch.full_like(p, 2.)
                stable_phi_clip_(reference.parameters(), 1.)
                reference_optimizer.step()
            assert all(torch.equal(a, b) for a, b in zip(phi.parameters(), reference.parameters()))
            assert (out / 'before_episode_001.pt').exists()
            rows = [json.loads(line) for line in (out / 'raw_gradients.jsonl').read_text().splitlines()]
            assert all(row['update_authorized_by_guard'] for row in rows)
            raw = torch.load(out / 'gradient_episode_000.pt', weights_only=False)
            assert all(bool((g == 2.).all()) for g in raw['raw_gradient'].values())
        else:
            assert caught is not None and not steps and not clip_calls
            if kind == 'rollout_failure':
                assert not list(out.glob('gradient_episode_*.pt'))
            else:
                raw = torch.load(out / 'gradient_episode_000.pt', weights_only=False)
                assert 'clipped_gradient' not in raw
                row = json.loads((out / 'raw_gradients.jsonl').read_text())
                assert row['update_authorized_by_guard'] is False
                assert row['error']
                if kind == 'huge':
                    assert row['raw_norm_cpu_fp64'] > 1e25
                if kind == 'nonfinite':
                    assert row['raw_all_finite'] is False


def main():
    torch.set_num_threads(2)
    for kind in ['normal', 'huge', 'nonfinite', 'mutated_phi', 'rollout_failure']:
        exercise(kind)
    print(json.dumps({'status': 'PASS',
        'scope': 'CPU fixture validates capture and guard, not MUSA or H2 correctness',
        'checks': ['normal_Adam_updates_byte_equal_to_uninstrumented_reference',
                   'huge_gradient_saved_and_stopped_before_clip_and_update',
                   'nonfinite_gradient_saved_and_stopped_before_clip_and_update',
                   'phi_mutation_rejected_before_update', 'hooks_restored_on_rollout_failure']}))


if __name__ == '__main__':
    main()
