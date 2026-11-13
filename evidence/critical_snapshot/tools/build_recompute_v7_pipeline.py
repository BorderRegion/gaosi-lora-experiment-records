"""Build an isolated continuation of the preserved, failed queue controllers.

Only scheduler dependencies and v7 core paths change. Numerical leaf commands
continue to use the project tools and the existing device lock.
"""
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path


MODULES = [
    'phi_core', 'phi_development', 'organization', 'final_parent',
    'formal_recovery', 'reflow', 'continual', 'seven_b', 'horizon',
    'confirmation', 'noise_controls', 'recovery_statistics',
    'selective_statistics', 'reflow_statistics', 'continual_statistics',
]
ADOPTED = 'phi_mix_O1_L_H2_core_s17_recompute_v7'
CONFIG_SHA = 'f70511613b15835c3eae3ea0fa86e937bb51bf3559301c600a4f9206fca414bc'


ADOPTION_GATE = '''
def wait_for_adopted_l():
    """Adopt one exact running job; never launch or resume it here."""
    import hashlib
    name = 'phi_mix_O1_L_H2_core_s17_recompute_v7'
    configfile = Path('configs') / (name + '.json')
    expected_sha = 'f70511613b15835c3eae3ea0fa86e937bb51bf3559301c600a4f9206fca414bc'
    resultfile = Path('runs') / name / 'result.json'
    while True:
        if hashlib.sha256(configfile.read_bytes()).hexdigest() != expected_sha:
            raise RuntimeError('Adopted L configuration changed')
        state = json.loads((Path('records/jobs') / (name + '.json')).read_text())
        if state['status'] == 'PASS':
            result = json.loads(resultfile.read_text())
            config = json.loads(configfile.read_text())
            if (result['status'] != 'PASS' or result['episodes'] != 192
                    or result['inner_updates'] != 2560
                    or result['horizon'] != 'EXACT_H2_tail'
                    or result['checkpoint'] != f'runs/{name}/latest.pt'
                    or json.loads((Path('runs') / name / 'config.json').read_text()) != config):
                raise RuntimeError('Adopted L result does not match the full v7 schedule')
            import torch
            checkpoint = torch.load(result['checkpoint'], map_location='cpu', weights_only=False)
            def finite(value):
                if torch.is_tensor(value):
                    return bool(torch.isfinite(value).all())
                if isinstance(value, dict):
                    return all(finite(v) for v in value.values())
                if isinstance(value, (tuple, list)):
                    return all(finite(v) for v in value)
                return True
            if (checkpoint['config'] != config or checkpoint['next_episode'] != 192
                    or checkpoint['inner_updates'] != 2560
                    or not finite(checkpoint['phi']) or not finite(checkpoint['meta_optimizer'])):
                raise RuntimeError('Adopted L final checkpoint is incomplete or nonfinite')
            for steps in [512, 2048, 2560]:
                if not (Path('runs') / name / f'phi_{steps:04d}.pt').is_file():
                    raise RuntimeError('Adopted L is missing a required learning-curve snapshot')
            print(json.dumps({'event': 'adopted_existing_L_complete', 'job': name,
                              'credited_inner_updates': 2560}), flush=True)
            return result
        if state['status'] != 'RUNNING':
            raise RuntimeError('Adopted L failed; preserve all evidence and inspect')
        proc = Path(f"/proc/{state.get('child_pid')}/stat")
        fields = proc.read_text().split() if proc.exists() else []
        if not fields or fields[2] == 'Z' or fields[21] != state.get('child_proc_start_ticks'):
            raise RuntimeError('Adopted L has no matching live process')
        time.sleep(30)

'''


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Expected one source anchor: {old!r}')
    return text.replace(old, new, 1)


