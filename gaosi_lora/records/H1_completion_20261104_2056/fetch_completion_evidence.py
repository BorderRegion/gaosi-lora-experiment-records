"""Fetch immutable completed audit evidence and inspect existing controller identities only."""
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
observation = ROOT / 'records/pipeline_monitor_20261104_205526_utc'
result = json.loads((observation / 'result.json').read_text())
expected = [j['job'] for j in result['jobs'] if j['controller']]
assert len(expected) == 15
payload = {'record': str(RECORD.relative_to(ROOT)), 'controllers': expected}
code = '''import base64,datetime as dt,hashlib,json,sys
from pathlib import Path
p=json.load(sys.stdin);root=Path('/root/autodl-tmp/gaosi_lora');record=root/p['record']
rows=[]
for old in p['controllers']:
 path=root/'records/jobs'/(old['name']+'.json');job=json.loads(path.read_text())
 assert job['status']=='RUNNING' and job['child_pid']==old['child_pid']
 assert job['child_proc_start_ticks']==old['child_proc_start_ticks']
 proc=Path('/proc')/str(job['child_pid']);fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
 assert fields[0] not in ('Z','X') and fields[19]==job['child_proc_start_ticks']
 command=(proc/'cmdline').read_bytes();assert ' '.join(job['command']).encode() in command.replace(b'\\0',b' ')
 blocking=[v.decode() for v in (proc/'environ').read_bytes().split(b'\\0') if v.startswith(b'MUSA_LAUNCH_BLOCKING=')]
 assert blocking==['MUSA_LAUNCH_BLOCKING=1']
 rows.append({'name':job['name'],'status':job['status'],'pid':job['child_pid'],'start_ticks':fields[19],
 'proc_state':fields[0],'cmdline':command.replace(b'\\0',b' ').decode(),'blocking':blocking,
 'job_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
assert Path('/tmp/gaosi_musa_device0.lock').stat().st_ino==31148027670
identity={'status':'EXISTING_15_CONTROLLER_IDENTITIES_VERIFIED_BEFORE_H1_REVIEW','checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'controllers':rows,'gpu_lock_inode':31148027670,'training_modified_or_restarted':False}
with (record/'controller_identity_before_review.json').open('x') as h:json.dump(identity,h,indent=2);h.write('\\n')
files={}
for name in ['artifact_audit.json','adam_replay.json','tensor_review.json','controller_identity_before_review.json']:
 b=(record/name).read_bytes();files[name]={'sha256':hashlib.sha256(b).hexdigest(),'data':base64.b64encode(b).decode()}
print(json.dumps(files))
'''
ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(code)], input=json.dumps(payload),
                       text=True, capture_output=True, check=True, timeout=60)
files = json.loads(reply.stdout)
for name, item in files.items():
    assert Path(name).name == name
    b = base64.b64decode(item['data'])
    assert hashlib.sha256(b).hexdigest() == item['sha256']
    with (RECORD / name).open('xb') as h:
        h.write(b)
receipt = {'status': 'COMPLETED_H1_EVIDENCE_FETCHED_HASH_VERIFIED',
           'fetched_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
           'files_sha256': {name: item['sha256'] for name, item in files.items()},
           'training_modified_or_restarted': False}
with (RECORD / 'fetch_receipt.json').open('x') as h:
    json.dump(receipt, h, indent=2)
    h.write('\n')
print(json.dumps(receipt))
