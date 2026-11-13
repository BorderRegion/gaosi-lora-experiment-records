"""Preserve completed development results across the two existing disks."""
import hashlib
import json
from pathlib import Path
import shutil
import time


def preserve_space(required_free=12 << 30):
    data = Path('/root/autodl-tmp')
    archive = Path('/root/gaosi_completed_runs')
    archive.mkdir(exist_ok=True)
    events = []
    for run in sorted(Path('runs').iterdir(), key=lambda p: p.stat().st_mtime):
        if shutil.disk_usage(data).free >= required_free:
            break
        if run.is_symlink() or not (run / 'result.json').exists():
            continue
        if not run.name.startswith(('capacity_', 'factor_lr_', 'rslora_')):
            continue
        size = sum(p.stat().st_size for p in run.rglob('*') if p.is_file())
        if shutil.disk_usage(archive).free < size + (3 << 30):
            break
        destination = archive / run.name
        if destination.exists():
            raise RuntimeError('Archive destination exists; inspect before moving')
        original = run.absolute()
        shutil.move(str(run), str(destination))
        original.symlink_to(destination, target_is_directory=True)
        event = {'epoch': time.time(), 'action': 'move_completed_run_preserving_path',
                 'original': str(original), 'archive': str(destination), 'bytes': size,
                 'deleted_results': False}
        with Path('records/storage_moves.jsonl').open('a') as f:
            f.write(json.dumps(event) + '\n')
        events.append(event)
    if shutil.disk_usage(data).free < 2 << 30:
        raise RuntimeError('Existing disks nearly full; preserve results and stop before new allocation')
    return events
