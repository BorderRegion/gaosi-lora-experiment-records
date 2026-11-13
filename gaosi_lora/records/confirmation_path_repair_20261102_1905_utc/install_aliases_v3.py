"""Repair four metadata references with exclusive, explicitly recorded links.

No training, source edits, process signals, resumption, or historical overwrites.
Links point only to this controller's exact future observed-v8 output filenames.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path

PRODUCER = 'records/pipeline_observed_blocking_v8/tools/confirmation_queue.py'
PRODUCER_SHA = 'bf82f5d4bc3e7aa23ccb0d719714f8eb795312e01a74616cf9dff4e15f1d67ba'
JOB = 'confirmation_queue_observed_blocking_v8'
IDENTITY = {'wrapper_pid': 761694, 'child_pid': 761695, 'child_proc_start_ticks': '80739193'}
RECORD = Path('records/confirmation_path_repair_20261102_1905_utc')


def mapping():
    return {f'records/confirmation_{kind}_ready_s{seed}_v1.json':
            f'confirmation_{kind}_ready_s{seed}_observed_blocking_v8.json'
            for kind in ['recovery', 'continual'] for seed in [29, 43]}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_links(root):
    links = mapping()
    # Check all four before creating any; broken/stale symlinks also conflict.
    for name, target in links.items():
        alias = root / name
        expected = alias.parent / target
        if os.path.lexists(alias) or os.path.lexists(expected):
            raise RuntimeError('Require absent alias and exact future target: ' + name)
    made = []
    try:
        for name, target in links.items():
            alias = root / name
            os.symlink(target, alias)
            made.append(alias)
            assert alias.is_symlink() and os.readlink(alias) == target
            assert not alias.exists(), 'No completion evidence may exist before the producer writes it'
    except BaseException:
        for alias in made:
            if alias.is_symlink() and os.readlink(alias) == links[str(alias.relative_to(root))]:
                alias.unlink()
        raise
    return links


def preflight(root):
    assert digest(root / PRODUCER) == PRODUCER_SHA
    build = json.loads((root / 'records/pipeline_observed_blocking_v8/build_manifest.json').read_text())
    assert build['sources']['confirmation_queue.py']['continuation_sha256'] == PRODUCER_SHA
    state_path = root / 'records/jobs' / (JOB + '.json')
    state = json.loads(state_path.read_text())
    assert state['status'] == 'RUNNING' and state['command'] == ['/usr/bin/python', '-u', PRODUCER]
    assert all(state[k] == value for k, value in IDENTITY.items())
    proc = Path('/proc') / str(state['child_pid'])
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == IDENTITY['child_proc_start_ticks']
    assert (proc / 'cmdline').read_bytes().split(b'\0')[:3] == [b'/usr/bin/python', b'-u', PRODUCER.encode()]
    assert not (proc / 'task' / str(state['child_pid']) / 'children').read_text().strip()
    for name in ['continual_pilot_ready_v1.json', 'confirmation_ready_v1.json',
                 'confirmation_organization_selected_v1.json']:
        assert not os.path.lexists(root / 'records' / name), name
    assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino == 31148027670
    return {'job': state, 'producer_sha256': PRODUCER_SHA,
            'build_manifest_sha256': digest(root / 'records/pipeline_observed_blocking_v8/build_manifest.json'),
            'producer_live_and_dormant_before_pilot': True,
            'original_gpu_lock_inode_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/root/autodl-tmp/gaosi_lora'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = root / RECORD / 'alias_activation.json'
    assert not receipt.exists()
    with (root / RECORD / 'alias_install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        evidence = preflight(root)
        if not args.apply:
            print(json.dumps({'status': 'PREFLIGHT_ONLY_NO_LINKS_CREATED', **evidence}))
            return
        validation = json.loads((root / RECORD / 'alias_validation_remote.json').read_text())
        assert validation['status'] == 'ALL_FOUR_CONSUMERS_ALIAS_INTEGRATION_PASS'
        for name in ['validate_aliases_v4.py', 'install_aliases_v3.py', 'validate_candidate.py', 'original/confirmation_queue.py']:
            assert validation['source_sha256'][name] == digest(root / RECORD / name), name
        for name, expected in validation['analysis_source_sha256'].items():
            assert digest(root / name) == expected, name
        links = create_links(root)
        value = {'status': 'FOUR_EXPLICIT_METADATA_ALIASES_INSTALLED',
                 'activated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'aliases_relative_to_project': links, **evidence,
                 'validation_sha256': digest(root / RECORD / 'alias_validation_remote.json'),
                 'installer_sha256': digest(Path(__file__)),
                 'sources_modified': False, 'training_or_controllers_restarted': False,
                 'historical_evidence_overwritten': False,
                 'completion_claimed': False,
                 'interpretation': 'Each alias resolves only after the original controller writes its exact same-seed v8 record. No alternative file search or historical-result fallback.'}
        with receipt.open('x') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
        print(json.dumps(value))


if __name__ == '__main__':
    main()
