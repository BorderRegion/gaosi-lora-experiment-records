"""Publish inactive handoff preparation and invoke isolated CPU regression once."""
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


def preserve(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')


def main():
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    validation = json.loads((HERE / 'validation_local_v2.json').read_text())
    assert validation['status'] == 'PASS'
    checks = dict(validation['production_reference_sha256'])
    for name in ['gaosi/meta.py', 'gaosi/runtime.py', 'tools/train_guarded_meta.py',
                 'tools/guarded_pipeline.py',
                 'records/organization_clean_pairing_20261110_1620/organization_queue_corrected_v2.py']:
        checks[name] = sha(ROOT / name)
    for name, expected in {**checks, **validation['source_sha256']}.items():
        assert sha(ROOT / name) == expected, name
    assert not (HERE / 'plan.json').exists() and not (HERE / 'activation.json').exists()
    files = [p for p in sorted(HERE.iterdir()) if p.is_file()]
    payload = {'references': checks, 'files': {str(p.relative_to(ROOT)): {
        'sha256': sha(p), 'content': base64.b64encode(p.read_bytes()).decode()} for p in files}}
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p['references'].items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
for name,value in p['files'].items():
 path=r/name;content=base64.b64decode(value['content']);assert hashlib.sha256(content).hexdigest()==value['sha256']
 if path.exists():assert path.read_bytes()==content,name
 else:
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('xb') as f:f.write(content)
print(json.dumps({'published_files':len(p['files']),'production_references_rehashed':len(p['references'])}))
'''
    publication = json.loads(transport.remote(code, payload))
    relative = str(HERE.relative_to(ROOT))
    code = '''import datetime as dt,json,subprocess,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);folder=r/p['folder']
intent=folder/'cpu_validation_invoked.json';out=folder/'validation_remote.json'
assert not intent.exists() and not out.exists(),'Inspect the existing invocation; never resubmit'
assert not (folder/'plan.json').exists() and not (folder/'activation.json').exists()
with intent.open('x') as f:json.dump({'status':'ISOLATED_PROCESS_FIXTURE_INVOKED_ONCE','utc':dt.datetime.now(dt.timezone.utc).isoformat()},f)
result=subprocess.run(['/usr/bin/python',str(folder/'validate.py'),'--out',str(out)],cwd=r,capture_output=True,text=True,timeout=40)
for name,value in [('cpu_validation.stdout',result.stdout),('cpu_validation.stderr',result.stderr)]:
 with (folder/name).open('x') as f:f.write(value)
with (folder/'cpu_validation_exit.json').open('x') as f:json.dump({'returncode':result.returncode},f)
assert result.returncode==0,result.stderr
print(out.read_text())
'''
    remote = json.loads(transport.remote(code, {'folder': relative}, attempts=1, timeout=55))
    assert remote['status'] == 'PASS' and remote['cases'] == validation['cases']
    assert remote['source_sha256'] == {**validation['source_sha256'],
                                      str(Path(__file__).relative_to(ROOT)): sha(Path(__file__))}
    assert remote['production_reference_sha256'] == validation['production_reference_sha256']
    preserve(HERE / 'validation_remote.json', remote)
    result = {'status': 'IDLE_HANDOFF_PREPARATION_PUBLISHED_ISOLATED_CPU_REGRESSION_PASSED_NOT_ACTIVATED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'publication': publication, 'source_sha256': remote['source_sha256'],
              'local_validation_sha256': sha(HERE / 'validation_local_v2.json'),
              'remote_validation_sha256': sha(HERE / 'validation_remote.json'),
              'production_sources_sha256': checks, 'production_plan_created': False,
              'production_handoff_activated': False, 'numerical_training_modified': False,
              'requires_before_activation': remote['requires_before_production'],
              'method_validated': False, 'full_goal_complete': False}
    preserve(HERE / 'publication_receipt.json', result)
    transport.sync(HERE / 'publication_receipt.json')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
