"""Repeat one real development episode with identical inputs in one process.

This observes backend repeatability without changing the production rollout,
updating phi, clipping gradients, or reading formal data.
"""
import argparse
import copy
import json
from pathlib import Path

import torch

import diagnose_meta_gradient as diagnosis
from gaosi.runtime import atomic_save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    repeats = config['analytic_repeats']
    if repeats < 2 or repeats > 4 or config['epsilons']:
        raise ValueError('Use 2–4 identical analytic repeats without differences')
    original_rollout = diagnosis.rollout

    def repeated(*values, **kwargs):
        phi, streams = values[3], values[4]
        cursors = {k: copy.deepcopy(v.state_dict()) for k, v in streams.items()}
        rng = diagnosis.torch.get_rng_state()
        device_rng = diagnosis.torch.musa.get_rng_state_all()
        first = None
        summaries = []
        for index in range(repeats):
            torch.set_rng_state(rng)
            torch.musa.set_rng_state_all(device_rng)
            for key, stream in streams.items():
                stream.load_state_dict(copy.deepcopy(cursors[key]))
            phi.zero_grad(set_to_none=True)
            result = original_rollout(*values, **kwargs)
            gradients = {k: p.grad.detach().cpu().clone()
                         for k, p in phi.named_parameters() if p.grad is not None}
            atomic_save({'gradient': gradients, 'terminal': result[2], 'repeat': index},
                        Path(config['out']) / f'repeat_{index}.pt')
            flat = torch.cat([g.flatten().double() for g in gradients.values()])
            finite = bool(torch.isfinite(flat).all())
            summary = {'repeat': index, 'all_finite': finite,
                       'nonfinite_elements': int((~torch.isfinite(flat)).sum()),
                       'norm': float(flat.norm()) if finite else None,
                       'terminal': result[2]}
            if first is None:
                first = flat.clone()
            elif finite and bool(torch.isfinite(first).all()):
                summary.update(bitwise_equal=bool(torch.equal(flat, first)),
                               max_absolute_difference=float((flat - first).abs().max()),
                               relative_l2_difference=float((flat - first).norm()
                                                           / first.norm().clamp_min(1e-30)))
            summaries.append(summary)
            Path(config['out'], 'repeat_summary.json').write_text(json.dumps(summaries, indent=2))
            print(json.dumps({'event': 'analytic_repeat', **summary}, allow_nan=False), flush=True)
        return result

    diagnosis.rollout = repeated
    diagnosis.main()


if __name__ == '__main__':
    main()
