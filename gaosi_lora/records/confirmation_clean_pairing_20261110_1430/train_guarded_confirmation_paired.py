"""Use the existing evidence guard with the versioned paired-stage validator."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(HERE))
import train_guarded_meta as original
from confirmation_runner_corrected import check_config

_original_pins = original.pinned_source_files
_original_check = original.check_original_entry


def pinned_source_files():
    return _original_pins() | {
        str((HERE / name).relative_to(ROOT)) for name in [
            'confirmation_runner_corrected.py', 'confirmation_queue_corrected.py',
            'guarded_pipeline_corrected.py', 'train_guarded_confirmation_paired.py']}


def check_entry(config, entry):
    if entry == 'confirmation':
        check_config(config)
    else:
        _original_check(config, entry)


original.pinned_source_files = pinned_source_files
original.check_original_entry = check_entry
digest, prepare, LOCK = original.digest, original.prepare, original.LOCK

if __name__ == '__main__':
    original.main()
