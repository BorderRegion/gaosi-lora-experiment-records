"""Preserve a completed CPU audit and its small evidence files; never run training."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[2]
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']


def digest(data):
    return hashlib.sha256(data).hexdigest()


EXPORT = r'''
import hashlib,json,shutil,sys,tarfile
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora')
payload=json.load(sys.stdin)
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()
auditpath=root/payload['audit']
assert sha(auditpath)==payload['audit_sha256']
audit=json.loads(auditpath.read_text())
assert audit['status']=='COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
assert audit['run']=='runs/'+payload['run'] and audit['job_status']=='PASS'
out=auditpath.parent/(payload['run']+'_evidence')
assert out.parent==root/'records/development_completion_20261104_2358'
out.mkdir(exist_ok=False)
files={}
tensors={}
for name,expected in audit['evidence_sha256'].items():
    source=(root/name).resolve()
    assert source.is_relative_to(root)
    if source.suffix=='.pt':
        tensors[name]=expected
        continue
    assert sha(source)==expected,name
    dest=out/'snapshot'/name
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,dest)
    assert sha(dest)==expected,name
    files[str(dest.relative_to(root))]=expected
shutil.copyfile(auditpath,out/'audit.json')
files[str((out/'audit.json').relative_to(root))]=payload['audit_sha256']
manifest={'files':files,'tensor_files_preserved_remotely_only_sha256':tensors}
with (out/'manifest.json').open('x') as f:f.write(json.dumps(manifest,indent=2)+'\n')
archive=Path(str(out)+'.tar.gz')
with archive.open('xb') as handle:
    with tarfile.open(fileobj=handle,mode='w:gz') as tar:
        for name in sorted(files):tar.add(root/name,arcname=name,recursive=False)
        tar.add(out/'manifest.json',arcname=str((out/'manifest.json').relative_to(root)),recursive=False)
print(json.dumps({'archive':str(archive.relative_to(root)),'sha256':sha(archive),
                  'manifest_sha256':sha(out/'manifest.json'),'files':len(files),
                  'tensor_files_remotely_only':len(tensors)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--audit-sha256', required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'phi_dev_(?:[LX]_(?:2048|2560|3584)|standard_adam|paired_adam)_t[0-4]_(?:clean|medium)_s17', args.run)
    assert re.fullmatch(r'[0-9a-f]{64}', args.audit_sha256)
    directory = Path(__file__).resolve().parent
    auditpath = directory / (args.run + '.json')
    relative = str(auditpath.relative_to(ROOT))
    raw = subprocess.run(SSH + ['cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + relative)],
                         capture_output=True, check=True, timeout=60).stdout
    assert digest(raw) == args.audit_sha256
    audit = json.loads(raw)
    assert audit['status'] == 'COMPLETED_DEVELOPMENT_SAVED_EVIDENCE_AUDITED'
    assert audit['run'] == 'runs/' + args.run and audit['job_status'] == 'PASS'
    with auditpath.open('xb') as handle:
        handle.write(raw)
    payload = {'run': args.run, 'audit': relative, 'audit_sha256': args.audit_sha256}
    reply = subprocess.run(SSH + ['/usr/bin/python -c ' + shlex.quote(EXPORT)],
                           input=json.dumps(payload), capture_output=True, text=True,
                           timeout=180, check=True)
    archive_info = json.loads(reply.stdout)
    archive = ROOT / archive_info['archive']
    assert archive == directory / (args.run + '_evidence.tar.gz')
    with archive.open('xb') as handle:
        subprocess.run(SSH + ['cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + archive_info['archive'])],
                       stdout=handle, stderr=subprocess.PIPE, timeout=180, check=True)
    assert digest(archive.read_bytes()) == archive_info['sha256']
    evidence = directory / (args.run + '_evidence')
    assert not evidence.exists()
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        prefix = str(evidence.relative_to(ROOT)) + '/'
        assert len(names) == len(set(names))
        assert all(m.isfile() and m.name.startswith(prefix)
                   and (ROOT / m.name).resolve().is_relative_to(evidence) for m in members)
        tar.extractall(ROOT, members=members, filter='data')
    manifest_path = evidence / 'manifest.json'
    assert digest(manifest_path.read_bytes()) == archive_info['manifest_sha256']
    manifest = json.loads(manifest_path.read_text())
    assert set(names) == set(manifest['files']) | {str(manifest_path.relative_to(ROOT))}
    assert len(manifest['files']) == archive_info['files']
    for name, expected in manifest['files'].items():
        assert digest((ROOT / name).read_bytes()) == expected, name
    for name, expected in audit['evidence_sha256'].items():
        if Path(name).suffix == '.pt':
            assert manifest['tensor_files_preserved_remotely_only_sha256'][name] == expected
        else:
            assert manifest['files'][str((evidence / 'snapshot' / name).relative_to(ROOT))] == expected
    assert (evidence / 'audit.json').read_bytes() == raw
    receipt = {
        'status': 'COMPLETED_DEVELOPMENT_AUDIT_AND_PREDICTION_EVIDENCE_FETCHED_AND_INDEPENDENTLY_REHASHED',
        'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'run': audit['run'], 'audit': relative, 'audit_sha256': args.audit_sha256,
        'archive': archive_info, 'fetch_script_sha256': digest(Path(__file__).read_bytes()),
        'prediction_files': len(audit['prediction_reviews']),
        'prediction_rows': sum(r['rows'] for r in audit['prediction_reviews']),
        'limitations': audit['limitations'], 'method_validated': False,
        'full_goal_complete': False, 'training_modified_or_restarted': False}
    receiptpath = directory / ('fetch_receipt_' + args.run + '.json')
    with receiptpath.open('x') as handle:
        handle.write(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    spec = importlib.util.spec_from_file_location('preserved_sync', ROOT / 'records/development_monitor_20261104_2128/review_prefix_v3.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path in [Path(__file__), receiptpath]:
        print(json.dumps(module.sync_preserved(path)))
    print(json.dumps({'status': receipt['status'], 'archive': archive_info,
                      'prediction_rows': receipt['prediction_rows']}))


if __name__ == '__main__':
    main()
