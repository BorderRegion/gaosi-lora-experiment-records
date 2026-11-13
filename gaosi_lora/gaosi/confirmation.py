"""Locked independent-phi confirmations reuse the existing numerical runners."""
import argparse
import json
from pathlib import Path
import random

from .scale import sha256


def check_config(config):
    entry = json.loads(Path(config['confirmation_entry_manifest']).read_text())
    if entry.get('status') != 'FROZEN_CONFIRMATION_ENTRY' or entry['config'] != config:
        raise ValueError('Confirmation settings differ from the frozen entry')
    seed = config['confirmation_seed']
    if seed not in [29, 43] or entry['independent_phi_seed'] != seed:
        raise ValueError('Only the two declared independent training seeds are allowed')
    for path in entry['freeze_manifests']:
        f = json.loads(Path(path).read_text())
        if (f.get('status') != 'FROZEN' or f.get('formal_data_used')
                or not f.get('stopping_rule_frozen') or sha256(f['phi']) != f['phi_sha256']):
            raise ValueError('Confirmation requires intact frozen phi rules for every method')
    for group in ['artifact_sha256', 'data_sha256']:
        for path, expected in entry[group].items():
            if sha256(path) != expected:
                raise ValueError(f'Confirmation input bytes changed: {path}')
    action = config['confirmation_action']
    if action != 'meta':
        if config['freeze_manifest'] not in entry['freeze_manifests']:
            raise ValueError('Each evaluated method must declare its own frozen independent phi')
        frozen = json.loads(Path(config['freeze_manifest']).read_text())
        if frozen.get('independent_phi_seed') != seed:
            raise ValueError('Confirmation evaluation points to a different phi training seed')
        required_artifacts = {config['parent']}
        for key in ['initial_adapter', 'source_checkpoint', 'phi']:
            if config.get(key):
                required_artifacts.add(config[key])
        if not required_artifacts <= entry['artifact_sha256'].keys():
            raise ValueError('Every evaluated parent/source/phi must have frozen bytes')
    if action == 'meta':
        if config['seed'] != seed or not config['retain_fact_support']:
            raise ValueError('Independent meta seed/three support routes cannot change')
        reference_path = config['meta_reference_config']
        if reference_path not in entry['artifact_sha256']:
            raise ValueError('Independent training requires the frozen original core settings')
        reference = json.loads(Path(reference_path).read_text())
        selected = json.loads(Path('records/phi_architecture_selected.json').read_text())
        if 'records/phi_architecture_selected.json' not in entry['artifact_sha256']:
            raise ValueError('Development architecture/budget selection must be frozen')
        for key in ['model', 'base_revision', 'rank', 'targets', 'scale', 'architecture',
                    'meta_lr', 'inner_lr', 'seq', 'effective_tokens', 'retain_weight',
                    'retain_fact_support', 'penalty', 'chunk', 'horizon']:
            if config[key] != reference[key]:
                raise ValueError(f'Independent training changed frozen {key}')
        if config['architecture'] != selected['architecture'] or selected['core_inner_steps'] not in [2560, 3584]:
            raise ValueError('Independent training must use the development-selected architecture/budget')
        schedule = [8] * 64 + [16] * 128
        if selected['core_inner_steps'] == 3584:
            schedule += [16] * 64
        manifest = json.loads(Path(config['world_manifest']).read_text())
        original = json.loads(Path(reference['world_manifest']).read_text())
        shuffled = json.loads(json.dumps(original))
        slots = [i for i, w in enumerate(original['worlds']) if w['split'] == 'meta']
        worlds = [original['worlds'][i] for i in slots]
        random.Random(seed).shuffle(worlds)
        for index, world in zip(slots, worlds):
            shuffled['worlds'][index] = world
        if manifest != shuffled:
            raise ValueError('Independent manifest may only permute the original meta worlds with its seed')
        meta_files = [str(Path('data/synthetic') / w['file'])
                      for w in manifest['worlds'] if w['split'] == 'meta']
        sources = meta_files + config['parent_data'] + config.get('historical_data', [])
        required = set(sources + [config['world_manifest'], reference['world_manifest'], 'data/language/inner.jsonl',
                                  'data/language/meta_query.jsonl'])
        if not required <= entry['data_sha256'].keys():
            raise ValueError('Confirmation meta data must be declared')
        for path in sources:
            source = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
            if not source or any(not r.get('world_id', '').startswith('meta') for r in source):
                raise ValueError('Independent phi training may only consume meta-world rows')
        stage = config['confirmation_stage']
        if stage in ['mixed_core_from_zero', 'clean_full_from_zero']:
            if config.get('initial_phi') or config.get('start_episode') or config.get('load_initial_meta_optimizer'):
                raise ValueError('An independent phi must start from zero output and fresh meta optimizer')
        elif stage == 'organization_specialization':
            import torch
            parent = torch.load(config['initial_phi'], map_location='cpu', weights_only=False)
            if (parent['config']['confirmation_seed'] != seed
                    or parent['config']['confirmation_stage'] != 'mixed_core_from_zero'
                    or parent['inner_updates'] != selected['core_inner_steps']
                    or not config['load_initial_meta_optimizer']):
                raise ValueError('Specialization must start at its own independently trained mixed core')
        else:
            raise ValueError('Unknown independent meta stage')
        expected = [8] * 64 if stage == 'organization_specialization' else schedule
        if stage == 'clean_full_from_zero':
            expected = schedule + [8] * 64
        if config['episode_K'] != expected or config['known_warm_episodes'] != (0 if stage == 'organization_specialization' else 64):
            raise ValueError('Independent core/clean/specialization schedule changed')
        if config['q_cycle'] != ([0.] if stage == 'clean_full_from_zero' else reference['q_cycle']):
            raise ValueError('Independent training noise schedule changed')
        if stage != 'organization_specialization' and config['organization'] != 'O1':
            raise ValueError('Independent cores and clean rules are trained in O1')
        paired_keys = ['parents', 'parent_data']
        baseline = reference
        artifacts = config['parents'] + [config['initial_phi']] if config.get('initial_phi') else config['parents'][:]
        if config['organization'] == 'O4':
            path = 'records/o4_parents_ready.json'
            if path not in entry['artifact_sha256']:
                raise ValueError('O4 independent training requires its paired construction record')
            o4 = json.loads(Path(path).read_text())
            if o4['status'] != 'PASS' or o4['formal_data_used']:
                raise ValueError('O4 construction must be valid meta-only data')
            baseline = {'parents': [r['checkpoint'] for r in o4['parents']],
                        'parent_data': [r['config']['data'] for r in o4['parents']],
                        'historical_bases': [r['master'] for r in o4['histories']],
                        'historical_data': [r['historical_bindings'] for r in o4['histories']]}
            paired_keys += ['historical_bases', 'historical_data']
            artifacts += config['historical_bases'] + [p + '.json' for p in config['historical_bases']]
        order = list(range(len(baseline['parents'])))
        random.Random(seed + 1).shuffle(order)
        if any(config[key] != [baseline[key][i] for i in order] for key in paired_keys):
            raise ValueError('Independent current/history/data parents must stay paired in the declared order')
        if not set(artifacts) <= entry['artifact_sha256'].keys():
            raise ValueError('Independent training artifacts must be declared')
    elif action == 'recover':
        if config['phase'] != 'formal' or config['data_seed'] != seed:
            raise ValueError('Confirmation recovery uses its fixed independent support seed')
        if (config['K'] != frozen['recovery_K'] or config['q_apply'] not in [0., frozen['q_medium']]
                or config['noise_seed'] != 1729 or config['noise_scheme'] != 'subspace'
                or config['organization'] != 'O1' or config['architecture'] != frozen['architecture']):
            raise ValueError('Confirmation must use actual primary K, doses and paired noise')
        if config['method'] == 'factor_lr':
            path = 'records/factor_recovery_frozen_v1.json'
            if path not in entry['artifact_sha256']:
                raise ValueError('Factor-LR confirmation needs its development freeze')
            factor = json.loads(Path(path).read_text())
            if (factor['formal_data_used'] or factor['selection_K'] != frozen['recovery_K']
                    or config['inner_lr'] != factor['selected_lr'] or config['ba_ratio'] != factor['selected_ratio']):
                raise ValueError('Confirmation factor baseline changed its development settings')
        elif config['method'] not in ['standard_adam', 'paired_adam', 'phi'] or config['inner_lr'] != frozen['recovery_lr']:
            raise ValueError('Confirmation method/rate differs from its frozen recovery settings')
        if config['method'] == 'phi' and config['phi'] != frozen['phi']:
            raise ValueError('Confirmation must use its own frozen phi')
        required = {config['data'], config.get('retain_data', config['data']),
                    'data/language/inner.jsonl', config['language_eval']}
        if not required <= entry['data_sha256'].keys():
            raise ValueError('Confirmation recovery data must be declared')
    elif action == 'reflow':
        if config['data_seed'] != seed or config['K'] != 80 or config['eval_steps'] != [20, 80]:
            raise ValueError('Independent continuation budget/seed changed')
        if not {config['data'], config['language_eval']} <= entry['data_sha256'].keys():
            raise ValueError('Independent continuation data must be declared')
    elif action == 'continual':
        if config['seed'] != seed or config['rounds'] != 8:
            raise ValueError('Independent continual seed/eight-round schedule changed')
        if not {config['parent_data'], *config['worlds'], *config['probe_worlds'],
                'data/language/inner.jsonl', config['language_eval']} <= entry['data_sha256'].keys():
            raise ValueError('Independent continual stream/probe/language data must be declared')
    else:
        raise ValueError('Unknown confirmation action')
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    check_config(config)  # Before task data and model loading in each runner.
    import torch
    if config['device'].startswith('musa'):
        import torch_musa
    torch.set_num_threads(8)
    action = config['confirmation_action']
    if action == 'meta':
        from .meta import train
        train(config, args.resume)
    elif action == 'recover':
        from .recover import run_recovery
        run_recovery(config, args.resume)
    elif action == 'reflow':
        from .reflow import run_reflow
        run_reflow(config, args.resume)
    else:
        from .continual import train
        train(config, args.resume)


if __name__ == '__main__':
    main()
