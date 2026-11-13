"""Compare preserved diagnostic prefixes on CPU without promoting either phi.

Requires completed, pinned prefix diagnostics. Later episodes may have different
starting phi; report that explicitly before interpreting their gradient difference.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_prefix(root):
    config = json.loads((root / 'diagnostic_config.json').read_text())
    result = json.loads((root / 'diagnostic_result.json').read_text())
    if (result['status'] != 'EXECUTED_REQUIRES_SCIENTIFIC_REVIEW'
            or config['formal_data_used'] is not False
            or config['training_repeats'] != 1
            or result.get('method_validated') is not False):
        raise ValueError('Require a completed independent development prefix')
    source = root / 'production_source_config.json'
    if sha256(source) != config['source_config_sha256']:
        raise ValueError('Preserved production configuration hash mismatch')
    run = root / 'repeat_0'
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    ends = [event for event in events if event.get('event') == 'episode_end']
    expected = list(range(config['stop_after_episode'] + 1))
    if [event['episode'] for event in ends] != expected:
        raise ValueError('Incomplete or discontinuous episode evidence')
    observations = [json.loads(line) for line in
                    (run / 'gradient_observations.jsonl').read_text().splitlines()]
    if [row['episode'] for row in observations] != expected:
        raise ValueError('Missing raw-gradient observations')
    inner = [[event for event in events if event.get('event') == 'inner'
              and event.get('episode') == episode] for episode in expected]
    source_config = json.loads(source.read_text())
    for episode, rows in enumerate(inner):
        if [row['k'] for row in rows] != list(range(1, source_config['episode_K'][episode] + 1)):
            raise ValueError('Inner-step evidence is incomplete')
    return config, observations, inner


def compare_tensors(torch, left, right):
    if set(left) != set(right):
        raise ValueError('Tensor names differ')
    if any((left[key] is None) != (right[key] is None) for key in left):
        raise ValueError('Gradient presence differs')
    keys = sorted(key for key in left if left[key] is not None)
    if not keys:
        raise ValueError('No tensors available')
    if any(left[key].shape != right[key].shape for key in keys):
        raise ValueError('Tensor shapes differ')
    a = torch.cat([left[key].detach().reshape(-1).double() for key in keys])
    b = torch.cat([right[key].detach().reshape(-1).double() for key in keys])
    finite_a, finite_b = bool(a.isfinite().all()), bool(b.isfinite().all())
    row = {'elements': a.numel(), 'left_finite': finite_a, 'right_finite': finite_b,
           'left_nonfinite_elements': int((~a.isfinite()).sum()),
           'right_nonfinite_elements': int((~b.isfinite()).sum())}
    if finite_a and finite_b:
        na, nb, diff = a.norm(), b.norm(), (a - b).norm()
        bitwise_equal = all(left[key].dtype == right[key].dtype and torch.equal(
            left[key].contiguous().view(torch.uint8),
            right[key].contiguous().view(torch.uint8)) for key in keys)
        row.update(bitwise_equal=bitwise_equal, left_norm=float(na),
                   right_norm=float(nb), max_absolute_difference=float((a - b).abs().max()),
                   relative_l2_difference_to_left=float(diff / na) if na else None,
                   cosine=float(torch.dot(a, b) / (na * nb)) if na and nb else None)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--left', type=Path, required=True)
    parser.add_argument('--right', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('Preserve existing comparison evidence')
    for root in [args.left.resolve(), args.right.resolve()]:
        if args.out.resolve().is_relative_to(root):
            raise ValueError('Keep comparison output outside preserved run directories')
    left, right = read_prefix(args.left), read_prefix(args.right)
    invariants = ['source_config_sha256', 'source_code_sha256', 'stop_after_episode',
                  'training_repeats', 'formal_data_used', 'lock_path']
    if any(left[0][key] != right[0][key] for key in invariants):
        raise ValueError('Prefix inputs or numerical sources differ')
    import torch
    torch.set_num_threads(2)
    result = {'status': 'COMPARISON_COMPLETE_REQUIRES_SCIENTIFIC_REVIEW',
              'method_validated': False, 'production_modified': False, 'core_budget_credit': 0,
              'left': str(args.left), 'right': str(args.right), 'episodes': [],
              'limitations': ['Different starting phi invalidate same-state interpretation.',
                              'A finite gradient or an execution PASS does not establish H2 correctness.']}
    event_keys = ['k', 'ld', 'lr', 'task_tokens', 'language_tokens', 'support_forwards',
                  'keep_tokens', 'keep_forwards', 'exact_window']
    evidence = {}
    for episode, (left_obs, right_obs) in enumerate(zip(left[1], right[1])):
        artifacts = []
        for root in [args.left, args.right]:
            path = root / 'repeat_0' / f'gradient_episode_{episode}.pt'
            evidence[str(path)] = sha256(path)
            payload = torch.load(path, map_location='cpu', weights_only=False)
            if (payload['episode'] != episode or payload['source_config_sha256']
                    != left[0]['source_config_sha256']):
                raise ValueError('Wrong gradient episode')
            artifacts.append(payload)
        a, b = artifacts
        row = {'episode': episode,
               'starting_phi': compare_tensors(torch, a['starting_phi'], b['starting_phi']),
               'raw_gradient': compare_tensors(torch, a['raw_gradient'], b['raw_gradient']),
               'forward_records_equal': all(all(x.get(key) == y.get(key) for key in event_keys)
                                            for x, y in zip(left[2][episode], right[2][episode])),
               'left_observation': left_obs, 'right_observation': right_obs}
        row['same_starting_phi_and_logged_forward_values'] = bool(
            row['starting_phi'].get('bitwise_equal') and row['forward_records_equal'])
        result['episodes'].append(row)
    result['raw_evidence_sha256'] = evidence
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'episodes': len(result['episodes']),
                      'output': str(args.out), 'method_validated': False}))


if __name__ == '__main__':
    main()
