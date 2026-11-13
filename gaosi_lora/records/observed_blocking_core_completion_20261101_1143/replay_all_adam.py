"""Read-only CPU replay of each saved Adam update in the completed L run."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import torch

torch.set_num_threads(2)
ROOT = Path('/root/autodl-tmp/gaosi_lora')
RUN = ROOT / 'runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
AUDIT = ROOT / 'records/observed_blocking_core_final_audit_20261101_1139.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return torch.load(path, map_location='cpu', weights_only=False)


def compare(left, right):
    assert left.shape == right.shape and left.dtype == right.dtype
    error = left.detach().double() - right.double()
    return {'equal': torch.equal(left, right),
            'finite': bool(torch.isfinite(left).all() and torch.isfinite(right).all()),
            'max_absolute_error': float(error.abs().max()),
            'relative_l2_error': float(error.norm()) / max(float(right.double().norm()), 1e-300),
            'allclose_rtol_2em5_atol_1em8': torch.allclose(left, right, rtol=2e-5, atol=1e-8)}


audit = json.loads(AUDIT.read_text())
assert audit['status'] == 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
assert audit['episodes_committed'] == 192 and audit['executed_inner_updates'] == 2560
for name, expected in audit['evidence_sha256'].items():
    assert digest(ROOT / name) == expected, name
config = json.loads((RUN / 'config.json').read_text())
assert config['episode_K'] == [8] * 64 + [16] * 128 and not config.get('initial_phi')
rows = []
for episode in range(192):
    gradient = load(RUN / f'gradient_episode_{episode:03d}.pt')
    after = load(RUN / f'before_episode_{episode + 1:03d}.pt' if episode < 191 else RUN / 'latest.pt')
    assert gradient['episode'] == episode and after['next_episode'] == episode + 1
    assert after['inner_updates'] == sum(config['episode_K'][:episode + 1])
    assert after['config'] == config
    names = list(gradient['starting_phi'])
    assert names == list(gradient['clipped_gradient']) == list(after['phi'])
    parameters = [torch.nn.Parameter(gradient['starting_phi'][n].clone()) for n in names]
    optimizer = torch.optim.Adam(parameters, lr=config['meta_lr'], foreach=False)
    if episode:
        before = load(RUN / f'before_episode_{episode:03d}.pt')
        assert before['next_episode'] == episode and before['config'] == config
        assert all(torch.equal(before['phi'][n], gradient['starting_phi'][n]) for n in names)
        assert all(float(s['step']) == episode for s in before['meta_optimizer']['state'].values())
        optimizer.load_state_dict(copy.deepcopy(before['meta_optimizer']))
    else:
        assert not optimizer.state_dict()['state']
    for name, parameter in zip(names, parameters):
        parameter.grad = gradient['clipped_gradient'][name].clone()
    optimizer.step()
    actual = optimizer.state_dict()
    assert actual['param_groups'] == after['meta_optimizer']['param_groups']
    phi = [compare(p, after['phi'][n]) for n, p in zip(names, parameters)]
    states = {}
    for index in actual['param_groups'][0]['params']:
        left, right = actual['state'][index], after['meta_optimizer']['state'][index]
        assert left.keys() == right.keys()
        for key in left:
            assert torch.is_tensor(left[key]) and float(right['step']) == episode + 1
            states.setdefault(key, []).append(compare(left[key], right[key]))
    comparisons = phi + [c for group in states.values() for c in group]
    rows.append({'episode': episode, 'starting_phi_equal': True,
                 'phi_max_absolute_error': max(c['max_absolute_error'] for c in phi),
                 'phi_max_relative_l2_error': max(c['relative_l2_error'] for c in phi),
                 'phi_all_equal': all(c['equal'] for c in phi),
                 'optimizer': {key: {'all_equal': all(c['equal'] for c in group),
                                     'max_absolute_error': max(c['max_absolute_error'] for c in group),
                                     'max_relative_l2_error': max(c['relative_l2_error'] for c in group)}
                               for key, group in states.items()},
                 'all_finite': all(c['finite'] for c in comparisons),
                 'allclose_rtol_2em5_atol_1em8': all(c['allclose_rtol_2em5_atol_1em8'] for c in comparisons)})
result = {
    'status': 'CPU_ALL_SAVED_ADAM_UPDATES_REPLAYED_REQUIRES_REVIEW',
    'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
    'source_sha256': digest(Path(__file__)), 'audit': str(AUDIT.relative_to(ROOT)),
    'audit_sha256': digest(AUDIT), 'audited_evidence_independently_rehashed': len(audit['evidence_sha256']),
    'episodes_checked': len(rows), 'torch_version': torch.__version__,
    'all_starting_phi_equal': True, 'all_optimizer_param_groups_equal': True,
    'all_finite': all(r['all_finite'] for r in rows),
    'allclose_rtol_2em5_atol_1em8': all(r['allclose_rtol_2em5_atol_1em8'] for r in rows),
    'maximum_phi_absolute_error': max(r['phi_max_absolute_error'] for r in rows),
    'maximum_phi_relative_l2_error': max(r['phi_max_relative_l2_error'] for r in rows),
    'optimizer_states_all_equal': all(v['all_equal'] for r in rows for v in r['optimizer'].values()),
    'rows': rows, 'method_validated': False, 'downstream_promotion_allowed': False,
    'limitations': ['Each update starts from its own saved MUSA state; this is not an accumulated CPU trajectory.',
                    'CPU/MUSA floating point differences are measured; allclose is not byte equality.',
                    'Saved gradients are consumed, not recomputed; this does not validate H2 derivatives.']}
out = Path(__file__).with_name('adam_replay.json')
with out.open('x') as handle:
    handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
print(json.dumps({k: result[k] for k in result if k != 'rows'}))
