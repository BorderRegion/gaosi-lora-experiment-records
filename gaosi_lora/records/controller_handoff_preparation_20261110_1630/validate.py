"""Exercise real isolated Linux processes, unchanged job.py and wait_for code."""
import argparse
import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('isolated_handoff', HERE / 'handoff.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


def wait_until(check, seconds=10):
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, 'Fixture deadline expired'
        time.sleep(.02)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handoff.preserve(path, value)


def run_case(parent, mode):
    root = parent / mode
    record = root / 'records/handoff'
    for path in [record, root / 'records/jobs', root / 'records/logs', root / 'tools']:
        path.mkdir(parents=True, exist_ok=True)
    for name in ['job.py', 'device_guard.py']:
        shutil.copyfile(ROOT / 'tools' / name, root / 'tools' / name)
    source = record / 'handoff.py'
    shutil.copyfile(HERE / 'handoff.py', source)
    adapter_file = record / 'identity_adapter.py'
    shutil.copyfile(HERE / 'identity_adapter.py', adapter_file)
    old_script = root / 'tools/old_controller.py'
    old_script.write_text("import json,time\nprint(json.dumps({'event':'completed_meta_waiting_for_agent_scientific_review','run':'runs/completed_leaf'}),flush=True)\nwhile True:time.sleep(.1)\n")
    queue = root / 'tools/replacement_queue.py'
    code = 7 if mode == 'queue_failure' else 0
    queue.write_text(f"from pathlib import Path\nimport json,sys,time\nPath('queue_invocations.jsonl').open('a').write(json.dumps({{'fixture':'NO_MODEL_OR_TRAINING'}})+'\\n')\nwhile not Path('release_queue.json').exists():time.sleep(.02)\nif {code}==0:Path('endpoint.json').write_text(json.dumps({{'status':'FIXTURE_DONE'}}))\nsys.exit({code})\n")
    leaf = root / 'runs/completed_leaf'
    write(leaf / 'result.json', {'status': 'PASS', 'inner_updates': 512,
                                'checkpoint': 'runs/completed_leaf/latest.pt', 'scope': 'FIXTURE_ONLY'})
    write(root / 'records/jobs/completed_leaf.json', {'status': 'PASS', 'scope': 'FIXTURE_ONLY'})
    (leaf / 'latest.pt').write_bytes(b'NOT_A_CHECKPOINT_NO_MODEL_OR_TRAINING')
    old_log = root / 'records/logs/old_logical.log'
    env = dict(os.environ, MUSA_LAUNCH_BLOCKING='1')
    with old_log.open('xb') as log:
        old = subprocess.Popen([sys.executable, '-u', 'tools/job.py', '--name', 'old_logical', '--',
                                sys.executable, '-u', str(old_script)], cwd=root, env=env,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    original_cwd = Path.cwd()
    waiter = None
    try:
        old_job = root / 'records/jobs/old_logical.json'
        wait_until(lambda: old_job.exists() and 'child_pid' in handoff.read(old_job) and old_log.stat().st_size > 0)
        old_state = handoff.read(old_job)
        old_child = handoff.identity(old_state['child_pid'])
        plan = {'scope': 'ISOLATED_PROCESS_FIXTURE', 'root': str(root), 'record': 'records/handoff',
                'logical_job': 'old_logical', 'replacement_job': 'replacement_v1',
                'command': [sys.executable, '-u', str(queue)],
                'old_job_sha256': handoff.sha(old_job), 'old_child': old_child,
                'old_wrapper': handoff.identity(old.pid),
                'boundary': {'leaf': 'completed_leaf', 'inner_updates': 512,
                             'review': 'records/review.json'},
                'boundary_evidence_sha256': {str(p.relative_to(root)): handoff.sha(p) for p in
                                             [leaf / 'result.json', root / 'records/jobs/completed_leaf.json']},
                'required_absent': ['endpoint.json', 'records/review.json'],
                'source_sha256': {str(p.relative_to(root)): handoff.sha(p)
                                  for p in [source, adapter_file, queue, root / 'tools/job.py', root / 'tools/device_guard.py']}}
        if mode == 'wrong_ticks':
            plan['old_child']['ticks'] = str(int(plan['old_child']['ticks']) + 1)
        if mode == 'unreleased_boundary':
            write(root / 'records/review.json', {'status': 'FIXTURE_ALREADY_RELEASED'})
        planfile = record / 'plan.json'
        write(planfile, plan)
        isolated = importlib.util.spec_from_file_location('fixture_' + mode, source)
        module = importlib.util.module_from_spec(isolated)
        isolated.loader.exec_module(module)
        os.chdir(root)
        if mode in ('wrong_ticks', 'unreleased_boundary'):
            try:
                module.switch(planfile)
            except AssertionError:
                pass
            else:
                raise AssertionError('Invalid preflight accepted')
            assert not (record / 'launch_intent.json').exists()
            assert not old_job.is_symlink() and handoff.sha(old_job) == plan['old_job_sha256']
            assert handoff.identity(old.pid)['state'] not in ('T', 't', 'Z', 'X')
            assert handoff.identity(old_state['child_pid'])['state'] not in ('T', 't', 'Z', 'X')
            return {'case': mode, 'rejected_before_any_signal_or_launch': True}
        # Use the exact original downstream wait_for function, with only its
        # 30-second polling sleep shortened in the isolated fixture.
        source_queue = ROOT / 'records/pipeline_observed_blocking_v8/tools/organization_queue.py'
        tree = ast.parse(source_queue.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'wait_for')
        waiter_script = root / 'tools/downstream_waiter.py'
        waiter_script.write_text("import json,time\nfrom pathlib import Path\nclass Clock:\n @staticmethod\n def sleep(seconds):time.sleep(.02)\n" +
                                 ast.unparse(function).replace('time.sleep(30)', 'Clock.sleep(30)') +
                                 "\nvalue=wait_for('endpoint.json','old_logical')\nPath('downstream_result.json').write_text(json.dumps(value))\n")
        waiter = subprocess.Popen([sys.executable, str(waiter_script)], cwd=root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pause = {'before_alias': 'before_alias', 'after_alias': 'after_alias'}.get(mode)
        receipt = module.switch(planfile, interrupt_at=pause)
        if pause:
            assert not (root / 'queue_invocations.jsonl').exists()
            assert not (record / 'activation.json').exists()
            assert waiter.poll() is None
            if pause == 'before_alias':
                assert not old_job.is_symlink()
                assert handoff.identity(old.pid)['state'] not in ('T', 't', 'Z', 'X')
            else:
                assert old_job.is_symlink()
                assert handoff.identity(old.pid)['state'] in ('T', 't')
                assert handoff.identity(old_state['child_pid'])['state'] in ('T', 't')
            receipt = module.finish(planfile)
        assert receipt['numerical_leaf_signalled'] is False and receipt['scientific_review_bypassed'] is False
        new_job = root / 'records/jobs/replacement_v1.json'
        wait_until(lambda: (root / 'queue_invocations.jsonl').exists())
        adapter_spec = importlib.util.spec_from_file_location('adapter_' + mode, adapter_file)
        adapter = importlib.util.module_from_spec(adapter_spec)
        adapter_spec.loader.exec_module(adapter)
        original = dict(name=plan['logical_job'], wrapper_pid=old.pid,
                        child_pid=old_state['child_pid'], child_proc_start_ticks=old_state['child_proc_start_ticks'])
        receipt_path = record / 'handoff_receipt.json'
        running_map = adapter.verify(root, original, 'records/handoff/handoff_receipt.json',
                                     handoff.sha(receipt_path), fixture=True)
        assert running_map['phase'] == 'REPLACEMENT_QUEUE_RUNNING'
        write(root / 'release_queue.json', {'scope': 'FIXTURE_ONLY'})
        wait_until(lambda: handoff.read(new_job)['status'] != 'RUNNING')
        assert old_job.is_symlink() and old_job.resolve() == new_job
        assert handoff.sha(record / 'old_job.json') == plan['old_job_sha256']
        assert handoff.read(record / 'old_job.json')['status'] == 'RUNNING'
        assert handoff.sha(record / 'old_controller.log') == handoff.sha(old_log)
        for name, expected in plan['source_sha256'].items():
            assert handoff.sha(root / name) == expected
        assert old.wait(timeout=5) == -signal.SIGKILL
        final = handoff.read(old_job)
        assert final['status'] == ('FAIL' if code else 'PASS') and final['exit_code'] == code
        assert len((root / 'queue_invocations.jsonl').read_text().splitlines()) == 1
        assert module.finish(planfile) == receipt  # Recovery never relaunches.
        mapped = adapter.verify(root, original, 'records/handoff/handoff_receipt.json',
                                handoff.sha(receipt_path), fixture=True)
        assert mapped['expected']['child_pid'] == final['child_pid']
        assert mapped['physical_name'] == 'replacement_v1'
        try:
            adapter.verify(root, original, 'records/handoff/handoff_receipt.json', '0' * 64, fixture=True)
        except AssertionError:
            pass
        else:
            raise AssertionError('Unpinned receipt was accepted')
        wrong_original = dict(original, child_proc_start_ticks='0')
        try:
            adapter.verify(root, wrong_original, 'records/handoff/handoff_receipt.json',
                           handoff.sha(receipt_path), fixture=True)
        except AssertionError:
            pass
        else:
            raise AssertionError('Receipt for a different original controller was accepted')
        old_job.unlink()
        old_job.symlink_to('completed_leaf.json')
        try:
            adapter.verify(root, original, 'records/handoff/handoff_receipt.json',
                           handoff.sha(receipt_path), fixture=True)
        except AssertionError:
            pass
        else:
            raise AssertionError('Wrong logical-job alias was accepted')
        finally:
            old_job.unlink()
            old_job.symlink_to('replacement_v1.json')
        if code:
            assert waiter.communicate(timeout=5)[1] and waiter.returncode != 0
            assert not (root / 'downstream_result.json').exists()
        else:
            output, error = waiter.communicate(timeout=5)
            assert waiter.returncode == 0, error
            assert handoff.read(root / 'downstream_result.json')['status'] == 'FIXTURE_DONE'
        return {'case': mode, 'logical_dependency_resolved': True, 'exit_status_propagated': final['status'],
                'old_evidence_preserved': True, 'queue_invocations': 1,
                'pinned_identity_adapter_passed': True, 'invalid_identity_adapter_inputs_rejected': 3,
                'interrupted_switch_recovered': bool(pause), 'model_or_training_executed': False}
    finally:
        os.chdir(original_cwd)
        if waiter is not None and waiter.poll() is None:
            waiter.kill()
            waiter.wait(timeout=5)
        # Only identities created inside this temporary fixture can be killed.
        for jobfile in (root / 'records/jobs').glob('*.json'):
            state = handoff.read(jobfile)
            for key in ('child_pid', 'wrapper_pid'):
                actual = handoff.identity(state.get(key)) if state.get(key) else None
                if actual and actual['cwd'] == str(root) and actual['state'] not in ('Z', 'X'):
                    os.kill(actual['pid'], signal.SIGKILL)
        old.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    assert output.is_relative_to(HERE) and not output.exists()
    with tempfile.TemporaryDirectory(prefix='gaosi_controller_handoff_') as directory:
        cases = [run_case(Path(directory), mode) for mode in
                 ['success', 'before_alias', 'after_alias', 'queue_failure', 'wrong_ticks', 'unreleased_boundary']]
    result = {'status': 'PASS', 'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope': 'real isolated Linux processes and unmodified production job wrapper; no model or training',
              'cases': cases, 'source_sha256': {str(p.relative_to(ROOT)): handoff.sha(p)
                                              for p in HERE.glob('*.py')},
              'production_reference_sha256': {p: handoff.sha(ROOT / p) for p in
                  ['tools/job.py', 'tools/device_guard.py',
                   'records/pipeline_observed_blocking_v8/tools/organization_queue.py']},
              'production_plan_created': False, 'production_handoff_activated': False,
              'requires_before_production': 'Actual completed-leaf audit and pinned boundary plan, monitor identity adapter and explicit observer handoff',
              'method_validated': False, 'full_goal_complete': False}
    handoff.preserve(output, result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
