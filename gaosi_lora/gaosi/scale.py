"""Validated scale entries reuse the production recovery and meta transitions.

Entry manifests lock every setting and the permitted data bytes before a
model is loaded. A 7B local episode never accepts development/final worlds.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda: file.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def valid_freeze(path):
    value = json.loads(Path(path).read_text())
    if (value.get('status') != 'FROZEN' or not value.get('stopping_rule_frozen')
            or value.get('formal_data_used') or value.get('organization') != 'O1'
            or sha256(value['phi']) != value['phi_sha256']):
        raise ValueError('Scale entry requires an intact O1 phi freeze')
    return value


def check_config(config):
    entry = json.loads(Path(config['scale_entry_manifest']).read_text())
    if entry.get('status') != 'FROZEN_SCALE_ENTRY' or entry['config'] != config:
        raise ValueError('Scale settings differ from the frozen entry')
    # Check the trained rule before opening task data, including for Adam.
    for path, expected in entry['upstream_freezes'].items():
        if sha256(path) != expected:
            raise ValueError('Scale upstream freeze bytes changed')
        frozen = valid_freeze(path)
        if frozen['architecture'] != config['architecture']:
            raise ValueError('Scale architecture differs from its frozen rule')
    action = config['scale_action']
    if action not in ['recover', 'meta']:
        raise ValueError('Unknown scale action')
    if action == 'recover':
        freeze = json.loads(Path(config['freeze_manifest']).read_text())
        if config.get('phase', 'formal') == 'formal':
            valid_freeze(config['freeze_manifest'])
        elif config['phase'] == 'phi_development':
            if freeze['status'] != 'PHI_DEVELOPMENT_PLAN':
                raise ValueError('Scale dev recovery requires a declared development plan')
        else:
            raise ValueError('Scale recovery is formal or phi development')
        if config['method'] == 'phi':
            expected = (freeze['allowed_phis'].get(config['phi'])
                        if config.get('phase') == 'phi_development' else freeze['phi_sha256'])
            if sha256(config['phi']) != expected:
                raise ValueError('Scale phi bytes differ from their declared hash')
    for path, expected in entry['artifact_sha256'].items():
        if sha256(path) != expected:
            raise ValueError(f'Scale artifact bytes changed: {path}')
    for path, expected in entry['data_sha256'].items():
        if sha256(path) != expected:
            raise ValueError(f'Scale task/language bytes changed: {path}')
    if action == 'meta':
        if not config.get('initial_phi') or not config.get('retain_fact_support'):
            raise ValueError('Local scale adaptation starts at a trained phi with three support routes')
        manifest = json.loads(Path(config['world_manifest']).read_text())
        meta_files = [str(Path('data/synthetic') / w['file'])
                      for w in manifest['worlds'] if w['split'] == 'meta']
        required = set(meta_files + config['parent_data'] +
                       [config['world_manifest'], 'data/language/inner.jsonl', 'data/language/meta_query.jsonl'])
        if not required <= entry['data_sha256'].keys():
            raise ValueError('Local scale adaptation has undeclared data')
        if any('final' in p or 'dev_parent' in p for p in meta_files + config['parent_data']):
            raise ValueError('Local scale training cannot consume development/final facts')
        for path in meta_files + config['parent_data']:
            source = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
            if not source or any(not r.get('world_id', '').startswith('meta') for r in source):
                raise ValueError('Local scale training requires meta world IDs')
    else:
        required = {config['data'], config['retain_data'], config['language_eval'],
                    'data/language/inner.jsonl'}
        if not required <= entry['data_sha256'].keys():
            raise ValueError('Scale recovery has undeclared data')
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    check_config(config)
    import torch
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    if config['scale_action'] == 'recover':
        from .recover import run_recovery
        run_recovery(config, args.resume)
    else:
        from .meta import train
        train(config, args.resume)


if __name__ == '__main__':
    main()
