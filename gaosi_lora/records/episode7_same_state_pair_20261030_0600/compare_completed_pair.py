"""CPU evidence review; no training, parameter updates or device work."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, 'tools')
import torch
from compare_prefix_gradients import compare_tensors

torch.set_num_threads(2)
out = Path('records/episode7_same_state_pair_20261030_0600/completed_pair_comparison.json')
assert not out.exists(), 'Preserve existing evidence'
result = {'status': 'EVIDENCE_REVIEW_NOT_METHOD_VALIDATION',
          'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
          'method_validated': False, 'production_modified_or_restarted': False,
          'core_budget_credit': 0, 'evidence_sha256': {}, 'groups': {}}


def read(path):
    path = Path(path)
    raw = path.read_bytes()
    result['evidence_sha256'][str(path)] = hashlib.sha256(raw).hexdigest()
    return raw


def tensor(path):
    read(path)
    return torch.load(path, map_location='cpu', weights_only=False)


reference = Path('records/prefix_gradient_results_20261030_0311/snapshot/runs/diagnostics/core_prefix_v7_raw_gradient_r1/repeat_0')
original = tensor(reference / 'gradient_episode_7.pt')
ref_events = [json.loads(line) for line in read(reference / 'events.jsonl').splitlines()]
ref_inner = [row for row in ref_events if row.get('event') == 'inner' and row.get('episode') == 7]
keys = ['k', 'ld', 'lr', 'task_tokens', 'language_tokens', 'support_forwards',
        'keep_tokens', 'keep_forwards', 'exact_window']
assert len(ref_inner) == 8
groups = {}
configs = {}
for group in ['async', 'blocking']:
    name = 'diagnose_L_episode7_same_state_' + group + '_r1'
    job = json.loads(read(Path('records/jobs') / (name + '.json')))
    assert job['status'] == 'PASS' and job['exit_code'] == 0
    run = Path('runs/diagnostics/L_episode7_same_state_' + group + '_r1')
    config = json.loads(read(run / 'config.json'))
    configs[group] = config
    assert config['episode'] == 7 and config['analytic_repeats'] == 4
    assert config['formal_data_used'] is False and config['epsilons'] == []
    initial = tensor(config['initial_phi'])
    assert result['evidence_sha256'][config['initial_phi']] == config['initial_phi_sha256']
    saved = tensor(run / 'analytic.pt')
    events = [json.loads(line) for line in read(run / 'events.jsonl').splitlines()]
    inner = [row for row in events if row.get('event') == 'analytic_inner']
    assert len(inner) == 32
    payloads = [tensor(run / ('repeat_' + str(i) + '.pt')) for i in range(4)]
    groups[group] = payloads
    review = {'job': job,
              'starting_phi_vs_reference': compare_tensors(torch, original['starting_phi'], saved['phi']),
              'starting_phi_vs_checkpoint': compare_tensors(torch, initial['phi'], saved['phi']),
              'repeats': []}
    for index, payload in enumerate(payloads):
        assert payload['repeat'] == index
        forward = inner[index * 8:(index + 1) * 8]
        assert [row['k'] for row in forward] == list(range(1, 9))
        review['repeats'].append({
            'repeat': index,
            'logged_forward_equal_to_reference': all(a.get(k) == b.get(k)
                for a, b in zip(ref_inner, forward) for k in keys),
            'raw_vs_reference': compare_tensors(torch, original['raw_gradient'], payload['gradient']),
            'raw_vs_repeat_0': compare_tensors(torch, payloads[0]['gradient'], payload['gradient']),
            'terminal': payload['terminal']})
    result['groups'][group] = review

invariants = ['episode', 'initial_phi', 'initial_phi_sha256', 'source_config_sha256',
              'source_code_sha256', 'analytic_repeats', 'epsilons', 'formal_data_used']
assert all(configs['async'][key] == configs['blocking'][key] for key in invariants)
result['cross_group'] = [{'async_repeat': i, 'blocking_repeat': j,
    'terminal_equal': a['terminal'] == b['terminal'],
    'raw_gradient': compare_tensors(torch, a['gradient'], b['gradient'])}
    for i, a in enumerate(groups['async']) for j, b in enumerate(groups['blocking'])]
result['limitations'] = [
    'Saved phi and recorded forward values do not prove equality of every internal gradient or tensor.',
    'Four identical blocking gradients support repeatability for this episode, not a backend fix.',
    'These finite replays do not validate historical giant gradients or the failed production checkpoint.']
with out.open('x') as handle:
    handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
print(json.dumps({'output': str(out), 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
    'blocking_all_bitwise_equal': all(row['raw_vs_repeat_0']['bitwise_equal']
        for row in result['groups']['blocking']['repeats']),
    'all_starting_phi_equal': all(g['starting_phi_vs_reference']['bitwise_equal']
        and g['starting_phi_vs_checkpoint']['bitwise_equal'] for g in result['groups'].values()),
    'all_logged_forwards_match': all(row['logged_forward_equal_to_reference']
        for g in result['groups'].values() for row in g['repeats']),
    'async_vs_blocking_relative_l2': [row['raw_gradient']['relative_l2_difference_to_left']
        for row in result['cross_group'] if row['blocking_repeat'] == 0]}))
