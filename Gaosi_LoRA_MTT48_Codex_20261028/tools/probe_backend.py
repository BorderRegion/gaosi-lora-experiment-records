"""Small backend probe; --model /LOCAL/PATH optionally probes an actual Qwen.

Does not install packages, change drivers, or train Gaosi. H2 here is a small
SGD adapter-only backend check; production H2 still needs the moment-aware tests.
"""
from __future__ import annotations
import argparse,json,pathlib,sys,time,traceback,importlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'reference'))
import torch
from core import LoRALinear,toy_rollout,mse_forward

TARGETS={'q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'}

def sync(device):
    if device.type=='musa': torch.musa.synchronize()
    elif device.type=='cuda': torch.cuda.synchronize()


def probe(device,model_path=None,real_h2=False,rank=32,base_dtype=torch.bfloat16):
    result={'device':str(device),'torch':torch.__version__,'tests':{}}
    if device.type=='musa':
        result['torch_musa']=getattr(importlib.import_module('torch_musa'),'__version__','unknown')
        result['device_name']=torch.musa.get_device_name(0)
    elif device.type=='cuda':result['device_name']=torch.cuda.get_device_name(0)
    else:result['device_name']='CPU; not MUSA verification'
    def run(name,fn):
        try:
            sync(device);start=time.perf_counter();details=fn();sync(device)
            result['tests'][name]={'status':'PASS','seconds':time.perf_counter()-start,'details':details}
        except Exception as ex:
            result['tests'][name]={'status':'FAIL','error':repr(ex),'traceback':traceback.format_exc()}
    def native_test():
        base=torch.randn(8,12,device=device)
        layer=LoRALinear(base,4).to(device)
        x=torch.randn(5,12,device=device,requires_grad=True)
        y=layer(x);loss=y.square().mean();loss.backward()
        assert layer.B.grad is not None and layer.weight.grad is None and x.grad is not None
        return {'loss':float(loss.detach().cpu()),'base_grad':None}
    run('native_adapter_backward',native_test)
    def h2_test():
        base=torch.randn(4,7,device=device)*.1
        A=(torch.randn(2,7,device=device)*.1).requires_grad_()
        B=(torch.randn(4,2,device=device)*.1).requires_grad_()
        batches=[tuple(torch.randn(*s,device=device) for s in [(5,7),(5,4),(5,7),(5,4)]) for _ in range(2)]
        raw=torch.zeros(2,2,device=device,requires_grad=True)
        a,b,_=toy_rollout(raw,base,A,B,batches,lr=.003,ridge=1e-10)
        J=mse_forward(base,a,b,batches[-1][2],batches[-1][3])
        grad=torch.autograd.grad(J,raw)[0]
        assert torch.isfinite(grad).all() and grad.abs().sum()>0
        return {'phi_grad_norm':float(grad.norm().cpu()),'note':'toy WITH moments; no real transformer yet'}
    run('toy_moment_h2',h2_test)
    def bf16_test():
        a=torch.randn(8,8,device=device,dtype=torch.bfloat16,requires_grad=True)
        loss=(a@a).float().square().mean();loss.backward()
        assert torch.isfinite(a.grad).all()
        return {'loss':float(loss.detach().cpu())}
    run('bf16_matmul_backward',bf16_test)
    if model_path:
        # Must be a pre-downloaded local path; does not initiate downloads.
        if not pathlib.Path(model_path).is_dir():raise ValueError('--model must be a local model directory')
        from transformers import AutoModelForCausalLM,AutoTokenizer
        model=AutoModelForCausalLM.from_pretrained(model_path,torch_dtype=base_dtype,
                    local_files_only=True,attn_implementation='eager').to(device)
        model.requires_grad_(False); model.config.use_cache=False
        modules=list(model.named_modules()); n=0
        for name,module in modules:
            if name.split('.')[-1] in TARGETS and isinstance(module,torch.nn.Linear):
                parent_name,_,child=name.rpartition('.')
                parent=model.get_submodule(parent_name) if parent_name else model
                setattr(parent,child,LoRALinear(module.weight,rank,1.,module.bias).to(device));n+=1
        assert n>0,'no target linear modules found'
        tok=AutoTokenizer.from_pretrained(model_path,local_files_only=True)
        batch=tok('A small research test. A model can learn patterns from text.',return_tensors='pt')
        batch={k:v.to(device) for k,v in batch.items()};batch['labels']=batch['input_ids'].clone()
        params={k:v for k,v in model.named_parameters() if v.requires_grad}
        result['real_model']={'path':model_path,'injected_modules':n,'trainable_parameters':sum(p.numel() for p in params.values())}
        def first_order():
            J=model(**batch).loss
            grads=torch.autograd.grad(J,tuple(params.values()))
            assert all(torch.isfinite(g).all() for g in grads)
            assert all(p.grad is None for p in model.parameters() if not p.requires_grad)
            return {'loss':float(J.detach().cpu()),'grad_sq_sum':sum(float(g.detach().float().square().sum().cpu()) for g in grads)}
        run('real_qwen_lora_backward',first_order)
        if real_h2:
            from torch.func import functional_call
            def higher_order():
                raw=torch.tensor(-9.,device=device,requires_grad=True)
                work=dict(params)
                for _ in range(2):
                    loss=functional_call(model,work,(),batch,strict=False).loss
                    gs=torch.autograd.grad(loss,tuple(work.values()),create_graph=True)
                    work={k:v-raw.exp()*g for (k,v),g in zip(work.items(),gs)}
                terminal=functional_call(model,work,(),batch,strict=False).loss
                g=torch.autograd.grad(terminal,raw)[0]
                assert torch.isfinite(g)
                return {'loss':float(terminal.detach().cpu()),'raw_step_gradient':float(g.detach().cpu()),
                        'note':'two-step SGD backend probe, NOT production Gaosi Adam-state H2'}
            run('real_qwen_lora_sgd_h2_backend_probe',higher_order)
        else:result['tests']['real_qwen_lora_sgd_h2_backend_probe']={'status':'NOT_RUN'}
    else:result['tests']['real_qwen']={'status':'NOT_RUN','reason':'no local --model supplied'}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--device',choices=['musa','cuda','cpu'],default='musa')
    p.add_argument('--model');p.add_argument('--real-h2',action='store_true');p.add_argument('--rank',type=int,default=32)
    p.add_argument('--dtype',choices=['bf16','fp32'],default='bf16')
    p.add_argument('--out',default='checks/backend_probe.json');a=p.parse_args()
    if a.device=='musa':import torch_musa  # Explicit requested backend; no silent CPU fallback.
    r=probe(torch.device(a.device),a.model,a.real_h2,a.rank,torch.bfloat16 if a.dtype=='bf16' else torch.float32)
    r['scope']='compatibility probe, not scientific effect evidence'
    path=pathlib.Path(a.out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(r,indent=2),encoding='utf8');print(json.dumps(r,indent=2))
    sys.exit(1 if any(t['status']=='FAIL' for t in r['tests'].values()) else 0)
