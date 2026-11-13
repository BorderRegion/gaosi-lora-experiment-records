from pathlib import Path
import base64,datetime,hashlib,json,os,sys
payload=json.load(sys.stdin)
root=Path(payload['root']);record=root/payload['record'];record.mkdir(exist_ok=True)
prefix=base64.b64decode(payload['prefix'])
assert hashlib.sha256(prefix).hexdigest()==payload['prefix_sha256']
backup=record/payload['backup'];backup.mkdir(exist_ok=False)
rows=[]
for name in ['NEXT_STEPS.md','REPORT.md']:
 p=root/name;old=p.read_bytes()
 with (backup/name).open('xb') as h:h.write(old)
 assert not old.startswith(prefix)
 temp=p.with_name(p.name+'.pipeline_progress_1244.tmp')
 with temp.open('xb') as h:h.write(prefix+old)
 assert p.read_bytes()==old
 os.replace(temp,p)
 new=p.read_bytes();assert new==prefix+old
 rows.append({'path':name,'before_sha256':hashlib.sha256(old).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest(),'old_history_retained_exactly':True})
receipt={'status':'DOCS_PREPENDED_WITH_LOCAL_HISTORY_PRESERVED','updated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(root),'prefix_sha256':payload['prefix_sha256'],'files':rows,'training_modified_or_restarted':False}
with (record/(payload['backup']+'_receipt.json')).open('x') as h:json.dump(receipt,h,indent=2);h.write('\n')
print(json.dumps(receipt))
