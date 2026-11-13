"""Fetch the completed CPU audit archive and independently rehash its text evidence."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preserve(path, data):
    if path.exists():
        assert path.read_bytes() == data, path
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(data)


def main():
    receiptpath = HERE / 'fetch_receipt.json'
    assert not receiptpath.exists()
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    transport.sync(Path(__file__))
    execution = json.loads((HERE / 'execution_receipt.json').read_text())
    archive = HERE / 'evidence.tar.gz'
    relative = str(archive.relative_to(ROOT))
    prefix = archive.stat().st_size if archive.exists() else 0
    code = '''import hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);data=(r/p['archive']).read_bytes();n=p['prefix']
assert 0<=n<=len(data)
print(json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'prefix_sha256':hashlib.sha256(data[:n]).hexdigest(),'chunks':[{'offset':i,'size':len(data[i:i+524288]),'sha256':hashlib.sha256(data[i:i+524288]).hexdigest()} for i in range(n,len(data),524288)]}))
'''
    metadata = json.loads(transport.remote(code, {'archive': relative, 'prefix': prefix}))
    assert metadata['sha256'] == execution['archive_sha256']
    if archive.exists():
        assert sha(archive) == metadata['prefix_sha256']
    else:
        with archive.open('xb'):
            pass
    code = '''import json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
with (r/p['archive']).open('rb') as f:
 f.seek(p['offset']);data=f.read(p['size'])
assert len(data)==p['size'];sys.stdout.buffer.write(data)
'''
    for chunk in metadata['chunks']:
        assert archive.stat().st_size == chunk['offset']
        data = transport.remote(code, {'archive': relative, **chunk}, timeout=90)
        assert len(data) == chunk['size'] and hashlib.sha256(data).hexdigest() == chunk['sha256']
        with archive.open('ab') as handle:
            handle.write(data)
    assert archive.stat().st_size == metadata['bytes'] and sha(archive) == execution['archive_sha256']
    extract = HERE / 'evidence'
    with tarfile.open(archive, 'r:gz') as handle:
        members = handle.getmembers()
        for member in members:
            target = (extract / member.name).resolve()
            assert member.isfile() and target.is_relative_to(extract / 'snapshot')
            preserve(target, handle.extractfile(member).read())
    audit_bytes = (extract / 'snapshot' / HERE.relative_to(ROOT) / 'audit.json').read_bytes()
    assert hashlib.sha256(audit_bytes).hexdigest() == execution['audit_sha256']
    preserve(HERE / 'audit.json', audit_bytes)
    audit = json.loads(audit_bytes)
    verified, tensors = [], []
    for name, expected in audit['evidence_sha256'].items():
        if Path(name).suffix == '.pt':
            tensors.append(name)
            continue
        assert sha(extract / 'snapshot' / name) == expected, name
        verified.append(name)
    assert len(members) == execution['non_tensor_files'] == len(verified) + 1
    value = {'status': 'SECOND_O4_PARENT_AUDIT_FETCHED_AND_NON_TENSORS_INDEPENDENTLY_REHASHED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'archive_sha256': sha(archive), 'audit_sha256': sha(HERE / 'audit.json'),
             'non_tensor_evidence_verified': len(verified), 'tensor_paths_retained_remote': tensors,
             'prediction_files': len(audit['prediction_reviews']),
             'prediction_rows': sum(item['rows'] for item in audit['prediction_reviews']),
             'fetch_source_sha256': sha(Path(__file__)),
             'method_validated': False, 'full_goal_complete': False}
    with receiptpath.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')
    transport.sync(receiptpath)
    print(json.dumps(value))


if __name__ == '__main__':
    main()
