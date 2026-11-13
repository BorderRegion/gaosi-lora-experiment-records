"""Inspect immutable scheduled checkpoints on CPU; no training or approval."""
import datetime as dt
import hashlib
import json
from pathlib import Path

import torch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    torch.set_num_threads(2)
    root = Path.cwd()
    record = Path(__file__).resolve().parent
    output = record / 'optimizer_clock_review.json'
    assert not output.exists()
    warm_folder = Path('records/observed_blocking_core_monitor_20261101_070654_utc')
    folder = Path('records/pipeline_monitor_20261103_172107_utc')
    warm_run = warm_folder / 'snapshot/runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8'
    run = folder / 'snapshot/runs/phi_mix_O1_L_H1_commonwarm_s17_observed_blocking_v8'
    evidence = {str(Path(__file__).resolve().relative_to(root)): sha(Path(__file__))}
    rows = []
    for path, archive, episode, updates in [
        (warm_run / 'phi_0512.pt', warm_folder, 64, 512),
        (run / 'before_episode_066.pt', folder, 66, 544),
        (run / 'latest.pt', folder, 67, 560),
    ]:
        manifest_file = archive / 'archive_source_sha256.json'
        manifest = json.loads(manifest_file.read_text())
        value = sha(path)
        assert manifest[str(path)] == value
        evidence[str(manifest_file)] = sha(manifest_file)
        evidence[str(path)] = value
        saved = torch.load(path, map_location='cpu', weights_only=False)
        assert saved['next_episode'] == episode and saved['inner_updates'] == updates
        assert saved['config']['architecture'] == 'L'
        if episode > 64:
            assert saved['config']['horizon'] == 'H1'
            assert saved['config']['load_initial_meta_optimizer'] is True
        optimizer = saved['meta_optimizer']
        assert optimizer['state'] and all(torch.isfinite(t).all() for t in saved['phi'].values())
        steps = {float(s['step']) for s in optimizer['state'].values()}
        assert steps == {float(episode)}
        assert len(optimizer['state']) == len(saved['phi'])
        assert all(torch.isfinite(t).all() for s in optimizer['state'].values()
                   for t in s.values() if torch.is_tensor(t))
        rows.append({'checkpoint': str(path), 'next_episode': episode,
                     'inner_updates': updates, 'optimizer_step': episode,
                     'optimizer_state_count': len(optimizer['state']), 'all_finite': True})
    result = {
        'status': 'ARCHIVED_H1_WARM_AND_FIRST_COMMITTED_OPTIMIZER_CLOCKS_MATCH',
        'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'rows': rows, 'evidence_sha256': evidence, 'torch_version': torch.__version__,
        'training_modified_or_restarted': False, 'live_training_files_read': False,
        'method_validated': False, 'downstream_promotion_allowed': False,
        'limitations': ['Saved Adam step counters and finite tensors do not prove update correctness.',
                        'Full saved-gradient Adam replay and scientific review remain required at completion.',
                        'No gradients, batches, model derivatives or efficacy are recomputed.'],
    }
    with output.open('x') as handle:
        handle.write(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'evidence_sha256'}))


if __name__ == '__main__':
    main()
