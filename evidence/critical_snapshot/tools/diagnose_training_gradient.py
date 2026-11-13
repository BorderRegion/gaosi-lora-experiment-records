"""Observe raw gradients around the unchanged real training clip operation.

Use the original readiness train entry (including Adam and parent loading),
and preserve pre-clip tensors even when the production nonfinite guard exits.
The diagnostic checkpoint is never promoted to a production phi.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch

from gaosi.meta import train
from gaosi.runtime import atomic_save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    source_path = Path(config['source_config'])
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != config['source_config_sha256']:
        raise ValueError('Source configuration changed')
    source = json.loads(source_path.read_text())
    if (source['device'] != 'musa' or source['selection_data'] != 'development_only'
            or source['episode_K'] != [2, 2] or source['architecture'] != 'L'
            or source['organization'] != 'O1'
            or any('final' in file for file in source['parent_data'])):
        raise ValueError('Only the original development L readiness may be observed')
    import torch_musa
    torch.set_num_threads(8)
    observed = dict(source, out=config['out'],
                    purpose='Instrumented original readiness; raw gradient diagnosis only')
    clip = torch.nn.utils.clip_grad_norm_
    calls = 0
    active_out = config['out']

    def observed_clip(parameters, *values, **kwargs):
        nonlocal calls
        parameters = list(parameters)
        # Queue copies without a host/device barrier before the original clip.
        raw = [p.grad.detach().clone() if p.grad is not None else None for p in parameters]
        starting_phi = [p.detach().clone() for p in parameters]
        returned = clip(parameters, *values, **kwargs)
        cpu = [g.cpu() if g is not None else None for g in raw]
        clipped = [p.grad.detach().cpu() if p.grad is not None else None for p in parameters]
        atomic_save({'raw_gradient': cpu, 'clipped_gradient': clipped,
                     'starting_phi': [p.cpu() for p in starting_phi],
                     'clip_return': returned.detach().cpu(), 'source_config': source,
                     'diagnostic_config': config}, Path(active_out) / f'gradient_episode{calls}.pt')
        finite = all(bool(torch.isfinite(g).all()) for g in cpu if g is not None)
        norm = (math.sqrt(sum(float(g.double().square().sum()) for g in cpu if g is not None))
                if finite else None)
        after_finite = all(bool(torch.isfinite(g).all()) for g in clipped if g is not None)
        after_norm = (math.sqrt(sum(float(g.double().square().sum()) for g in clipped if g is not None))
                      if after_finite else None)
        returned_cpu = float(returned.cpu())
        row = {'event': 'observed_training_clip', 'episode': calls,
               'all_raw_gradients_finite': finite, 'raw_norm_cpu_fp64': norm,
               'original_clip_return': returned_cpu if math.isfinite(returned_cpu) else None,
               'original_clip_return_finite': math.isfinite(returned_cpu),
               'clipped_norm_cpu_fp64': after_norm, 'all_clipped_gradients_finite': after_finite}
        with Path(active_out, 'gradient_observations.jsonl').open('a') as handle:
            handle.write(json.dumps(row, allow_nan=False) + '\n')
        print(json.dumps(row, allow_nan=False), flush=True)
        calls += 1
        return returned

    torch.nn.utils.clip_grad_norm_ = observed_clip
    if config.get('hvp_trace', False):
        from hvp_trace import install
        install(config['out'])
    if config.get('hvp_sync_only', False):
        if config.get('hvp_trace', False):
            raise ValueError('Choose either trace or synchronization only')
        from hvp_trace import install_sync_only
        install_sync_only()
    try:
        repeats = config.get('training_repeats', 1)
        if repeats not in [1, 2, 3]:
            raise ValueError('Only 1–3 diagnostic readiness repeats are allowed')
        for repeat in range(repeats):
            calls = 0
            active_out = config['out'] if repeats == 1 else str(Path(config['out']) / f'repeat_{repeat}')
            observed['out'] = active_out
            train(observed)
    finally:
        torch.nn.utils.clip_grad_norm_ = clip


if __name__ == '__main__':
    main()
