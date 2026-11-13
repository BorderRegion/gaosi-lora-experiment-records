import pathlib,time,subprocess
root=pathlib.Path('.')
for size in ['3B','7B']:
 while not (root/'records'/f'model_{size}.json').exists():
  time.sleep(20)
 command=['/usr/bin/python','-u','protocol/tools/probe_backend.py','--device','musa','--model',f'/root/autodl-tmp/models/Qwen2.5-{size}','--rank','32','--real-h2','--out',f'checks/musa_{size.lower()}_real_h2.json']
 code=subprocess.call(command)
 print('PROBE_EXIT',size,code,flush=True)
