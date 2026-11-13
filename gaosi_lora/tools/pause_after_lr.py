"""Drain the last LR task before choosing a measurable rule-development family."""
import json
import os
from pathlib import Path
import signal
import time


def live(state):
    proc = Path(f"/proc/{state['child_pid']}/stat")
    if not proc.exists():
        return False
    fields = proc.read_text().split()
    return fields[21] == state['child_proc_start_ticks'] and fields[2] != 'Z'


def main():
    dispatcher_file = Path('records/jobs/development_queue_v3.json')
    last_file = Path('records/jobs/capacity_3b_lrdev_rules_lr0.0003_s17_v2.json')
    while not last_file.exists():
        dispatcher = json.loads(dispatcher_file.read_text())
        if not live(dispatcher):
            raise RuntimeError('Dispatcher exited before the planned scientific review boundary')
        time.sleep(30)
    dispatcher = json.loads(dispatcher_file.read_text())
    if dispatcher['status'] != 'RUNNING' or not live(dispatcher):
        raise RuntimeError('Dispatcher is not live at the last LR task')
    pid = dispatcher['child_pid']
    os.kill(pid, signal.SIGSTOP)
    print('LR_REVIEW_DISPATCHER_PAUSED', pid, flush=True)
    state = json.loads(last_file.read_text())
    while state['status'] == 'RUNNING':
        time.sleep(30)
        state = json.loads(last_file.read_text())
    review = {'status': 'READY_FOR_LR_AND_RULE_REVIEW', 'dispatcher_pid': pid,
              'dispatcher_start_ticks': dispatcher['child_proc_start_ticks'],
              'last_job_status': state['status'],
              'reason': 'Review all three development learning rates and rule floor before expanding five capacity settings. No training subprocess was interrupted.'}
    Path('records/lr_review_boundary.json').write_text(json.dumps(review, indent=2))
    print(json.dumps(review), flush=True)


if __name__ == '__main__':
    main()
