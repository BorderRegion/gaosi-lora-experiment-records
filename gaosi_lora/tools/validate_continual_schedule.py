"""Check frozen schedules/base probes independently using tiny dev fixtures."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import torch
from transformers import AutoTokenizer,Qwen2Config,Qwen2ForCausalLM

from gaosi.data import rows
from gaosi.model import AdapterModel
from gaosi.runtime import atomic_save
from gaosi.transition import Phi
from gaosi.continual import check_config
from gaosi.capacity_reference import evaluate
from continual_queue import POLICY,pilot_adequacy,select_worlds,valid_freezes
from device_guard import uses_musa


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tokenizer',default='/root/autodl-tmp/models/Qwen2.5-3B')
    parser.add_argument('--out',default='checks/continual_schedule_cpu.json')
    args=parser.parse_args()
    torch.set_num_threads(2)
    torch.manual_seed(2948)
    tests={}
    with tempfile.TemporaryDirectory(prefix='gaosi_dev_continual_schedule_') as directory:
        root=Path(directory)
        tokenizer=AutoTokenizer.from_pretrained(args.tokenizer,local_files_only=True)
        path=root/'tiny'
        tiny=Qwen2ForCausalLM(Qwen2Config(vocab_size=len(tokenizer),hidden_size=8,intermediate_size=16,
            num_hidden_layers=1,num_attention_heads=1,num_key_value_heads=1,head_dim=8,attention_dropout=0.,
            eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.eos_token_id))
        tiny.save_pretrained(path);tokenizer.save_pretrained(path);del tiny
        model=AdapterModel(str(path),2,'all7','O1','cpu')
        digest=model.base_digest()
        parent=root/'parent/latest.pt'
        atomic_save({'config':{'model':str(path),'rank':2,'targets':'all7','scale':1.,'organization':'O1'},
                     'base_digest':digest},parent)
        del model
        source=rows('data/synthetic/dev_parent.jsonl')
        ids=sorted({row['fact_id'] for row in source})[:13]
        files=[]
        for i,key in enumerate(ids):
            file=root/f'dev_task_{i}.jsonl'
            file.write_text(''.join(json.dumps(row)+'\n' for row in source if row['fact_id']==key))
            files.append(str(file))
        phi=root/'phi.pt';atomic_save({'phi':Phi('L').state_dict()},phi)
        phi_hash=hashlib.sha256(phi.read_bytes()).hexdigest()
        manifests={}
        for org in ['O1','O2','O3','O4']:
            frozen={'status':'FROZEN','stopping_rule_frozen':True,'formal_data_used':False,'organization':org,
                    'training_noise':'mixed','phi':str(phi),'phi_sha256':phi_hash,'architecture':'L',
                    'q_medium':.7,'capacity_lr':.001,'recovery_lr':.001,'rank':2,'targets':'all7','scale':1.}
            file=root/f'freeze_{org}.json';file.write_text(json.dumps(frozen));manifests[org]=str(file)
        all_frozen={'status':'ALL_PHI_FROZEN','manifests':manifests}
        assert set(valid_freezes(all_frozen))=={'O1','O2','O3','O4'}
        bad=json.loads(Path(manifests['O2']).read_text());bad['organization']='O1'
        Path(manifests['O2']).write_text(json.dumps(bad))
        try:valid_freezes(all_frozen)
        except ValueError:pass
        else:raise AssertionError('Organization phi swap accepted')
        bad['organization']='O2';Path(manifests['O2']).write_text(json.dumps(bad))
        tests['all_four_freezes_are_hashed_and_organization_swaps_rejected']='PASS'
        plan={'status':'FROZEN_CONTINUAL_SCHEDULE','phi_frozen_before_selection':True,'worlds':files[1:9],
              'probe_worlds':files[9:13],'parent':str(parent),'parent_data':files[0],
              'day_steps':32,'night_steps':16,'day_lr':.001,'night_lr':.001,'seed':17,'replay_seed':912,
              'probe_seed':37,'seq':128,'effective_tokens':16,'language_eval':'data/language/dev.jsonl',
              'language_eval_tokens':16,'measure_inference':True,'inference_repetitions':2,
              'data_sha256':{file:hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in files}}
        planfile=root/'schedule.json';planfile.write_text(json.dumps(plan))
        config={key:value for key,value in plan.items() if key not in
                ['status','phi_frozen_before_selection','data_sha256']}
        config.update(phase='formal',rounds=8,freeze_manifest=manifests['O1'],schedule_manifest=str(planfile),
                      organization='O1',architecture='L',night_method='standard_adam',q_apply=.7)
        check_config(config)
        for key,value in [('worlds',list(reversed(config['worlds']))),('day_steps',64),('night_lr',.01),
                          ('probe_seed',38),('q_apply',.5),('effective_tokens',8),('measure_inference',False)]:
            try:check_config(dict(config,**{key:value}))
            except ValueError:pass
            else:raise AssertionError(f'Changed formal {key} accepted')
        tests['formal_budget_rates_data_order_probe_seed_dose_and_cost_measurements_cannot_drift']='PASS'
        original=Path(files[1]).read_text();Path(files[1]).write_text(original+'\n')
        try:check_config(config)
        except ValueError as error:assert 'bytes' in str(error)
        else:raise AssertionError('Changed task bytes accepted')
        Path(files[1]).write_text(original)
        tests['formal_schedule_rejects_changed_task_bytes']='PASS'
        broken=json.loads(Path(manifests['O1']).read_text());broken['phi_sha256']='invalid'
        Path(manifests['O1']).write_text(json.dumps(broken))
        try:check_config(dict(config,parent_data='missing_DO_NOT_READ'))
        except ValueError as error:assert 'intact' in str(error)
        else:raise AssertionError('Bad phi accepted')
        broken['phi_sha256']=phi_hash;Path(manifests['O1']).write_text(json.dumps(broken))
        tests['formal_phi_freeze_validated_before_task_access_for_Adam_too']='PASS'
        tiny_config={'out':str(root/'base_reference'),'freeze_manifest':manifests['O1'],
                     'schedule_manifest':str(planfile),'parent':str(parent),'probe_worlds':files[9:13],
                     'seq':128,'effective_tokens':16,'device':'cpu','inference_repetitions':2}
        evaluate(tiny_config)
        result=json.loads((root/'base_reference/result.json').read_text())
        assert result['base_unchanged'] and result['base_digest']==digest
        assert len(result['probes'])==4 and result['common_extra_rank']==16
        assert all([point['steps'] for point in task['curves']]==[1,4,16] for task in result['probes'])
        assert not result['repeated_as_independent_0_4_8_measurements']
        tests['untouched_base_has_same_four_rank16_1_4_16_probes_and_shared_inference_reference']='PASS'
        manifest={'worlds':[{'world_id':f'final_{i:03d}','split':'final','kind':'fact','file':f'{i}.jsonl'}
                            for i in range(16)]}
        stream,probes=select_worlds(manifest)
        assert len(stream)==8 and len(probes)==4 and not set(stream)&set(probes)
        assert select_worlds(manifest)==(stream,probes)
        assert len(POLICY['organizations'])*len(POLICY['methods'])==16
        tests['sixteen_arms_share_deterministic_eight_world_order_and_disjoint_four_probes']='PASS'
        pilot=[]
        for dose in ['clean','medium']:
            folder=root/dose;folder.mkdir()
            events=[]
            for round_number in [1,2]:
                for event in ['new_day_evaluation','new_night_evaluation']:
                    events.append({'event':event,'round':round_number,
                                   'metrics':{'candidate_accuracy':.9,'strict_EM':.8}})
            (folder/'events.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
            pilot.append({'config':{'out':str(folder)}})
        assert pilot_adequacy(pilot)[0]
        for event in events:
            if event['event']=='new_night_evaluation':event['metrics']['candidate_accuracy']=.5
        (folder/'events.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
        assert not pilot_adequacy(pilot)[0]
        tests['development_adequacy_needs_new_task_acquisition_and_night_retention_in_both_doses']='PASS'
        file=root/'cpu.json';file.write_text(json.dumps({'device':'cpu'}))
        assert not uses_musa(['/usr/bin/python','-m','gaosi.capacity_reference','--config',str(file)])
        file.write_text(json.dumps({'device':'musa'}))
        assert uses_musa(['/usr/bin/python','-m','gaosi.capacity_reference','--config',str(file)])
        tests['real_base_probes_share_the_existing_device_mutex']='PASS'
    result={'status':'PASS','scope':'Tiny random CPU/dev fixtures only; not sixteen real trajectories or MUSA cost data',
            'tests':tests}
    Path(args.out).write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
