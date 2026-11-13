"""Idempotent source publication plus a single CPU regression invocation."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')


def main():
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    preparation = json.loads((HERE / 'preparation.json').read_text())
    local_validation = json.loads((HERE / 'validation_local.json').read_text())
    assert local_validation['status'] == 'PASS'
    for name, expected in preparation['original_source_sha256'].items():
        assert sha(ROOT / name) == expected
    for name, expected in local_validation['source_sha256'].items():
        assert sha(ROOT / name) == expected
    files = [p for p in sorted(HERE.iterdir()) if p.is_file()]
    sources = {str(p.relative_to(ROOT)): sha(p) for p in files}
    payload = {'originals': preparation['original_source_sha256'], 'sources': sources,
               'files': {str(p.relative_to(ROOT)): base64.b64encode(p.read_bytes()).decode() for p in files}}
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p['originals'].items():
 assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
for name,data in p['files'].items():
 target=r/name;content=base64.b64decode(data)
 assert hashlib.sha256(content).hexdigest()==p['sources'][name],name
 if target.exists():assert target.read_bytes()==content,name
 else:
  target.parent.mkdir(parents=True,exist_ok=True)
  with target.open('xb') as f:f.write(content)
print(json.dumps({'status':'VERSIONED_SOURCES_PUBLISHED_NO_TRAINING_INSPECTION','sources':p['sources'],'originals':p['originals']}))
'''
    published = json.loads(transport.remote(code, payload))
    save(HERE / 'source_publication.json', published)
    relative = str(HERE.relative_to(ROOT))
    code = '''import datetime as dt,json,subprocess,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);folder=r/p['folder']
invocation=folder/'cpu_validation_invocation.json';output=folder/'validation_remote.json'
assert not invocation.exists() and not output.exists(),'Preserve prior invocation; inspect instead of resubmitting'
with invocation.open('x') as f:json.dump({'status':'CPU_REGRESSION_INVOKED_ONCE','utc':dt.datetime.now(dt.timezone.utc).isoformat(),'training_submitted':False},f)
result=subprocess.run(['/usr/bin/python',str(folder/'validate.py'),'--out',str(output)],cwd=r,capture_output=True,text=True,timeout=40)
for name,value in [('cpu_validation.stdout',result.stdout),('cpu_validation.stderr',result.stderr)]:
 with (folder/name).open('x') as f:f.write(value)
with (folder/'cpu_validation_exit.json').open('x') as f:json.dump({'returncode':result.returncode},f)
assert result.returncode==0,result.stderr
print(output.read_text())
'''
    # This is an invocation, not a read: after any lost reply inspect these exact
    # files; do not retry this call or infer that a training process stopped.
    validation = json.loads(transport.remote(code, {'folder': relative}, attempts=1, timeout=55))
    assert validation['status'] == 'PASS'
    assert validation['checkpoint_io'] == 'real_torch_save_load_CPU_only_no_tensor_training'
    for name, expected in validation['source_sha256'].items():
        assert sha(ROOT / name) == expected, name
    save(HERE / 'validation_remote.json', validation)
    receipt = {'status': 'VERSIONED_CONFIRMATION_PAIRING_PUBLISHED_CPU_REGRESSION_PASSED_NOT_ACTIVATED',
               'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'published_source_sha256': sources,
               'local_validation_sha256': sha(HERE / 'validation_local.json'),
               'remote_validation_sha256': sha(HERE / 'validation_remote.json'),
               'remote_checkpoint_io': validation['checkpoint_io'],
               'simulated_meta_entries': sum(v['simulated_meta_entries'] for v in validation['runs']),
               'invalid_mutations_rejected': sum(v['invalid_mutations_rejected'] for v in validation['runs']),
               'original_numerical_sources_unchanged': True, 'training_or_controller_modified': False,
               'requires_before_training': ['Explicit completed-review-boundary controller handoff',
                   'Existing artifact, Adam replay and scientific reviews for every actual completed meta leaf'],
               'numerical_derivatives_or_efficacy_validated': False, 'full_goal_complete': False}
    save(HERE / 'publication_receipt.json', receipt)
    transport.sync(HERE / 'source_publication.json')
    transport.sync(HERE / 'publication_receipt.json')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
