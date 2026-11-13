"""Small full-base corruption followed by ordinary LoRA-only repair.

The loaded working copy is perturbed once. Model files stay untouched and
base parameters receive no training gradients. This is outside the main
adapter-noise experiment and never changes phi or calibration settings.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import torch

from .data import rows, LanguageStream
from .evaluate import evaluate_qa, evaluate_language
from .model import AdapterModel, synchronize
from .noise import canonical_noise
from .runtime import rng_state, restore_rng
from .scale import sha256


def parameter_seed(seed, name):
    raw = hashlib.sha256(f'{seed}:{name}'.encode()).digest()
    return int.from_bytes(raw[:8], 'little') % (2 ** 63 - 1)


@torch.no_grad()
def perturb_base(model, q, seed, chunk_elements=1048576):
    """Per-tensor RMS Gaussian in FP32, rounded once to the working dtype.

    Every floating base parameter, including embeddings/norms/biases, is
    included once; tied parameters are not perturbed a second time. Small
    CPU chunks bound staging memory. q is unrelated to adapter q_apply.
    """
    if not 0 <= q <= 1 or chunk_elements < 1:
        raise ValueError('Invalid base noise dose or chunk size')
    started = time.time()
    seen, records = {}, []
    for name, parameter in model.named_parameters():
        if name in model.specs or not parameter.is_floating_point():
            continue
        if not parameter.is_contiguous():
            raise ValueError('Boundary corruption requires contiguous base parameters')
        identity = (str(parameter.device), parameter.untyped_storage().data_ptr(),
                    parameter.storage_offset(), parameter.numel())
        if identity in seen:
            continue
        seen[identity] = name
        flat = parameter.detach().view(-1)
        squared = 0.
        for begin in range(0, flat.numel(), chunk_elements):
            values = flat[begin:begin + chunk_elements].cpu().float()
            squared += float(values.double().square().sum())
        rms = math.sqrt(squared / max(1, flat.numel()))
        generator = torch.Generator(device='cpu').manual_seed(parameter_seed(seed, name))
        damage, rounding, after_squared, changed = 0., 0., 0., 0
        for begin in range(0, flat.numel(), chunk_elements):
            destination = flat[begin:begin + chunk_elements]
            clean = destination.cpu().float()
            if q and rms:
                target = math.sqrt(1 - q) * clean + math.sqrt(q) * rms * torch.randn(
                    clean.shape, generator=generator, dtype=torch.float32)
            else:
                target = clean.clone()
            rounded = target.to(parameter.dtype).float()
            damage += float((rounded.double() - clean.double()).square().sum())
            rounding += float((rounded.double() - target.double()).square().sum())
            after_squared += float(rounded.double().square().sum())
            changed += int(rounded.ne(clean).sum())
            destination.copy_(rounded.to(destination))
        records.append({'name': name, 'elements': flat.numel(), 'dtype': str(parameter.dtype),
                        'original_squared': squared, 'perturbed_squared': after_squared,
                        'actual_damage_squared': damage, 'rounding_squared': rounding,
                        'changed_elements': changed, 'rms': rms})
    original = sum(r['original_squared'] for r in records)
    damage = sum(r['actual_damage_squared'] for r in records)
    return {'scheme': 'all_floating_base_parameters_per_tensor_RMS_Gaussian',
            'base_q': q, 'base_noise_seed': seed, 'chunk_elements': chunk_elements,
            'CPU_generator': 'independent SHA256(seed:name) seed per named parameter',
            'normalization': 'sqrt(1-q)*W + sqrt(q)*RMS(W)*Z in FP32 then one working-dtype rounding',
            'excludes': 'all adapter parameters and non-parameter buffers',
            'shared_parameter_policy': 'deduplicated parameter/storage, one perturbation',
            'zero_rms_policy': 'zero parameters stay zero', 'base_parameters': records,
            'parameter_elements': sum(r['elements'] for r in records),
            'original_squared': original, 'actual_damage_squared': damage,
            'actual_relative_damage': math.sqrt(damage / max(original, 1e-300)),
            'rounding_squared': sum(r['rounding_squared'] for r in records),
            'perturbation_seconds': time.time() - started}


def check_config(config):
    plan = json.loads(Path(config['freeze_manifest']).read_text())
    if plan.get('status') != 'DEVELOPMENT_PLAN' or not plan.get('base_boundary_diagnostic'):
        raise ValueError('Base boundary requires its own declared development plan')
    if config not in plan['boundary_configs']:
        raise ValueError('Base boundary settings differ from their declaration')
    # Calibration and data checks precede model construction or task access.
    if sha256(plan['calibration_manifest']) != plan['calibration_sha256']:
        raise ValueError('Boundary calibration bytes changed')
    calibration = json.loads(Path(plan['calibration_manifest']).read_text())
    if (calibration['status'] != 'CALIBRATION_FROZEN' or calibration['formal_data_used']
            or not calibration['ordinary_recovery_validated']):
        raise ValueError('Boundary needs a valid ordinary recovery calibration')
    if (config['method'] != 'standard_adam' or config['q_apply'] != 0.
            or config['noise_scheme'] != 'subspace' or config['phase'] != 'development'):
        raise ValueError('Boundary repair is ordinary Adam without adapter noise')
    for path, digest in plan['data_sha256'].items():
        if sha256(path) != digest:
            raise ValueError('Boundary development data bytes changed')
    if sha256(config['parent']) != plan['parent_sha256']:
        raise ValueError('Boundary parent bytes changed')
    return plan


def loss_changes(reference, damaged, output):
    return {route: {'injected': damaged[route]['nll'] - reference[route]['nll'],
                    'recovered': damaged[route]['nll'] - output[route]['nll'],
                    'residual': output[route]['nll'] - reference[route]['nll']}
            for route in ['task', 'language']}


def run_boundary(config, resume=False):
    plan = check_config(config)
    import gaosi.recover as recovery
    original_constructor = recovery.AdapterModel
    captured = {}

    def working_model(*args, **kwargs):
        model = AdapterModel(*args, **kwargs)
        original_digest = model.base_digest()
        checkpoint = torch.load(config['parent'], map_location='cpu', weights_only=False)
        clean, _ = canonical_noise(checkpoint['adapter'], model.specs, 0., config['noise_seed'])
        model.load_adapter(clean)
        data = [r for r in rows(config['data'], 'query')
                if r['sample_id'].endswith(':0') or r['kind'] != 'fact']
        snapshot = rng_state()
        try:
            reference = {'task': evaluate_qa(model, data, Path(config['out']) / 'pristine_base_task.jsonl'),
                         'language': evaluate_language(model, LanguageStream(config['language_eval'],
                                      model.tokenizer, checkpoint['config']['seq'], 991),
                                      config['language_eval_tokens'])}
            before_adapter = model.adapter_cpu()
            noise = perturb_base(model, config['base_q'], config['base_noise_seed'], config['base_chunk_elements'])
            for name, value in model.adapter().items():
                if not torch.equal(value.detach().cpu(), before_adapter[name]):
                    raise RuntimeError('Base corruption changed adapter bytes')
            if any(p.requires_grad for name, p in model.named_parameters() if name not in model.specs):
                raise RuntimeError('Base corruption enabled a base parameter gradient')
        finally:
            restore_rng(snapshot)
        perturbed_digest = model.base_digest()
        if config['base_q'] == 0 and perturbed_digest != original_digest:
            raise RuntimeError('Zero base noise must be a bitwise identity')
        captured.update(pristine_base_digest=original_digest, perturbed_base_digest=perturbed_digest,
                        pristine_reference=reference, corruption=noise)
        return model

    recovery.AdapterModel = working_model
    try:
        recovery.run_recovery(config, resume)
    finally:
        recovery.AdapterModel = original_constructor
    result = json.loads((Path(config['out']) / 'result.json').read_text())
    if not result['base_unchanged'] or result['base_digest'] != captured['perturbed_base_digest']:
        raise RuntimeError('Perturbed base changed during adapter-only repair')
    changes = loss_changes(captured['pristine_reference'], result['initial'], result['final'])
    diagnostic = {'status': 'PASS', 'config': config, **captured,
                  'repair': result, 'loss_changes_vs_pristine_base': changes,
                  'primary_K': plan['primary_K'], 'actual_K': config['K'],
                  'base_unchanged_during_repair_means': 'the corrupted working base, not the pristine pretrained base',
                  'scope': 'Development-only full-base-noise plus ordinary LoRA repair boundary; not phi efficacy, permanent deletion or main adapter-noise results'}
    (Path(config['out']) / 'boundary_result.json').write_text(json.dumps(diagnostic, indent=2))
    print(json.dumps({'event': 'base_boundary_complete', 'base_q': config['base_q'],
                      'actual_relative_base_damage': captured['corruption']['actual_relative_damage'],
                      'loss_changes_vs_pristine_base': changes}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    if config['device'] == 'musa':
        import torch_musa
    torch.set_num_threads(8)
    run_boundary(config, args.resume)


if __name__ == '__main__':
    main()
