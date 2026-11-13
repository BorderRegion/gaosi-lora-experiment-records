"""CPU checks of bounded config validation, lock ownership and v7 capture."""
import argparse
import copy
import fcntl
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import diagnose_prefix_gradient as diagnosis


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--cpu-torch', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    checks = []
    diagnosis.validate(config)
    checks.append('valid_bounded_prefix')

    def rejected(name, change):
        candidate = copy.deepcopy(config)
        change(candidate)
        try:
            diagnosis.validate(candidate)
        except (ValueError, RuntimeError):
            checks.append(name)
        else:
            raise AssertionError('Invalid config accepted: ' + name)

    rejected('reject_episode_8', lambda c: c.update(stop_after_episode=8))
    rejected('reject_negative_episode', lambda c: c.update(stop_after_episode=-1))
    rejected('reject_unbounded_repeats', lambda c: c.update(training_repeats=3))
    rejected('reject_formal_data', lambda c: c.update(formal_data_used=True))
    rejected('reject_production_output', lambda c: c.update(out='runs/phi_mix_O1_L_H2_core_s17_recompute_v7'))
    rejected('reject_changed_source_config', lambda c: c.update(source_config_sha256='0' * 64))
    rejected('reject_incomplete_source_pin', lambda c: c['source_code_sha256'].pop('gaosi/meta.py'))
    rejected('reject_changed_source_code', lambda c: c['source_code_sha256'].update({'gaosi/meta.py': '0' * 64}))
    rejected('reject_different_device_lock', lambda c: c.update(lock_path='/tmp/unrelated.lock'))
    with tempfile.TemporaryDirectory() as temp:
        lock = Path(temp) / 'cpu_test_only.lock'
        with lock.open('a') as handle:
            try:
                diagnosis.require_inherited_lock(lock)
            except RuntimeError:
                checks.append('reject_unheld_lock')
            else:
                raise AssertionError('Unheld lock accepted')
            fcntl.flock(handle, fcntl.LOCK_EX)
            diagnosis.require_inherited_lock(lock)
            checks.append('accept_own_held_descriptor')

    if args.cpu_torch:
        import torch
        from gaosi.gradients import stable_phi_clip_
        from gaosi.runtime import atomic_save

        def exercise(nonfinite=False, rollout_failure=False):
            with tempfile.TemporaryDirectory() as temp:
                out = Path(temp)
                source = {'episode_K': [8, 8], 'out': str(out)}
                phi = torch.nn.Linear(2, 1)
                calls = []

                def rollout(*values, **kwargs):
                    if rollout_failure:
                        raise RuntimeError('test rollout failure')
                    return None

                original_rollout = rollout

                def train(current):
                    for episode in range(2):
                        meta.rollout(None, None, None, phi)
                        for p in phi.parameters():
                            p.grad = torch.full_like(p, float('nan') if nonfinite else 1e25)
                        returned = meta.stable_phi_clip_(phi.parameters(), 1.)
                        calls.append(float(returned))
                        atomic_save({'next_episode': episode + 1, 'config': current}, out / 'latest.pt')

                meta = SimpleNamespace(stable_phi_clip_=stable_phi_clip_, rollout=rollout, train=train)
                caught = False
                try:
                    diagnosis.instrumented_train(meta, torch, atomic_save, source, config, out)
                except RuntimeError:
                    caught = True
                    if not (nonfinite or rollout_failure):
                        raise
                assert caught == bool(nonfinite or rollout_failure)
                assert meta.stable_phi_clip_ is stable_phi_clip_
                assert meta.rollout is original_rollout
                if rollout_failure:
                    assert not list(out.glob('gradient_episode_*.pt'))
                    return
                raw = torch.load(out / 'gradient_episode_0.pt', weights_only=False)
                observations = [json.loads(line) for line in (out / 'gradient_observations.jsonl').read_text().splitlines()]
                if nonfinite:
                    assert 'clipped_gradient' not in raw
                    assert observations[0]['all_raw_gradients_finite'] is False
                    assert 'clip_error' in observations[0]
                    assert not calls
                    assert all(bool(torch.isnan(p.grad).all()) for p in phi.parameters())
                    return
                assert len(calls) == len(observations) == 2
                assert observations[0]['raw_norm_cpu_fp64'] > 1e25
                assert abs(observations[0]['clipped_norm_cpu_fp64'] - 1.) < 1e-6
                assert all(bool((g == 1e25).all()) for g in raw['raw_gradient'].values())
                assert all(float(g.abs().max()) < 1. for g in raw['clipped_gradient'].values())
                saved = torch.load(out / 'before_episode_1.pt', weights_only=False)
                assert saved['next_episode'] == 1 and saved['config'] == source

        exercise()
        checks.append('capture_actual_stable_clip_and_preserve_huge_raw_gradients')
        exercise(nonfinite=True)
        checks.append('nonfinite_rejection_preserves_raw_evidence_without_update')
        exercise(rollout_failure=True)
        checks.append('restore_hooks_after_rollout_failure')
    print(json.dumps({'status': 'PASS', 'checks': checks, 'check_count': len(checks),
                      'torch_checks': args.cpu_torch, 'musa_imported': 'torch_musa' in sys.modules,
                      'production_training_started_or_modified': False}))


if __name__ == '__main__':
    main()
