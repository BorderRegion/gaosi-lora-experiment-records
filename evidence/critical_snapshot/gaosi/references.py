"""Base/drop-adapter and matched retain-only references after phi freezing."""
import argparse
import hashlib
import json
from pathlib import Path

import torch

from .data import LanguageStream, rows
from .evaluate import evaluate_language, evaluate_qa
from .model import AdapterModel, NativeLoRA, memory
from .runtime import Run, seed_all


def evaluate_references(config):
    run = Run(config['out'], config)
    frozen = json.loads(Path(config['freeze_manifest']).read_text())
    if (frozen['status'] != 'FROZEN' or not frozen['stopping_rule_frozen']
            or hashlib.sha256(Path(frozen['phi']).read_bytes()).hexdigest() != frozen['phi_sha256']):
        raise ValueError('References require an intact frozen phi and stopping rule')
    seed_all(config['seed'])
    checkpoints = {}
    for mode in ['all', 'retain_only']:
        checkpoint = config['parents'][mode]
        verified = json.loads((Path(checkpoint).parent / 'result.json').read_text())
        if not verified['acquisition_gate_passed']:
            raise ValueError('Reference parent did not pass acquisition')
        checkpoints[mode] = torch.load(checkpoint, map_location='cpu', weights_only=False)
    all_parent, retain = checkpoints['all'], checkpoints['retain_only']
    if all_parent['base_digest'] != retain['base_digest']:
        raise ValueError('All/retain-only references must share the same base')
    for key in ['model', 'base_revision', 'rank', 'targets', 'scale', 'data', 'target_ids', 'parent_layout']:
        if all_parent['config'][key] != retain['config'][key]:
            raise ValueError(f'Reference parents differ in {key}')
    if (all_parent['config']['parent_mode'] != 'all' or retain['config']['parent_mode'] != 'retain_only'
            or set(config['target_ids']) != set(all_parent['config']['target_ids'])):
        raise ValueError('Reference parents do not implement the same F/R request')
    if all_parent['fact_stream'] != retain['fact_stream']:
        raise ValueError('All/retain-only parents have different R exposures')
    c = all_parent['config']
    model = AdapterModel(c['model'], c['rank'], c['targets'], 'O1', config['device'], scale=c['scale'])
    if model.base_digest() != all_parent['base_digest']:
        raise ValueError('Reference base differs from the learned parent')
    query = [r for r in rows(config['data'], 'query') if r['sample_id'].endswith(':0')]
    targets = set(config['target_ids'])
    groups = {'F': [r for r in query if r['fact_id'] in targets],
              'R': [r for r in query if r['fact_id'] not in targets]}
    if not groups['F'] or not groups['R']:
        raise ValueError('References require nonempty F and R query groups')
    target_entities = {row['entity'] for row in groups['F']}
    same_entity_count = sum(row['entity'] in target_entities for row in groups['R'])
    language = LanguageStream(config['language_eval'], model.tokenizer, c['seq'], 991)
    results = {}

    def measure(label):
        result = {name: evaluate_qa(model, data, run.path / f'{label}_{name}.jsonl')
                  for name, data in groups.items()}
        result['language'] = evaluate_language(model, language, config['language_eval_tokens'])
        run.log('reference', label=label, metrics=result)
        return result

    model.load_adapter(all_parent['adapter'])
    results['parent_all'] = measure('parent_all')
    for module in model.modules():
        if isinstance(module, NativeLoRA):
            module.enabled = False
    results['base'] = measure('base')
    # Disabling the adapter is exactly W0 here. Keep both semantic labels and
    # share the measured examples instead of pretending they are independent.
    results['drop_adapter'] = results['base']
    for module in model.modules():
        if isinstance(module, NativeLoRA):
            module.enabled = True
    model.load_adapter(retain['adapter'])
    results['retain_only'] = measure('retain_only')
    assert model.base_digest() == all_parent['base_digest']
    run.result(status='PASS', config=config, results=results, base_unchanged=True,
               base_equals_drop_adapter=True, drop_adapter_measurements='base_F.jsonl and base_R.jsonl',
               same_entity_R_equals_R=same_entity_count == len(groups['R']),
               same_entity_R_query_count=same_entity_count,
               same_paragraph_R='Not applicable: this synthetic dataset contains separate entries, not mixed paragraphs.',
               memory=memory(config['device']),
               note='Reference behavior only; disabling LoRA does not delete immutable base knowledge.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    evaluate_references(config)


if __name__ == '__main__':
    main()
