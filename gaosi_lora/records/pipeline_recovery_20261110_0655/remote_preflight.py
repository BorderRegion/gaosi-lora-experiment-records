"""Read-only identity and capture-existence checks after a local disk failure."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path('/root/autodl-tmp/gaosi_lora')
rows = []
for name, pid, ticks in [
    ('organization_queue_observed_blocking_v8', 761616, '80739115'),
    ('phi_mix_O1_X_specialize512_s17_observed_blocking_v8', 68659, '154509185'),
    ('horizon_uniform_initial_gate_observed_blocking_v8', 48797, '154082318'),
    ('horizon_known_K4_H2_uniform_s17_observed_blocking_v8', 70260, '154541867'),
]:
    job = json.loads((ROOT / 'records/jobs' / (name + '.json')).read_text())
    assert job['status'] == 'RUNNING' and job['child_pid'] == pid
    assert job['child_proc_start_ticks'] == ticks
    proc = Path('/proc') / str(pid)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[0] not in ('Z', 'X') and fields[19] == ticks, name
    blocking = [s.decode() for s in (proc / 'environ').read_bytes().split(b'\0')
                if s == b'MUSA_LAUNCH_BLOCKING=1']
    assert blocking == ['MUSA_LAUNCH_BLOCKING=1'], name
    rows.append({'name': name, 'pid': pid, 'start_ticks': ticks, 'state': fields[0],
                 'wchan': (proc / 'wchan').read_text(), 'blocking': blocking,
                 'argv_sha256': hashlib.sha256((proc / 'cmdline').read_bytes()).hexdigest()})
lock = Path('/tmp/gaosi_musa_device0.lock')
assert lock.stat().st_ino == 31148027670
capture = ROOT / 'records/pipeline_monitor_20261110_065036_utc'
stat = os.statvfs(ROOT)
print(json.dumps({'checked_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'active_processes': rows, 'gpu_lock_inode': lock.stat().st_ino,
                  'remote_available_bytes': stat.f_bavail * stat.f_frsize,
                  'due_capture': str(capture.relative_to(ROOT)),
                  'due_capture_directory_exists': capture.exists(),
                  'due_capture_archive_exists': Path(str(capture) + '.tar.gz').exists(),
                  'training_or_controllers_modified': False}))
