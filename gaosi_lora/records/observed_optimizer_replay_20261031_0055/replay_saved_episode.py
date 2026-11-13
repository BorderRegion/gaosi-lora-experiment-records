"""Replay one immutable, archived Adam update on CPU; never inspect live training."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import torch


torch.set_num_threads(2)
root = Path('/root/autodl-tmp/gaosi_lora')
observation = root / 'records/observed_blocking_core_monitor_20261031_003645_utc'
run = observation / 'snapshot/runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
paths = [run / 'before_episode_082.pt', run / 'gradient_episode_082.pt',
         run / 'before_episode_083.pt']
manifest = json.loads((observation / 'archive_source_sha256.json').read_text())
hashes = {}
for path in paths:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == manifest[str(path.relative_to(root))]
    hashes[str(path.relative_to(root))] = actual
before, gradient, after = [torch.load(p, map_location='cpu', weights_only=False) for p in paths]
assert before['next_episode'] == gradient['episode'] == 82
assert after['next_episode'] == 83
assert before['config'] == after['config']
names = list(before['phi'])
assert names == list(gradient['starting_phi']) == list(gradient['clipped_gradient'])
assert all(torch.equal(before['phi'][n], gradient['starting_phi'][n]) for n in names)
parameters = [torch.nn.Parameter(before['phi'][n].clone()) for n in names]
optimizer = torch.optim.Adam(parameters, lr=before['config']['meta_lr'], foreach=False)
optimizer.load_state_dict(copy.deepcopy(before['meta_optimizer']))
for name, parameter in zip(names, parameters):
    parameter.grad = gradient['clipped_gradient'][name].clone()
optimizer.step()


def compare(actual, expected):
    assert actual.shape == expected.shape and actual.dtype == expected.dtype
    delta = actual.double() - expected.double()
    denom = float(expected.double().norm())
    return {'byte_equal': torch.equal(actual, expected),
            'maximum_absolute_error': float(delta.abs().max()),
            'relative_l2_error': float(delta.norm()) / max(denom, 1e-300),
            'all_finite': bool(torch.isfinite(actual).all() and torch.isfinite(expected).all()),
            'allclose_rtol_2e-5_atol_1e-8': torch.allclose(actual, expected, rtol=2e-5, atol=1e-8)}


rows = []
actual_optimizer = optimizer.state_dict()
assert actual_optimizer['param_groups'] == after['meta_optimizer']['param_groups']
for name, parameter, index in zip(names, parameters, actual_optimizer['param_groups'][0]['params']):
    row = {'parameter': name, 'phi': compare(parameter.detach(), after['phi'][name]), 'optimizer': {}}
    state = actual_optimizer['state'][index]
    expected = after['meta_optimizer']['state'][index]
    assert state.keys() == expected.keys()
    for key in state:
        if torch.is_tensor(state[key]):
            row['optimizer'][key] = compare(state[key], expected[key])
        else:
            assert state[key] == expected[key]
    rows.append(row)
comparisons = [r['phi'] for r in rows] + [c for r in rows for c in r['optimizer'].values()]
result = {'status': 'CPU_SAVED_UPDATE_COMPARISON_REQUIRES_REVIEW',
          'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
          'episode': 82, 'checkpoint_next_episode': [82, 83],
          'input_sha256': hashes, 'saved_starting_phi_byte_equal': True,
          'optimizer_param_groups_equal': True, 'torch_version': torch.__version__,
          'all_finite': all(c['all_finite'] for c in comparisons),
          'all_byte_equal': all(c['byte_equal'] for c in comparisons),
          'allclose_rtol_2e-5_atol_1e-8': all(c['allclose_rtol_2e-5_atol_1e-8'] for c in comparisons),
          'parameters': rows, 'production_modified_or_restarted': False,
          'method_validated': False, 'downstream_promotion_allowed': False,
          'limitations': ['Single archived episode only, CPU Adam compared against saved MUSA update.',
                         'This does not recompute gradients or establish H2 derivative correctness.',
                         'Roundoff metrics and the stated allclose tolerance do not authorize promotion.']}
print(json.dumps(result, indent=2, allow_nan=False))