def transform(module, text):
    for name in MODULES:
        versions = [1, 2, 3] if name == 'phi_core' else [1, 2]
        for version in versions:
            text = text.replace(f'{name}_queue_v{version}', f'{name}_queue_recompute_v7')
    for old, new in [
        ('_H2_core_s17', '_H2_core_s17_recompute_v7'),
        ('_H2_extend3584_s17', '_H2_extend3584_s17_recompute_v7'),
        ('_H1_commonwarm_s17', '_H1_commonwarm_s17_recompute_v7'),
        ('Path(__file__).resolve().parents[1]', 'Path.cwd()'),
    ]:
        text = text.replace(old, new)
    if module == 'phi_core':
        text = replace_once(text, "default='calibration_queue'", "default='calibration_queue_v3'")
        text = replace_once(text, '\ndef main():', '\n' + ADOPTION_GATE + '\ndef main():')
        text = replace_once(text, '    args = p.parse_args()\n',
                            '    args = p.parse_args()\n    adopted_l = wait_for_adopted_l()\n')
        text = replace_once(text, "    for architecture in ['L', 'X']:\n        name = f'profile_3b_",
                            "    for architecture in ['X']:\n        name = f'profile_3b_")
        text = text.replace('H2_three_routes_learned_parent\'', 'H2_three_routes_learned_parent_recompute_v7\'')
        text = replace_once(text, "        train(f'phi_{architecture}_real_episode_readiness_s17', smoke)",
                            "        if architecture != 'L':\n            train(f'phi_{architecture}_real_episode_readiness_s17_recompute_v7', smoke)")
        text = replace_once(text, "purpose='full_protocol_O1_phi_mix_2560'",
                            "purpose='full_protocol_O1_phi_mix_2560_fresh_phi_after_recompute_v7'")
        anchor = "        endpoints[architecture] = train(f'phi_mix_O1_{architecture}_H2_core_s17_recompute_v7', core)"
        text = replace_once(text, anchor, """        if architecture == 'L':
            expected = dict(core, out='runs/phi_mix_O1_L_H2_core_s17_recompute_v7')
            actual = json.loads(Path('configs/phi_mix_O1_L_H2_core_s17_recompute_v7.json').read_text())
            if actual != expected:
                raise RuntimeError('Adopted L differs from the calibrated core template')
            endpoints[architecture] = adopted_l
        else:
            endpoints[architecture] = train(f'phi_mix_O1_{architecture}_H2_core_s17_recompute_v7', core)""")
    ast.parse(text)
    return text


def write_new(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f'Preserved snapshot differs: {path}')
    else:
        path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    dest = root / 'records/pipeline_recompute_v7'
    sources = {}
    for module in MODULES + ['calibration', 'storage']:
        filename = f'{module}_queue.py' if module != 'storage' else 'storage.py'
        source = (root / 'tools' / filename).read_bytes()
        original = source.decode()
        modified = transform(module, original) if module in MODULES else original
        output = modified.encode()
        write_new(dest / 'original' / filename, source)
        write_new(dest / 'tools' / filename, output)
        diff = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
                                          fromfile='original/' + filename, tofile='tools/' + filename))
        write_new(dest / 'diffs' / (filename + '.diff'), diff.encode())
        sources[filename] = {'original_sha256': hashlib.sha256(source).hexdigest(),
                             'continuation_sha256': hashlib.sha256(output).hexdigest()}
    manifest = {'status': 'PREPARED_NOT_STARTED', 'modules': MODULES,
                'adopted_L_job': ADOPTED, 'adopted_L_config_sha256': CONFIG_SHA,
                'sources': sources,
                'jobs': [{'module': n + '_queue', 'name': n + '_queue_recompute_v7'} for n in MODULES],
                'no_duplicate_L': True, 'old_failed_paths_preserved': True,
                'numerical_leaf_sources_modified': False,
                'shared_helpers': ['tools/job.py', 'tools/device_guard.py'],
                'stable_manifest_paths_reused_only_when_no_prior_downstream_result_exists': True}
    write_new(dest / 'build_manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    print(json.dumps({'snapshot': str(dest), 'controllers': len(MODULES), 'status': manifest['status']}))


if __name__ == '__main__':
    main()
