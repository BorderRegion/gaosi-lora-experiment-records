"""Inspect completed common-warm H1 tensor continuity on CPU; no training or approval."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import torch


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equal(a, b):
    if torch.is_tensor(a):
        return torch.is_tensor(b) and a.dtype == b.dtype and a.shape == b.shape and torch.equal(a, b)
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def finite(a):
    if torch.is_tensor(a):
        return bool(torch.isfinite(a).all())
    if isinstance(a, dict):
        return all(finite(v) for v in a.values())
    if isinstance(a, (list, tuple)):
        return all(finite(v) for v in a)
    return True


def steps(checkpoint):
    return {float(s['step']) for s in checkpoint['meta_optimizer']['state'].values()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    audit = json.loads(args.audit.read_text())
    assert audit['status'] == 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
    assert audit['episodes_committed'] == 128 and audit['start_episode'] == 64
    assert audit['executed_inner_updates'] == 2048 and audit['cumulative_inner_updates'] == 2560
    config = audit['config']
    assert config['architecture'] == 'L' and config['horizon'] == 'H1'
    assert config['start_episode'] == 64 and config['load_initial_meta_optimizer'] is True
    assert config['episode_K'] == [8] * 64 + [16] * 128
    run = Path(audit['run'])
    assert not args.out.exists() and not args.out.resolve().is_relative_to(run.resolve())
    evidence = {str(args.audit): sha(args.audit), str(Path(__file__)): sha(__file__)}

    def load(path):
        path = Path(path)
        value = sha(path)
        assert audit['evidence_sha256'].get(str(path)) == value, str(path)
        evidence[str(path)] = value
        return torch.load(path, map_location='cpu', weights_only=False)

    warm_path = Path(config['initial_phi'])
    preserved_path = run / 'before_episode_064.pt'
    expected = '4998e1ef0596fd40f797bb208ff1ebc7374b422916dc4d7cfc8981f577b83b65'
    assert sha(warm_path) == sha(preserved_path) == expected
    warm, preserved = load(warm_path), load(preserved_path)
    assert equal(warm, preserved) and finite(warm['phi']) and finite(warm['meta_optimizer'])
    assert warm['next_episode'] == 64 and warm['inner_updates'] == 512
    assert steps(warm) == {64.}
    first = load(run / 'gradient_episode_064.pt')['starting_phi']
    assert equal(first, warm['phi']) and sum(t.numel() for t in first.values()) == 78850
    names = set(first)
    active = set()
    rows = []
    for episode in range(64, 192):
        item = load(run / f'gradient_episode_{episode:03d}.pt')
        raw = item['raw_gradient']
        assert item['episode'] == episode and set(raw) == names and finite(raw)
        norms = {n: float(t.double().norm()) for n, t in raw.items()}
        active.update(n for n, norm in norms.items() if norm > 0)
        rows.append({'episode': episode, 'raw_norm_by_tensor': norms})
    assert active == names
    snapshots = []
    for total, episode in [(2048, 160), (2560, 192)]:
        saved = load(run / f'phi_{total:04d}.pt')
        assert saved['config'] == config and saved['inner_updates'] == total
        assert saved['next_episode'] == episode and steps(saved) == {float(episode)}
        assert finite(saved['phi']) and finite(saved['meta_optimizer'])
        change = {n: float((t.double() - first[n].double()).norm()) for n, t in saved['phi'].items()}
        assert all(v > 0 for v in change.values())
        snapshots.append({'inner_updates': total, 'next_episode': episode,
                          'optimizer_step': episode, 'change_from_common_warm_by_tensor': change})
    final, endpoint = load(run / 'latest.pt'), load(run / 'phi_2560.pt')
    assert final['next_episode'] == 192 and final['inner_updates'] == 2560
    assert final['config'] == config and finite(final['phi']) and finite(final['meta_optimizer'])
    assert equal(final['phi'], endpoint['phi']) and equal(final['meta_optimizer'], endpoint['meta_optimizer'])
    assert steps(final) == {192.}
    output = {
        'status': 'SAVED_COMMONWARM_H1_TENSORS_INSPECTED_REQUIRES_SCIENTIFIC_REVIEW',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'run': str(run), 'phi_parameters': 78850, 'episodes_checked': 128,
        'warm_checkpoint_full_bytes_sha256': expected, 'warm_phi_and_optimizer_preserved_exactly': True,
        'first_rollout_phi_equal_common_warm': True, 'initial_optimizer_step': 64,
        'final_optimizer_step': 192, 'every_parameter_tensor_has_nonzero_saved_gradient': True,
        'latest_phi_and_optimizer_equal_2560_snapshot': True,
        'snapshots': snapshots, 'gradient_rows': rows, 'evidence_sha256': evidence,
        'method_validated': False, 'downstream_promotion_allowed': False,
        'limitations': ['Saved tensors do not recompute or validate MUSA derivatives.',
                        'Nonzero gradients and parameter motion do not establish efficacy or convergence.']}
    with args.out.open('x') as handle:
        json.dump(output, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps({k: v for k, v in output.items() if k not in ['gradient_rows', 'evidence_sha256', 'snapshots']}))


if __name__ == '__main__':
    main()
