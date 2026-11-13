"""Inspect completed X tensors on CPU; never train or approve a run."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import torch


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    audit = json.loads(args.audit.read_text())
    assert audit['status'] == 'ARTIFACT_AUDIT_PASS_REQUIRES_SCIENTIFIC_REVIEW'
    assert audit['episodes_committed'] == 192 and audit['cumulative_inner_updates'] == 2560
    run = Path(audit['run'])
    config = audit['config']
    assert config['architecture'] == 'X' and not config.get('initial_phi')
    assert config.get('start_episode', 0) == 0
    assert not args.out.exists() and not args.out.resolve().is_relative_to(run.resolve())
    evidence = {str(args.audit): sha(args.audit), str(Path(__file__)): sha(__file__)}

    def load(path):
        path = Path(path)
        value = sha(path)
        if str(path) in audit['evidence_sha256']:
            assert audit['evidence_sha256'][str(path)] == value
        evidence[str(path)] = value
        return torch.load(path, map_location='cpu', weights_only=False)

    initial = load(run / 'initial_phi.pt')
    assert initial['config'] == config and initial['next_episode'] == 0
    assert initial['origin'] == 'fresh initialized phi; optimizer not yet stepped'
    first = load(run / 'gradient_episode_000.pt')['starting_phi']
    assert initial['phi'].keys() == first.keys()
    assert all(torch.equal(first[n], initial['phi'][n]) for n in first)
    assert sum(t.numel() for t in first.values()) == 83795
    heads = ['net.4.weight', 'net.4.bias', 'coord.2.weight', 'coord.2.bias']
    assert all(torch.count_nonzero(first[n]) == 0 for n in heads)
    rows = []
    gradient_names_with_nonzero = set()
    for episode in range(192):
        artifact = load(run / f'gradient_episode_{episode:03d}.pt')
        assert artifact['episode'] == episode
        raw = artifact['raw_gradient']
        assert raw.keys() == first.keys()
        assert all(torch.isfinite(t).all() for t in raw.values())
        values = {n: float(t.double().norm()) for n, t in raw.items()}
        gradient_names_with_nonzero.update(n for n, v in values.items() if v > 0)
        rows.append({'episode': episode, 'raw_norm_by_tensor': values,
                     'context_weight_gradient_norm': float(raw['net.4.weight'][2:].double().norm()),
                     'context_bias_gradient_norm': float(raw['net.4.bias'][2:].double().norm())})
    assert gradient_names_with_nonzero == set(first)
    snapshots = []
    for step in [512, 2048, 2560]:
        saved = load(run / f'phi_{step:04d}.pt')
        assert saved['config'] == config and saved['inner_updates'] == step
        phi = saved['phi']
        assert phi.keys() == first.keys() and all(torch.isfinite(t).all() for t in phi.values())
        assert all(torch.count_nonzero(phi[n]) > 0 for n in heads)
        snapshots.append({'step': step, 'next_episode': saved['next_episode'],
                          'norm_by_tensor': {n: float(t.double().norm()) for n, t in phi.items()},
                          'change_from_initial_by_tensor': {
                              n: float((t.double() - first[n].double()).norm()) for n, t in phi.items()}})
    final = load(run / 'latest.pt')
    endpoint = load(run / 'phi_2560.pt')
    assert final['next_episode'] == 192 and final['inner_updates'] == 2560
    assert all(torch.equal(final['phi'][n], endpoint['phi'][n]) for n in first)
    context_active = [r['episode'] for r in rows
                      if r['context_weight_gradient_norm'] > 0 and r['context_bias_gradient_norm'] > 0]
    assert context_active
    output = {'status': 'SAVED_X_CORE_TENSORS_INSPECTED_REQUIRES_SCIENTIFIC_REVIEW',
              'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'run': str(run), 'phi_parameters': 83795, 'episodes_checked': len(rows),
              'fresh_initial_phi_matches_first_rollout': True, 'initial_output_heads_zero': True,
              'every_parameter_tensor_has_nonzero_saved_gradient': True,
              'context_output_rows_have_nonzero_saved_gradients_in_episodes': context_active,
              'latest_phi_equal_2560_snapshot': True, 'snapshots': snapshots, 'gradient_rows': rows,
              'evidence_sha256': evidence, 'method_validated': False,
              'downstream_promotion_allowed': False,
              'limitations': ['Saved tensors do not recompute or validate MUSA derivatives.',
                              'Nonzero gradients and parameter motion do not establish efficacy or convergence.']}
    with args.out.open('x') as handle:
        handle.write(json.dumps(output, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in output.items()
                      if k not in ['evidence_sha256', 'snapshots', 'gradient_rows',
                                   'context_output_rows_have_nonzero_saved_gradients_in_episodes']}))


if __name__ == '__main__':
    main()
