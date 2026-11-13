"""Validate only stable outer clipping, including captured real MUSA gradients."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from gaosi.gradients import stable_phi_clip_


def parameters(values, device):
    result = []
    for value in values:
        if value is not None:
            p = torch.nn.Parameter(torch.zeros_like(value, device=device))
            p.grad = value.to(device).clone()
            result.append(p)
    return result


def norm(values):
    return sum(float(v.detach().cpu().double().square().sum()) for v in values) ** .5


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', choices=['cpu', 'musa'], required=True)
    parser.add_argument('--captured')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.device == 'musa':
        import torch_musa
    tests = {}
    for scale in [1., 1e30, torch.finfo(torch.float32).max / 4]:
        values = [torch.tensor([3., 4.]) * scale, torch.tensor([0., 0.])]
        ps = parameters(values, args.device)
        before = stable_phi_clip_(ps)
        torch.testing.assert_close(ps[0].grad.cpu(), torch.tensor([.6, .8]), atol=3e-7, rtol=3e-7)
        assert abs(float(before) / (5 * scale) - 1) < 1e-6
        tests[f'known_3_4_direction_scale_{scale}'] = 'PASS'
    ps = parameters([torch.tensor([.1, -.2])], args.device)
    original = ps[0].grad.clone()
    stable_phi_clip_(ps)
    assert torch.equal(ps[0].grad, original)
    tests['below_threshold_unchanged'] = 'PASS'
    ps = parameters([torch.tensor([float('inf'), 1.])], args.device)
    try:
        stable_phi_clip_(ps)
    except RuntimeError:
        assert torch.isinf(ps[0].grad[0]) and ps[0].grad[1] == 1
    else:
        raise AssertionError('A nonfinite gradient was accepted')
    tests['nonfinite_rejected_before_mutation'] = 'PASS'
    observed = None
    if args.captured:
        raw = torch.load(args.captured, map_location='cpu', weights_only=False)['raw_gradient']
        ps = parameters(raw, args.device)
        returned = float(stable_phi_clip_(ps))
        after = norm([p.grad for p in ps])
        assert returned > 1e30 and abs(after - 1) < 2e-6
        expected = [g.double() / (norm([v for v in raw if v is not None]) + 1e-6)
                    for g in raw if g is not None]
        for p, value in zip(ps, expected):
            torch.testing.assert_close(p.grad.cpu(), value.float(), atol=2e-8, rtol=2e-6)
        observed = {'raw_norm': returned, 'clipped_norm': after}
        tests['captured_real_gradient_direction_and_unit_norm'] = 'PASS'
    result = {'status': 'PASS', 'device': args.device, 'tests': tests, 'captured': observed,
              'scope': 'Outer clipping arithmetic only; does not validate oversized H2 gradients'}
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
