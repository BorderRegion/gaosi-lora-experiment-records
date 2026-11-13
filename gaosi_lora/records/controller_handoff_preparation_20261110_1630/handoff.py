"""Explicit idle-boundary controller replacement; no numerical leaf is signalled.

Requires a separately reviewed, immutable plan. No production plan is supplied.
The original job JSON is archived, then becomes a symlink to a new standard
job.py wrapper. Downstream readers retain their original dependency path.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def preserve(path, value):
    path = Path(path)
    content = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    if path.exists():
        assert path.read_bytes() == content, str(path)
    else:
        temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()) + '.' + str(time.time_ns()))
        with temporary.open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            assert path.read_bytes() == content, str(path)
        finally:
            temporary.unlink()


def identity(pid):
    proc = Path('/proc') / str(pid)
    try:
        fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': int(pid), 'ticks': fields[19], 'state': fields[0], 'ppid': int(fields[1]),
                'argv': [v.decode() for v in (proc / 'cmdline').read_bytes().split(b'\0') if v],
                'cwd': str((proc / 'cwd').resolve()),
                'children': [int(v) for v in (proc / 'task' / str(pid) / 'children').read_text().split()]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def match(expected, allow_terminal=False):
    actual = identity(expected['pid'])
    if actual is None or actual['state'] in ('Z', 'X'):
        assert allow_terminal, 'Expected process is not live'
        return None
    assert all(actual[k] == expected[k] for k in ('pid', 'ticks', 'argv', 'cwd')), 'Process identity differs'
    return actual


def checked_plan(path):
    path = Path(path).resolve()
    plan = read(path)
    root = Path(plan['root']).resolve()
    record = root / plan['record']
    assert Path.cwd().resolve() == root and path == record / 'plan.json'
    assert plan['logical_job'] != plan['replacement_job']
    assert all('/' not in plan[k] and plan[k] not in ('', '.', '..')
               for k in ('logical_job', 'replacement_job'))
    assert plan['scope'] in ('PREPARED_PRIMARY_IDLE_BOUNDARY_HANDOFF', 'ISOLATED_PROCESS_FIXTURE')
    for name, expected in plan['source_sha256'].items():
        source = (root / name).resolve()
        assert source.is_relative_to(root) and sha(source) == expected, name
    assert str(Path(__file__).resolve().relative_to(root)) in plan['source_sha256']
    assert 'tools/job.py' in plan['source_sha256'] and 'tools/device_guard.py' in plan['source_sha256']
    assert any(str(root / name) in plan['command'] for name in plan['source_sha256']), 'Queue command is not pinned'
    return plan, root, record


def preflight(path):
    plan, root, record = checked_plan(path)
    logical = root / 'records/jobs' / (plan['logical_job'] + '.json')
    replacement = logical.with_name(plan['replacement_job'] + '.json')
    assert not logical.is_symlink() and sha(logical) == plan['old_job_sha256']
    old = read(logical)
    assert old['name'] == plan['logical_job'] and old['status'] == 'RUNNING'
    assert old['child_pid'] == plan['old_child']['pid']
    assert old['child_proc_start_ticks'] == plan['old_child']['ticks']
    assert old['wrapper_pid'] == plan['old_wrapper']['pid']
    child, wrapper = match(plan['old_child']), match(plan['old_wrapper'])
    assert not child['children'] and child['ppid'] == wrapper['pid']
    assert wrapper['children'] == [child['pid']], 'Controller has an active subprocess'
    assert not replacement.exists() and not (record / 'launch_intent.json').exists()
    boundary = plan['boundary']
    leaf_job = root / 'records/jobs' / (boundary['leaf'] + '.json')
    resultfile = root / 'runs' / boundary['leaf'] / 'result.json'
    assert plan['boundary_evidence_sha256'].get(str(leaf_job.relative_to(root))) == sha(leaf_job)
    assert plan['boundary_evidence_sha256'].get(str(resultfile.relative_to(root))) == sha(resultfile)
    for name, expected in plan['boundary_evidence_sha256'].items():
        assert sha(root / name) == expected, name
    result = read(resultfile)
    assert read(leaf_job)['status'] == result['status'] == 'PASS'
    assert result['inner_updates'] == boundary['inner_updates']
    assert result['checkpoint'] == 'runs/' + boundary['leaf'] + '/latest.pt'
    assert not (root / boundary['review']).exists(), 'Do not race scientific release'
    log = root / 'records/logs' / (plan['logical_job'] + '.log')
    last = json.loads(log.read_text().splitlines()[-1])
    assert last['event'] == 'completed_meta_waiting_for_agent_scientific_review'
    assert last['run'] == 'runs/' + boundary['leaf']
    for name in plan['required_absent']:
        assert not (root / name).exists(), name
    return plan, root, record


def gate(path):
    plan, root, record = checked_plan(path)
    own = identity(os.getpid())
    preserve(record / 'gate_ready.json', {'pid': own['pid'], 'ticks': own['ticks'],
                                         'argv': own['argv'], 'cwd': own['cwd'],
                                         'plan_sha256': sha(path)})
    while not (record / 'activation.json').exists():
        time.sleep(.1)
    activation = read(record / 'activation.json')
    assert activation['pid'] == own['pid'] and activation['ticks'] == own['ticks']
    assert activation['plan_sha256'] == sha(path)
    assert activation['receipt_sha256'] == sha(record / 'handoff_receipt.json')
    logical = root / 'records/jobs' / (plan['logical_job'] + '.json')
    replacement = logical.with_name(plan['replacement_job'] + '.json')
    assert logical.is_symlink() and logical.resolve() == replacement.resolve()
    state = read(logical)
    assert state['name'] == plan['replacement_job'] and state['child_pid'] == own['pid']
    assert state['child_proc_start_ticks'] == own['ticks'] and state['status'] == 'RUNNING'
    checked_plan(path)
    # exec keeps the job.py-recorded PID and start ticks. job.py records the
    # actual eventual queue exit code; no handoff operation writes a fake PASS.
    os.execvpe(plan['command'][0], plan['command'], os.environ)


def stop_exact(expected):
    actual = match(expected)
    if actual['state'] not in ('T', 't'):
        os.kill(expected['pid'], signal.SIGSTOP)
    deadline = time.monotonic() + 3
    while match(expected)['state'] not in ('T', 't'):
        assert time.monotonic() < deadline, 'Stop not confirmed; inspect the same process'
        time.sleep(.01)


def kill_stopped(expected):
    actual = match(expected, allow_terminal=True)
    if actual is not None:
        assert actual['state'] in ('T', 't') and not actual['children']
        os.kill(expected['pid'], signal.SIGKILL)


def finish(path, interrupt_at=None):
    """Recover only the recorded staged processes; never relaunch a queue."""
    plan, root, record = checked_plan(path)
    intent = read(record / 'switch_intent.json')
    assert intent['plan_sha256'] == sha(path)
    logical = root / 'records/jobs' / (plan['logical_job'] + '.json')
    replacement = logical.with_name(plan['replacement_job'] + '.json')
    if (record / 'activation.json').exists():
        assert logical.is_symlink() and logical.resolve() == replacement.resolve()
        assert read(record / 'activation.json')['receipt_sha256'] == sha(record / 'handoff_receipt.json')
        return read(record / 'handoff_receipt.json')
    match(intent['new_child'])
    assert read(replacement)['status'] == 'RUNNING'
    assert not (root / plan['boundary']['review']).exists()
    if not logical.is_symlink():
        assert sha(logical) == plan['old_job_sha256']
        assert not match(plan['old_child'])['children']
        stop_exact(plan['old_child'])
        stop_exact(plan['old_wrapper'])
        assert match(plan['old_wrapper'])['children'] == [plan['old_child']['pid']]
        for name, source in [('old_job.json', logical), ('old_controller.log',
                             root / 'records/logs' / (plan['logical_job'] + '.log'))]:
            destination = record / name
            if destination.exists():
                assert destination.read_bytes() == source.read_bytes()
            else:
                with destination.open('xb') as handle:
                    handle.write(source.read_bytes())
        temporary = logical.with_name(logical.name + '.handoff')
        if not temporary.is_symlink():
            temporary.symlink_to(replacement.name)
        assert temporary.resolve() == replacement.resolve()
        temporary.replace(logical)
    assert logical.resolve() == replacement.resolve()
    if interrupt_at == 'after_alias':
        return {'status': 'FIXTURE_INTERRUPTED_AFTER_ALIAS_BEFORE_RETIREMENT'}
    # The old wrapper must not run its finally block and overwrite the alias.
    # Its original RUNNING state and partial wall-time provenance are archived.
    wrapper = match(plan['old_wrapper'], allow_terminal=True)
    if wrapper is not None:
        assert wrapper['state'] in ('T', 't')
        assert wrapper['children'] == [plan['old_child']['pid']]
        os.kill(wrapper['pid'], signal.SIGKILL)
    kill_stopped(plan['old_child'])
    receipt = {'status': 'EXPLICIT_IDLE_CONTROLLER_HANDOFF_LOGICAL_DEPENDENCY_PRESERVED',
               'scope': plan['scope'], 'plan_sha256': sha(path),
               'logical_job': plan['logical_job'], 'replacement_job': plan['replacement_job'],
               'original_identity': {'wrapper': plan['old_wrapper'], 'child': plan['old_child']},
               'replacement_identity': {'wrapper': intent['new_wrapper'], 'child': intent['new_child']},
               'original_job_sha256': sha(record / 'old_job.json'),
               'original_log_sha256': sha(record / 'old_controller.log'),
               'source_sha256': plan['source_sha256'],
               'boundary_evidence_sha256': plan['boundary_evidence_sha256'],
               'numerical_leaf_signalled': False, 'scientific_review_bypassed': False,
               'requires_monitor_identity_adapter': True, 'method_validated': False,
               'full_goal_complete': False}
    preserve(record / 'handoff_receipt.json', receipt)
    preserve(record / 'activation.json', {'pid': intent['new_child']['pid'],
                                         'ticks': intent['new_child']['ticks'],
                                         'plan_sha256': sha(path),
                                         'receipt_sha256': sha(record / 'handoff_receipt.json')})
    return receipt


def switch(path, interrupt_at=None):
    plan, root, record = preflight(path)
    preserve(record / 'launch_intent.json', {'plan_sha256': sha(path), 'submission_count': 1})
    command = [sys.executable, '-u', 'tools/job.py', '--name', plan['replacement_job'], '--',
               sys.executable, '-u', str(Path(__file__).resolve()), 'gate', '--plan', str(Path(path).resolve())]
    env = dict(os.environ, MUSA_LAUNCH_BLOCKING='1')
    log = root / 'records/logs' / (plan['replacement_job'] + '.log')
    with log.open('xb', buffering=0) as output:
        process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    preserve(record / 'staged_wrapper.json', {'pid': process.pid, 'submission_count': 1})
    job = root / 'records/jobs' / (plan['replacement_job'] + '.json')
    deadline = time.monotonic() + 15
    while True:
        if (record / 'gate_ready.json').exists() and job.exists():
            state = read(job)
            if state.get('child_pid') and state.get('child_proc_start_ticks'):
                break
        assert process.poll() is None, 'Staged gate exited; inspect without relaunching'
        assert time.monotonic() < deadline, 'Staged gate observation expired; inspect without relaunching'
        time.sleep(.02)
    new_child, new_wrapper = identity(state['child_pid']), identity(process.pid)
    ready = read(record / 'gate_ready.json')
    assert ready['pid'] == new_child['pid'] and ready['ticks'] == new_child['ticks']
    assert ready['plan_sha256'] == sha(path) and new_child['ppid'] == process.pid
    assert new_wrapper['children'] == [new_child['pid']] and not new_child['children']
    preserve(record / 'switch_intent.json', {'plan_sha256': sha(path), 'new_child': new_child,
                                           'new_wrapper': new_wrapper})
    if interrupt_at == 'before_alias':
        return {'status': 'FIXTURE_INTERRUPTED_WITH_STAGED_GATE_ONLY'}
    return finish(path, interrupt_at)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'switch', 'finish', 'gate'])
    parser.add_argument('--plan', required=True)
    args = parser.parse_args()
    if args.action == 'gate':
        gate(args.plan)
    elif args.action == 'preflight':
        preflight(args.plan)
        print(json.dumps({'status': 'IDLE_BOUNDARY_PREFLIGHT_PASS_NO_PROCESS_MODIFIED'}))
    else:
        print(json.dumps(globals()[args.action](args.plan)))


if __name__ == '__main__':
    main()
