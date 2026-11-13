"""Compare archived X warm512 tensors on CPU; never approve or touch training."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import torch


def equal(left, right):
    if torch.is_tensor(left):
        return (torch.is_tensor(right) and left.shape == right.shape
                and left.dtype == right.dtype and torch.equal(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equal(left[k], right[k]) for k in left)
    if isinstance(left, (list, tuple)):
        return type(left) is type(right) and len(left) == len(right) and all(
            equal(a, b) for a, b in zip(left, right))
    return left == right


def finite(value):
    if torch.is_tensor(value):
        return bool(torch.isfinite(value).all())
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(v) for v in value)
    return True


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', required=True, type=Path)
parser.add_argument('--warm-sha', required=True)
parser.add_argument('--latest-sha', required=True)
args = parser.parse_args()
torch.set_num_threads(2)
checkpoints = {}
for name, expected in [('phi_0512.pt', args.warm_sha), ('latest.pt', args.latest_sha)]:
    path = args.run / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    checkpoints[name] = torch.load(path, map_location='cpu', weights_only=False)
warm, latest = checkpoints['phi_0512.pt'], checkpoints['latest.pt']
config = json.loads((args.run / 'config.json').read_text())
assert config['architecture'] == 'X' and config['episode_K'] == [8] * 64 + [16] * 128
assert warm['next_episode'] == latest['next_episode'] == 64
assert warm['inner_updates'] == latest['inner_updates'] == 512
assert warm['config'] == latest['config'] == config
assert equal(warm['phi'], latest['phi'])
assert equal(warm['meta_optimizer'], latest['meta_optimizer'])
assert finite(warm['phi']) and finite(warm['meta_optimizer'])
assert sum(t.numel() for t in warm['phi'].values()) == 83795
steps = sorted({float(s['step']) for s in warm['meta_optimizer']['state'].values()})
assert steps == [64.0]
print(json.dumps({'status': 'ARCHIVED_X_WARM512_TENSORS_VERIFIED',
                  'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'run': str(args.run), 'warm_sha256': args.warm_sha,
                  'latest_sha256': args.latest_sha, 'architecture': 'X',
                  'phi_parameter_count': 83795, 'next_episode': 64, 'inner_updates': 512,
                  'phi_tensors_equal': True, 'optimizer_tensors_equal': True,
                  'phi_and_optimizer_finite': True, 'optimizer_steps': steps,
                  'config_equal': True, 'original_schedule_equal': True,
                  'method_validated': False, 'downstream_promotion_allowed': False,
                  'training_modified_or_restarted': False,
                  'limitations': ['CPU reads of archived tensors only; gradients are not recomputed.',
                                  'Finiteness and tensor equality do not validate H2 derivatives.']}))
