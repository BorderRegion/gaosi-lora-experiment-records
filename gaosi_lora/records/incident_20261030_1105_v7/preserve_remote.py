
import json,hashlib,shutil,datetime,tarfile,subprocess,os,time
from pathlib import Path
root=Path('/root/autodl-tmp/gaosi_lora');os.chdir(root);incident=root/'records/incident_20261030_1105_v7';incident.mkdir(exist_ok=False)
manifest=json.loads((root/'records/pipeline_recompute_v7/build_manifest.json').read_text())
name=manifest['adopted_L_job'];job=json.loads((root/'records/jobs'/f'{name}.json').read_text())
if job['status']!='FAIL':raise RuntimeError('Review changed production state')
sources=[root/'configs'/f'{name}.json',root/'runs'/name/'latest.pt',root/'runs'/name/'phi_0512.pt',root/'runs'/name/'events.jsonl',root/'runs'/name/'config.json',root/'records/jobs'/f'{name}.json',root/'records/logs'/f'{name}.log',root/'records/pipeline_recompute_v7/build_manifest.json',root/'records/pipeline_recompute_v7/launch.json']
sources+=list((root/'gaosi').glob('*.py'))
sources+=[root/'tools'/x for x in ['job.py','device_guard.py','diagnose_meta_gradient.py','diagnose_repeat_gradient.py','diagnose_prefix_gradient.py','hvp_trace.py']]
for j in manifest['jobs']:
 sources+=[root/'records/jobs'/f"{j['name']}.json",root/'records/logs'/f"{j['name']}.log"]
sha={}
for p in sources:
 dest=incident/'snapshot'/p.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);sha[str(dest.relative_to(root))]=hashlib.sha256(dest.read_bytes()).hexdigest()
summary={'status':'V7_NONFINITE_ELEMENTS_EPISODE72','recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'training_job':job,'committed_episodes':72,'committed_inner_updates':640,'uncommitted_episode':72,'uncommitted_inner_steps':16,'checkpoint_finite_from_inspector':True,'checkpoint_sha256':hashlib.sha256((incident/'snapshot/runs'/name/'latest.pt').read_bytes()).hexdigest(),'raw_failed_gradient_preserved':False,'reason_raw_missing':'Process exited at nonfinite guard; no raw-gradient capture in production v7. Existing checkpoint is pre-episode72.','controller_failures_are_upstream_propagation':True,'training_restarted_or_modified':False,'source_hashes':sha}
(incident/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
archive=root/'records/incident_20261030_1105_v7.tar.gz'
with archive.open('xb') as f:
 with tarfile.open(fileobj=f,mode='w:gz') as t:t.add(incident,arcname=str(incident.relative_to(root)))
print(json.dumps({'summary':summary,'archive':str(archive.relative_to(root)),'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'archive_bytes':archive.stat().st_size}))
