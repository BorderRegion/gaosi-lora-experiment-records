"""Build independent v8 scheduler sources without starting any controller.

Reuse the preserved protocol schedules; route every meta-training leaf through
the independent guard and require reviewed completed evidence before adoption.
"""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path

from build_recompute_v7_pipeline import MODULES, transform as legacy_transform, write_new
from guarded_pipeline import ADOPTED, CONFIG_SHA


def replace_function(text, name, replacement):
    nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(nodes) != 1:
        raise ValueError('Expected exactly one function ' + name)
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + '\n' + ''.join(lines[node.end_lineno:])


def transform(module, source):
    text = legacy_transform(module, source).replace('recompute_v7', 'observed_blocking_v8')
    for old, new in [
        ('_specialize512_s17', '_specialize512_s17_observed_blocking_v8'),
        ('_matched_s17', '_matched_s17_observed_blocking_v8'),
        ('_s{seed}_v1', '_s{seed}_observed_blocking_v8'),
        ('_s17_v1', '_s17_observed_blocking_v8'),
    ]:
        text = text.replace(old, new)
    # The early 7B evidence belongs to the completed ordinary readiness queue.
    # Its controller and manifest keep their original identities.
    if module == 'phi_core':
        text = replace_function(text, 'run_job', 'from guarded_pipeline import run_job')
        text = replace_function(text, 'wait_for_adopted_l',
                                'from guarded_pipeline import adopt_l as wait_for_adopted_l')
        text = text.replace('full_protocol_O1_phi_mix_2560_fresh_phi_after_observed_blocking_v8',
                            'full_protocol_O1_phi_mix_2560_fresh_phi_candidate_blocking_mitigation_review_required')
    ast.parse(text)
    if 'recompute_v7' in text:
        raise ValueError('A failed v7 path survived transformation')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    dest = root / 'records/pipeline_observed_blocking_v8'
    sources = {}
    for module in MODULES + ['calibration', 'storage']:
        filename = f'{module}_queue.py' if module != 'storage' else 'storage.py'
        original = (root / 'tools' / filename).read_bytes()
        modified = transform(module, original.decode()).encode() if module in MODULES else original
        write_new(dest / 'original' / filename, original)
        write_new(dest / 'tools' / filename, modified)
        diff = ''.join(difflib.unified_diff(original.decode().splitlines(True), modified.decode().splitlines(True),
                                          fromfile='original/' + filename, tofile='tools/' + filename))
        write_new(dest / 'diffs' / (filename + '.diff'), diff.encode())
        sources[filename] = {'original_sha256': hashlib.sha256(original).hexdigest(),
                             'continuation_sha256': hashlib.sha256(modified).hexdigest()}
    helpers = ['tools/guarded_pipeline.py', 'tools/train_guarded_meta.py',
               'tools/audit_observed_meta.py', 'tools/build_observed_v8_pipeline.py',
               'tools/build_recompute_v7_pipeline.py', 'tools/job.py',
               'tools/device_guard.py', 'tools/diagnose_prefix_gradient.py']
    manifest = {'status': 'PREPARED_NOT_STARTED_REQUIRES_CPU_VALIDATION_AND_L_REVIEW',
                'modules': MODULES, 'sources': sources, 'adopted_L_job': ADOPTED,
                'adopted_L_config_sha256': CONFIG_SHA,
                'jobs': [{'module': n + '_queue', 'name': n + '_queue_observed_blocking_v8'} for n in MODULES],
                'helpers_sha256': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in helpers},
                'numerical_source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in sorted((root / 'gaosi').glob('*.py'))},
                'no_duplicate_L': True, 'old_failed_jobs_preserved': True,
                'method_validated': False, 'downstream_started': False,
                'every_meta_entry_requires_saved_raw_gradients_and_separate_scientific_review': True,
                'process_local_environment': {'MUSA_LAUNCH_BLOCKING': '1'},
                'device_lock': '/tmp/gaosi_musa_device0.lock',
                'stable_downstream_record_paths_require_absence_check_before_launch': True,
                'conditional_followups': ['organization extensions if improving',
                                         'official LoRA-FA if O2 best and superiority claimed',
                                         'new-order 16/32 rounds if mechanisms interpretable',
                                         'real-text task if synthetic mechanisms clear']}
    write_new(dest / 'build_manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    print(json.dumps({'snapshot': str(dest), 'controllers': len(MODULES), 'status': manifest['status']}))


if __name__ == '__main__':
    main()
