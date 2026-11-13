
import pathlib,json,hashlib,sys,torch,datetime
sys.path.insert(0,'tools');from compare_prefix_gradients import compare_tensors
torch.set_num_threads(2);root=pathlib.Path('.')
reference=root/'records/prefix_gradient_results_20261030_0311/snapshot/runs/diagnostics/core_prefix_v7_raw_gradient_r1/repeat_0'
run=root/'runs/diagnostics/L_episode7_same_state_async_r1'
out=root/'records/episode7_same_state_pair_20261030_0600/async_reference_comparison.json'
assert not out.exists()
r={'status':'EVIDENCE_COMPARISON_NOT_METHOD_VALIDATION','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'method_validated':False,'production_modified':False,'core_budget_credit':0,'evidence_sha256':{},'repeats':[]}
def read_json(p):
 r['evidence_sha256'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return json.loads(p.read_text())
def tensor(p):
 r['evidence_sha256'][str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return torch.load(p,map_location='cpu',weights_only=False)
a=tensor(reference/'gradient_episode_7.pt');b=tensor(run/'analytic.pt')
c=read_json(run/'config.json');assert c['episode']==7 and c['analytic_repeats']==4 and c['formal_data_used'] is False
saved=tensor(pathlib.Path(c['initial_phi']));assert hashlib.sha256(pathlib.Path(c['initial_phi']).read_bytes()).hexdigest()==c['initial_phi_sha256']
r['saved_starting_phi_reference_vs_async']=compare_tensors(torch,a['starting_phi'],b['phi']);r['saved_starting_phi_checkpoint_vs_async']=compare_tensors(torch,saved['phi'],b['phi'])
ref_events=[json.loads(l) for l in (reference/'events.jsonl').read_text().splitlines()];ref=[x for x in ref_events if x.get('event')=='inner' and x.get('episode')==7]
events=[json.loads(l) for l in (run/'events.jsonl').read_text().splitlines()];inners=[x for x in events if x.get('event')=='analytic_inner'];assert len(ref)==8 and len(inners)==32
keys=['k','ld','lr','task_tokens','language_tokens','support_forwards','keep_tokens','keep_forwards','exact_window'];first=None
for i in range(4):
 p=tensor(run/f'repeat_{i}.pt');assert p['repeat']==i
 fwd=inners[i*8:(i+1)*8];assert [v['k'] for v in fwd]==list(range(1,9))
 row={'repeat':i,'forward_records_equal_to_reference':all(x.get(k)==y.get(k) for x,y in zip(ref,fwd) for k in keys),'raw_vs_original_anomalous_gradient':compare_tensors(torch,a['raw_gradient'],p['gradient']),'terminal':p['terminal']}
 if first is None:first=p['gradient']
 row['raw_vs_first_repeat']=compare_tensors(torch,first,p['gradient']);r['repeats'].append(row)
r['limitations']=['Recorded ld/lr and token values do not include every support gradient or intermediate state tensor.','Saved starting phi is byte-equal; repeated replay source restores RNG and streams and performs no optimizer updates.','No observed normal repeat proves that the earlier nonfinite/giant gradients are fixed.']
with out.open('x') as f:f.write(json.dumps(r,indent=2,allow_nan=False)+'\n')
print(json.dumps({'output':str(out),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'phi_equal':r['saved_starting_phi_reference_vs_async']['bitwise_equal'],'all_forward_records_match':all(x['forward_records_equal_to_reference'] for x in r['repeats']),'raw_reference_norm':r['repeats'][0]['raw_vs_original_anomalous_gradient']['left_norm'],'norms':[x['raw_vs_original_anomalous_gradient']['right_norm'] for x in r['repeats']]}))
