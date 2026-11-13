"""Versioned integer-affine task family; original modulo11 tasks stay intact."""
import argparse
import hashlib
import json
from pathlib import Path
import random

SUPPORT_X = [0, 1, 2, 4, 6, 8, 10]
QUERY_X = [3, 5, 7, 9]


def generate(name, seed):
    rng = random.Random(seed)
    a, b = rng.choice([1, 2]), rng.choice([0, 1, 2, 3, 4])
    data = []
    for role, xs in [('support', SUPPORT_X), ('query', QUERY_X)]:
        for x in xs:
            data.append({'sample_id': f'{name}:{role}:{x}', 'world_id': name,
                         'kind': 'affine_integer', 'role': role, 'x': x,
                         'prompt': f'In system {name}, f multiplies its input by a fixed integer and adds a fixed integer. What is f({x})?\nAnswer: ',
                         'answer': str(a * x + b), 'candidates': [str(k) for k in range(25)]})
    return data, {'a': a, 'b': b}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='data/synthetic/rules_integer_v1')
    parser.add_argument('--heldout', action='store_true')
    parser.add_argument('--freeze', default='records/rule_family_frozen.json')
    args = parser.parse_args()
    dest = Path(args.out)
    if not args.heldout:
        if dest.exists():
            raise RuntimeError('Do not overwrite an existing task version')
        dest.mkdir(parents=True)
        manifest = {'version': 'integer_affine_interpolation_v1', 'phase': 'development_pilot',
                    'reason': 'Original mod11 worlds fit support but fail disjoint x queries at three-LR screen; simplify arithmetic and remove extrapolation before phi training.',
                    'seed': 314159, 'splits_generated': ['dev'], 'meta_final_generated': False,
                    'a_values': [1, 2], 'b_values': [0, 1, 2, 3, 4],
                    'support_x': SUPPORT_X, 'query_x': QUERY_X,
                    'ordinary_budget': {'steps': 128, 'lr': .0003},
                    'query_selection': 'All predefined dev query examples. No final results used.',
                    'worlds': [], 'oracles_offline_only': {}}
        combined = []
        for i in range(8):
            name = f'dev_integer_world_{i:03d}'
            data, oracle = generate(name, 314159 + 2000 + i)
            file = dest / f'{name}.jsonl'
            file.write_text(''.join(json.dumps(r) + '\n' for r in data))
            manifest['worlds'].append({'world_id': name, 'file': file.name,
                                      'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            manifest['oracles_offline_only'][name] = oracle
            combined.extend(data)
        file = dest / 'dev_combined.jsonl'
        file.write_text(''.join(json.dumps(r) + '\n' for r in combined))
        manifest['combined_sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
        (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    else:
        freeze = json.loads(Path(args.freeze).read_text())
        if freeze['status'] != 'FAMILY_FROZEN' or freeze['version'] != 'integer_affine_interpolation_v1':
            raise ValueError('Task family must be frozen from dev pilot before held-out generation')
        original = json.loads(Path('data/synthetic/manifest.json').read_text())
        manifest = {'version': freeze['version'], 'freeze': args.freeze, 'worlds': [],
                    'oracles_offline_only': {}, 'original_hard_worlds_retained': True}
        combined = []
        for split, start, end, offset in [('meta', 48, 64, 1000), ('final', 16, 32, 3000)]:
            for i in range(start, end):
                name = f'{split}_integer_world_{i:03d}'
                data, oracle = generate(name, 314159 + offset + i)
                file = dest / f'{name}.jsonl'
                if file.exists():
                    raise RuntimeError('Do not overwrite held-out tasks')
                file.write_text(''.join(json.dumps(r) + '\n' for r in data))
                manifest['worlds'].append({'world_id': name, 'split': split, 'kind': 'affine_integer',
                                          'file': str(file.relative_to('data/synthetic')),
                                          'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
                manifest['oracles_offline_only'][name] = oracle
        revised = {k: v for k, v in original.items() if k != 'rule_oracles'}
        revised['worlds'] = [w for w in original['worlds'] if w['kind'] == 'fact']
        revised['worlds'] += [{k: v for k, v in w.items() if k != 'sha256'} for w in manifest['worlds']]
        for w in json.loads((dest / 'manifest.json').read_text())['worlds']:
            revised['worlds'].append({'world_id': w['world_id'], 'split': 'dev', 'kind': 'affine_integer',
                                      'file': str((dest / w['file']).relative_to('data/synthetic'))})
        revised['rule_family_freeze'] = args.freeze
        revised['original_manifest'] = 'data/synthetic/manifest.json'
        (dest / 'active_manifest.json').write_text(json.dumps(revised, indent=2))
        (dest / 'heldout_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
