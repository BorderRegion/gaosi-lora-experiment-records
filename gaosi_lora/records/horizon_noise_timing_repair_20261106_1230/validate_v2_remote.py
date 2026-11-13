"""Validate completed H1/H2 tensors and postprocessing gates on CPU only."""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path('/root/autodl-tmp/gaosi_lora')
HERE = ROOT / 'records/horizon_effect_comparison_20261103_1005'
RECORD = ROOT / 'records/horizon_noise_timing_repair_20261106_1230'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    assert Path.cwd() == ROOT and not (RECORD / 'remote_validation.json').exists()
    import torch
    torch.set_num_threads(2)
    sys.path.insert(0, str(HERE))
    path = HERE / 'compare_noise_timing_v2.py'
    spec = importlib.util.spec_from_file_location('comparison_v2', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inputs = module.read(HERE / 'inputs.json')
    module.verify_inputs(inputs)
    endpoints = module.endpoint_gate()
    declared = module.pairs(inputs)
    assert declared == read(HERE / 'declared_pairs.json')
    pair = declared[0]
    review = module.report_pair(pair, inputs)
    pairpath = HERE / (Path(pair['H1']['out']).name + '_paired_review_v2.json')
    module.preserve(pairpath, review)
    h1result = read(ROOT / pair['H1']['out'] / 'result.json')
    h2result = read(ROOT / pair['H2']['out'] / 'result.json')
    actual_read = module.read
    rejected = []
    for key in ['base_digest', 'initial', 'clean']:
        modified = copy.deepcopy(h1result)
        modified[key] = {'deliberate_invalid_mutation': True}
        h1path = ROOT / pair['H1']['out'] / 'result.json'
        def mutated_read(p):
            if (ROOT / Path(p)).resolve() == h1path:
                return modified
            return actual_read(p)
        module.read = mutated_read
        try:
            module.report_pair(pair, inputs)
        except (AssertionError, ValueError):
            rejected.append(key)
        else:
            raise AssertionError('Mutation accepted: ' + key)
        finally:
            module.read = actual_read
    assert rejected == ['base_digest', 'initial', 'clean']
    old_initial = torch.load(ROOT / pair['H2']['out'] / 'initial.pt', map_location='cpu', weights_only=False)
    assert old_initial['corruption'] == h2result['noise']
    module.noise_pair(old_initial['corruption'], h1result['noise'])
    ledger = [json.loads(s) for s in (ROOT / pair['H1']['out'] / 'events.jsonl').read_text().splitlines()]
    last = ledger[-1]
    job = read(ROOT / 'records/jobs' / (Path(pair['H1']['out']).name + '.json'))
    active = last['process_wall_seconds']
    record = {
        'status': 'REAL_CPU_H1_H2_PAIR_AND_STRICT_GATE_VALIDATION_PASS',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'torch_threads': torch.get_num_threads(), 'pair_review': str(pairpath.relative_to(ROOT)),
        'pair_review_sha256': sha(pairpath),
        'all64_per_step_tokens_and_terminal_saved_stream_states_equal': True,
        'shared_initial_adapter_tensor_digest': review['initial_adapter_tensor_sha256'],
        'shared_initial_corruption_non_timing_fields_equal': True,
        'real_completed_initial_and_clean_evaluations_equal': True,
        'strict_comparison_mutations_rejected': rejected,
        'noise_timer_review': review['noise_timing_review'],
        'measured_H1_wrapper_seconds_including_lock_wait': job['wall_seconds'],
        'measured_H1_last_event_process_seconds': active,
        'wrapper_minus_last_event_process_seconds_includes_lock_wait_and_startup': job['wall_seconds'] - active,
        'core_endpoint_evidence_sha256': endpoints,
        'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [
            path, HERE / 'noise_metadata_v2.py', HERE / 'audit_horizon_v2.py',
            HERE / 'phi_horizon_L_H1_2560_t0_clean_s17_v1.json',
            ROOT / 'records/jobs/phi_horizon_effect_comparison_s17_v1.json',
            ROOT / 'records/logs/phi_horizon_effect_comparison_s17_v1.log', Path(__file__)]},
        'numerical_training_executed': False, 'continuation_started': False,
        'method_validated': False, 'full_goal_complete': False}
    module.preserve(RECORD / 'remote_validation.json', record)
    print(json.dumps({'status': record['status'], 'pair_review_sha256': sha(pairpath),
                      'validation_sha256': sha(RECORD / 'remote_validation.json'),
                      'H1_process_seconds': active, 'H1_wrapper_seconds': job['wall_seconds']}))


if __name__ == '__main__':
    main()
