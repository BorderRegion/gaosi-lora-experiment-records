"""Common extra-rank16 probes and inference cost on the untouched base."""
import argparse
import hashlib
import json
from pathlib import Path

import torch

from .continual import inference_cost, scratch_probes
from .data import rows
from .model import AdapterModel
from .runtime import Run, seed_all


def evaluate(config):
    frozen = json.loads(Path(config['freeze_manifest']).read_text())
    if (frozen['status'] != 'FROZEN' or not frozen['stopping_rule_frozen'] or frozen['formal_data_used']
            or frozen['organization'] != 'O1'
            or hashlib.sha256(Path(frozen['phi']).read_bytes()).hexdigest() != frozen['phi_sha256']):
        raise ValueError('Base probes require intact O1 freeze before reading formal data')
    plan = json.loads(Path(config['schedule_manifest']).read_text())
    if (plan['status'] != 'FROZEN_CONTINUAL_SCHEDULE' or config['parent'] != plan['parent']
            or config['probe_worlds'] != plan['probe_worlds'] or len(config['probe_worlds']) != 4
            or any(config[key]!=plan[key] for key in ['seq','effective_tokens','inference_repetitions'])):
        raise ValueError('Base probes must match the four frozen continual probe tasks')
    for file, digest in plan['data_sha256'].items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest() != digest:
            raise ValueError('Base probe data changed after schedule freeze')
    seed_all(plan['seed'])
    run = Run(config['out'], config)
    parent = torch.load(config['parent'], map_location='cpu', weights_only=False)
    c = parent['config']
    model = AdapterModel(c['model'], c['rank'], c['targets'], 'O1', config['device'], scale=c['scale'])
    digest = model.base_digest()
    if digest != parent['base_digest']:
        raise ValueError('Base capacity reference differs from the common base')
    del parent
    # Fresh normal LoRA has B=0 and contributes exactly zero. It remains frozen
    # while the extra common rank16 is trained, so this measures W0 itself.
    probes = scratch_probes(model, plan['probe_worlds'], run.path / 'rank16', plan['probe_seed'],
                            config['seq'], config['effective_tokens'], plan['day_lr'])
    prompt = next(row['prompt'] for row in rows(plan['parent_data']) if row['role'] == 'query')
    cost = inference_cost(model, prompt, config.get('inference_repetitions', 5))
    if model.base_digest() != digest or any(torch.count_nonzero(value).item()
                                          for name, value in model.adapter().items() if name.endswith('.B')):
        raise RuntimeError('Base reference was changed by a temporary probe')
    run.result(status='PASS', config=config, common_extra_rank=16, steps=[1,4,16],
               probes=probes, inference=cost, base_unchanged=True, base_digest=digest,
               shared_reference=True, repeated_as_independent_0_4_8_measurements=False,
               compute_counters=model.compute_counters,
               interpretation='Scratch-adapter learnability of W0, not unused capacity in an old adapter')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    evaluate(config)


if __name__ == '__main__':
    main()
