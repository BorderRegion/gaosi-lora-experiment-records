"""Frozen development-only noise transfer controls using production recovery."""
import argparse
import json
from pathlib import Path

from .scale import sha256, valid_freeze


def check_config(config):
    entry = json.loads(Path(config['noise_control_entry']).read_text())
    if entry['status'] != 'FROZEN_NOISE_CONTROL_ENTRY' or entry['config'] != config:
        raise ValueError('Noise-control settings differ from the frozen entry')
    frozen = {}
    # Check every frozen rule before opening task/language data, even for Adam.
    for label, path in entry['phi_freezes'].items():
        if sha256(path) != entry['artifact_sha256'][path]:
            raise ValueError('Noise-control phi freeze bytes changed')
        frozen[label] = valid_freeze(path)
    mix, clean = frozen['mixed'], frozen['clean']
    if mix['training_noise'] != 'mixed' or clean['training_noise'] != 'clean':
        raise ValueError('Noise controls require distinct mixed and clean training rules')
    for f in [mix, clean]:
        if any(f[k] != mix[k] for k in ['architecture', 'recovery_K', 'recovery_lr', 'q_medium', 'development_parent']):
            raise ValueError('Noise-control rules have different development settings')
    plan = json.loads(Path(config['freeze_manifest']).read_text())
    if (plan['status'] != 'PHI_DEVELOPMENT_PLAN' or not plan['noise_transfer_diagnostic']
            or plan['formal_data_used']):
        raise ValueError('Noise-control plan must remain development only')
    if (config['phase'] != 'phi_development' or config['parent'] != mix['development_parent']
            or config['data'] not in plan['allowed_data'] or 'final' in config['data']
            or config['language_eval'] != 'data/language/dev.jsonl'
            or config['K'] != mix['recovery_K'] or config['inner_lr'] != mix['recovery_lr']
            or config['q_apply'] != mix['q_medium'] or config['architecture'] != mix['architecture']
            or config['noise_seed'] != 1729 or config['data_seed'] != 17
            or config['noise_scheme'] not in ['subspace', 'factor', 'uniform', 'shrink']
            or config['task_layout'] != 'all' or config.get('retain_data')
            or config['effective_tokens'] != 512 or config['retain_weight'] != .25):
        raise ValueError('Noise-control primary horizon/dose/data/support settings changed')
    if config['method'] == 'phi':
        if config['phi'] not in [mix['phi'], clean['phi']]:
            raise ValueError('Noise transfer must use one of the frozen O1 rules')
    elif config['method'] not in ['standard_adam', 'paired_adam']:
        raise ValueError('Unsupported noise-control method')
    for label in ['artifact_sha256', 'data_sha256']:
        for path, expected in entry[label].items():
            if sha256(path) != expected:
                raise ValueError(f'Noise-control input bytes changed: {path}')
    required = {config['parent'], config['freeze_manifest'], *entry['phi_freezes'].values()}
    for key in ['phi', 'initial_adapter', 'match_update_norms']:
        if config.get(key):
            required.add(config[key])
    if not required <= entry['artifact_sha256'].keys():
        raise ValueError('Every noise-control input artifact must have frozen bytes')
    if not {config['data'], config['language_eval'], 'data/language/inner.jsonl'} <= entry['data_sha256'].keys():
        raise ValueError('Noise-control data bytes must be declared')
    source = [json.loads(line) for line in Path(config['data']).read_text().splitlines() if line.strip()]
    if not source or any(not r['world_id'].startswith('dev') for r in source):
        raise ValueError('Noise-control rows must come from development worlds')
    if config.get('match_update_norms'):
        values = json.loads(Path(config['match_update_norms']).read_text())
        if len(values) != config['K']:
            raise ValueError('Norm matching requires the exact primary-horizon stock update sequence')
    return entry


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    config = json.loads(Path(args.config).read_text())
    check_config(config)
    import torch
    if config['device'].startswith('musa'):
        import torch_musa
    torch.set_num_threads(8)
    from .recover import run_recovery
    run_recovery(config, args.resume)


if __name__ == '__main__':
    main()
