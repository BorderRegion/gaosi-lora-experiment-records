"""Reclaim duplicate local snapshot blocks while preserving every evidence path.

Only immutable snapshots captured before 2026-11-10 are eligible. No original
training file, active monitor, archive, review or mutable ledger is rewritten.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path('/home/ubuntu/diff/gaosi_lora')
HERE = Path(__file__).resolve().parent
JOURNAL = HERE / 'deduplication.jsonl'
PATTERN = re.compile(r'(pipeline_monitor|development_monitor|horizon_effect_monitor|diagnostic_pair_monitor)_(\d{8})_\d{6}_utc')
TARGET_BYTES = 2 * 1024**3


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    assert not JOURNAL.exists()
    available_before = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize
    groups, candidates = {}, []
    for capture in sorted((ROOT / 'records').iterdir()):
        match = PATTERN.fullmatch(capture.name)
        if not match or match[2] >= '20261110' or not capture.is_dir() or capture.is_symlink():
            continue
        snapshot = capture / 'snapshot'
        if not snapshot.is_dir():
            continue
        for path in snapshot.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_size >= 16384:
                candidates.append((stat.st_size, path))
    candidates.sort(key=lambda row: (-row[0], str(row[1])))
    replaced, reclaimed = 0, 0
    with JOURNAL.open('x') as journal:
        for size, path in candidates:
            stat = path.stat()
            digest = sha(path)
            key = (size, digest)
            if key not in groups:
                groups[key] = path
                continue
            canonical = groups[key]
            master_stat = canonical.stat()
            if (stat.st_dev, stat.st_ino) == (master_stat.st_dev, master_stat.st_ino):
                continue
            assert stat.st_nlink == 1 and stat.st_dev == master_stat.st_dev
            assert sha(canonical) == digest
            assert canonical.read_bytes() == path.read_bytes()
            temporary = path.with_name('.gaosi_identical_snapshot_' + str(os.getpid()))
            assert not temporary.exists()
            entry = {'path': str(path.relative_to(ROOT)),
                     'canonical': str(canonical.relative_to(ROOT)),
                     'bytes': size, 'sha256': digest,
                     'old_inode': stat.st_ino, 'canonical_inode': master_stat.st_ino,
                     'old_allocated_bytes': stat.st_blocks * 512}
            journal.write(json.dumps({'intent': entry}) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            os.link(canonical, temporary, follow_symlinks=False)
            os.replace(temporary, path)
            assert path.stat().st_ino == master_stat.st_ino
            assert sha(path) == digest
            replaced += 1
            reclaimed += stat.st_blocks * 512
            journal.write(json.dumps({'completed': entry}) + '\n')
            journal.flush()
            if reclaimed >= TARGET_BYTES:
                break
    stat = os.statvfs(ROOT)
    result = {'status': 'IDENTICAL_IMMUTABLE_CAPTURE_FILES_HARDLINKED_WITH_ALL_PATHS_AND_BYTES_PRESERVED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'eligible_candidates': len(candidates), 'replaced_duplicate_files': replaced,
              'old_duplicate_allocated_bytes_reclaimed': reclaimed,
              'available_before_bytes': available_before,
              'available_after_bytes': stat.f_bavail * stat.f_frsize,
              'journal_sha256': sha(JOURNAL), 'source_sha256': sha(Path(__file__)),
              'scope': 'Local immutable monitor snapshot files captured before 2026-11-10 only.',
              'all_evidence_paths_and_file_bytes_preserved': True,
              'original_training_artifacts_modified': False,
              'remote_modified': False, 'method_validated': False,
              'full_goal_complete': False}
    with (HERE / 'deduplication_receipt.json').open('x') as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
