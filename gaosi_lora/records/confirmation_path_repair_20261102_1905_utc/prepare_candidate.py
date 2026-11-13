"""Preserve and validate a metadata-only repair; never edit live sources."""
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(data)


def main():
    changes = {}
    for name in ['build_observed_v8_pipeline.py', 'confirmation_queue.py']:
        source = (ROOT / 'tools' / name if name.startswith('build_') else
                  ROOT / 'records/pipeline_observed_blocking_v8/tools' / name)
        before = source.read_bytes()
        text = before.decode()
        if name.startswith('build_'):
            anchor = "        ('_s{seed}_v1', '_s{seed}_observed_blocking_v8'),\n"
            assert text.count(anchor) == 1
            after = text.replace(anchor, anchor +
                                 "        ('_s{s}_v1', '_s{s}_observed_blocking_v8'),\n").encode()
        else:
            assert text.count('_ready_s{s}_v1.json') == 2
            after = text.replace('_ready_s{s}_v1.json',
                                 '_ready_s{s}_observed_blocking_v8.json').encode()
        write_new(HERE / 'original' / name, before)
        write_new(HERE / 'candidate' / name, after)
        diff = ''.join(difflib.unified_diff(before.decode().splitlines(True),
                                          after.decode().splitlines(True),
                                          fromfile=str(source.relative_to(ROOT)),
                                          tofile='candidate/' + name))
        write_new(HERE / 'diffs' / (name + '.diff'), diff.encode())
        changes[str(source.relative_to(ROOT))] = {
            'before_sha256': hashlib.sha256(before).hexdigest(),
            'candidate_sha256': hashlib.sha256(after).hexdigest(),
        }
    manifest = {
        'status': 'ISOLATED_CANDIDATE_NOT_ACTIVATED',
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'changes': changes,
        'problem': 'Generated per-seed writers use observed_blocking_v8; final manifest uses v1.',
        'affected_consumers': ['recovery', 'selective', 'reflow', 'continual'],
        'live_training_modified_or_restarted': False,
        'live_controllers_modified_or_restarted': False,
        'historical_evidence_overwritten': False,
        'activation_pending': 'A running Python main retains its original code. Disk edits alone cannot repair it.',
        'forbidden_workaround': 'Do not silently select existing legacy v1 results or alias historical records.',
        'previous_goal_turn_classification': 'no progress: status response only; current turn prepares and tests a repair.',
    }
    write_new(HERE / 'candidate_manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
