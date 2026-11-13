"""Resume an existing immutable archive with verified chunks; never collect or train."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path('/home/ubuntu/diff/gaosi_lora')
HELPER = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
assert hashlib.sha256(HELPER.read_bytes()).hexdigest() == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
spec = importlib.util.spec_from_file_location('resilient_remote', HELPER)
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def fetch(relative, expected, directory):
    folder = ROOT / relative
    assert folder.parent == ROOT / 'records' and folder.name.startswith('pipeline_monitor_')
    archive = Path(str(folder) + '.tar.gz')
    directory.mkdir(exist_ok=False)
    prefix = archive.stat().st_size if archive.exists() else 0
    code = '''import hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);f=r/(p['relative']+'.tar.gz');data=f.read_bytes();n=p['prefix']
assert hashlib.sha256(data).hexdigest()==p['expected'] and 0<=n<=len(data)
print(json.dumps({'sha256':p['expected'],'bytes':len(data),'prefix_bytes':n,'prefix_sha256':hashlib.sha256(data[:n]).hexdigest(),'chunks':[{'offset':i,'size':len(data[i:i+262144]),'sha256':hashlib.sha256(data[i:i+262144]).hexdigest()} for i in range(n,len(data),262144)]}))
'''
    metadata = json.loads(transport.remote(code, {'relative': relative, 'expected': expected, 'prefix': prefix}))
    preserve(directory / 'manifest.json', metadata)
    if archive.exists():
        assert sha(archive) == metadata['prefix_sha256'], 'Existing prefix differs; preserve and stop'
        if prefix < metadata['bytes']:
            with (directory / 'partial_before_resume.tar.gz').open('xb') as target, archive.open('rb') as source:
                shutil.copyfileobj(source, target)
    else:
        with archive.open('xb'):
            pass
    for index, chunk in enumerate(metadata['chunks']):
        assert archive.stat().st_size == chunk['offset']
        code = '''import json,sys
from pathlib import Path
p=json.load(sys.stdin)
with (Path('/root/autodl-tmp/gaosi_lora')/(p['relative']+'.tar.gz')).open('rb') as f:
 f.seek(p['offset']);data=f.read(p['size'])
assert len(data)==p['size'];sys.stdout.buffer.write(data)
'''
        data = transport.remote(code, {'relative': relative, **chunk}, timeout=90)
        assert len(data) == chunk['size'] and hashlib.sha256(data).hexdigest() == chunk['sha256']
        with archive.open('ab') as handle:
            handle.write(data)
        preserve(directory / ('chunk_' + str(index) + '.json'), {**chunk, 'verified': True})
    assert archive.stat().st_size == metadata['bytes'] and sha(archive) == expected
    with tarfile.open(archive, 'r:gz') as handle:
        for member in handle.getmembers():
            target = (ROOT / member.name).resolve()
            assert member.isfile() and target.is_relative_to(folder)
            data = handle.extractfile(member).read()
            if target.exists():
                assert target.read_bytes() == data
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as destination:
                    destination.write(data)
    manifest = json.loads((folder / 'archive_source_sha256.json').read_text())
    for name, expected_source in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(folder) and sha(path) == expected_source, name
    result = json.loads((folder / 'result.json').read_text())
    preserve(directory / 'receipt.json', {'status': 'EXISTING_ARCHIVE_RESUMED_AND_SOURCES_REHASHED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'observation': relative,
             'archive_sha256': sha(archive), 'archive_bytes': metadata['bytes'], 'preserved_prefix_bytes': prefix,
             'verified_source_files': len(manifest), 'collector_resubmitted': False,
             'training_modified_or_restarted': False})
    return result, len(manifest)
