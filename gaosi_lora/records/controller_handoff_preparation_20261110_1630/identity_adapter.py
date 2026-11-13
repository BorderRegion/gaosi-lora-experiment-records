"""Verify an explicitly pinned handoff without changing original launch evidence."""
import hashlib
import json
import os
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def live(pid, ticks):
    try:
        tail = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return tail[0] not in ('Z', 'X') and tail[19] == ticks
    except (FileNotFoundError, ProcessLookupError):
        return False


def verify(root, original, receipt_name, receipt_sha256, runtime=True, fixture=False):
    """Return the allowed replacement identity and all evidence to capture.

    The caller must pin receipt_sha256 in its own immutable collector/review
    configuration. A receipt discovered only through a mutable job is not enough.
    runtime=False verifies captured bytes; symlink and /proc checks belong to the
    actual remote capture. It does not attest a current live process.
    """
    root = Path(root).resolve()
    receipt_path = (root / receipt_name).resolve()
    assert receipt_path.is_relative_to(root) and sha(receipt_path) == receipt_sha256
    receipt = read(receipt_path)
    assert receipt['status'] == 'EXPLICIT_IDLE_CONTROLLER_HANDOFF_LOGICAL_DEPENDENCY_PRESERVED'
    assert receipt['scope'] == ('ISOLATED_PROCESS_FIXTURE' if fixture else 'PREPARED_PRIMARY_IDLE_BOUNDARY_HANDOFF')
    assert receipt['numerical_leaf_signalled'] is False and receipt['scientific_review_bypassed'] is False
    record = receipt_path.parent
    planfile = record / 'plan.json'
    assert sha(planfile) == receipt['plan_sha256']
    plan = read(planfile)
    assert receipt['source_sha256'] == plan['source_sha256']
    assert receipt['boundary_evidence_sha256'] == plan['boundary_evidence_sha256']
    assert receipt['logical_job'] == original['name'] == plan['logical_job']
    assert receipt['replacement_job'] == plan['replacement_job']
    assert receipt['original_identity'] == {'wrapper': plan['old_wrapper'], 'child': plan['old_child']}
    assert original['wrapper_pid'] == plan['old_wrapper']['pid']
    assert original['child_pid'] == plan['old_child']['pid']
    assert original['child_proc_start_ticks'] == plan['old_child']['ticks']
    oldfile = record / 'old_job.json'
    assert sha(oldfile) == receipt['original_job_sha256'] == plan['old_job_sha256']
    old = read(oldfile)
    assert old['name'] == original['name'] and old['status'] == 'RUNNING'
    for key in ('wrapper_pid', 'child_pid', 'child_proc_start_ticks'):
        assert old[key] == original[key]
    assert sha(record / 'old_controller.log') == receipt['original_log_sha256']
    assert sha(root / 'records/logs' / (original['name'] + '.log')) == receipt['original_log_sha256']
    files = {receipt_path, planfile, oldfile, record / 'old_controller.log',
             root / 'records/logs' / (original['name'] + '.log')}
    for group in [plan['source_sha256'], plan['boundary_evidence_sha256']]:
        for name, expected in group.items():
            path = (root / name).resolve()
            assert path.is_relative_to(root) and sha(path) == expected, name
            files.add(path)
    intent = read(record / 'switch_intent.json')
    assert intent['plan_sha256'] == receipt['plan_sha256']
    assert receipt['replacement_identity'] == {'wrapper': intent['new_wrapper'], 'child': intent['new_child']}
    ready = read(record / 'gate_ready.json')
    assert ready['plan_sha256'] == receipt['plan_sha256']
    for key in ('pid', 'ticks', 'argv', 'cwd'):
        assert ready[key] == intent['new_child'][key]
    activation = read(record / 'activation.json')
    assert activation == {'pid': intent['new_child']['pid'], 'ticks': intent['new_child']['ticks'],
                          'plan_sha256': receipt['plan_sha256'], 'receipt_sha256': receipt_sha256}
    logical = root / 'records/jobs' / (original['name'] + '.json')
    physical = logical.with_name(plan['replacement_job'] + '.json')
    if runtime:
        assert logical.is_symlink() and os.readlink(logical) == physical.name
        assert logical.resolve() == physical.resolve()
        for expected in (plan['old_wrapper'], plan['old_child']):
            assert not live(expected['pid'], expected['ticks']), 'Original controller still live'
    state = read(logical)
    assert state['name'] == plan['replacement_job']
    assert state['child_pid'] == intent['new_child']['pid']
    assert state['child_proc_start_ticks'] == intent['new_child']['ticks']
    assert state['wrapper_pid'] == intent['new_wrapper']['pid']
    assert state['status'] in ('RUNNING', 'PASS', 'FAIL')
    phase = 'TERMINAL_WRAPPER_STATUS_RECORDED'
    if runtime and state['status'] == 'RUNNING':
        assert live(state['child_pid'], state['child_proc_start_ticks'])
        assert live(state['wrapper_pid'], intent['new_wrapper']['ticks'])
        proc = Path('/proc') / str(state['child_pid'])
        argv = [v.decode() for v in (proc / 'cmdline').read_bytes().split(b'\0') if v]
        assert argv in (plan['command'], intent['new_child']['argv']), 'Unexpected replacement command'
        assert b'MUSA_LAUNCH_BLOCKING=1' in (proc / 'environ').read_bytes().split(b'\0')
        phase = 'REPLACEMENT_QUEUE_RUNNING' if argv == plan['command'] else 'ACTIVATED_GATE_BEFORE_EXEC'
    elif state['status'] == 'RUNNING':
        phase = 'CAPTURED_RUNNING_STATE_NO_CURRENT_PROCESS_ATTESTATION'
    files.update({logical, physical, record / 'switch_intent.json', record / 'gate_ready.json',
                  record / 'activation.json', root / 'records/logs' / (plan['replacement_job'] + '.log')})
    return {'logical_name': original['name'], 'physical_name': plan['replacement_job'],
            'expected': dict(original, wrapper_pid=state['wrapper_pid'], child_pid=state['child_pid'],
                             child_proc_start_ticks=state['child_proc_start_ticks']),
            'receipt': receipt_name, 'receipt_sha256': receipt_sha256, 'phase': phase,
            'additional_files': sorted(str(p.relative_to(root)) for p in files)}
