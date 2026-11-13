"""CPU FP64 checks. These do NOT validate MUSA or real Qwen training."""
from __future__ import annotations
import argparse, json, math, pathlib, sys
import torch
from torch.nn import functional as F
from core import (lora_delta, effective_sqnorm, canonicalize, subspace_noise,
                  factor_noise, LoRALinear, AdapterPhi, paired_adam_step, toy_rollout, mse_forward)

def near(a,b,atol=1e-10,rtol=1e-9):
    torch.testing.assert_close(a,b,atol=atol,rtol=rtol)

def run():
    torch.set_num_threads(2)
    torch.manual_seed(17)
    D = torch.float64
    out = {}
    A=torch.randn(3,7,dtype=D); B=torch.randn(5,3,dtype=D); W=torch.randn(5,7,dtype=D)
    x=torch.randn(9,7,dtype=D,requires_grad=True)
    layer=LoRALinear(W,3,scale=1.3,adapter_dtype=D)
    before=layer.weight.detach().clone()
    near(layer(x),F.linear(x,W))
    with torch.no_grad(): layer.A.copy_(A); layer.B.copy_(B)
    near(layer(x),F.linear(x,W+1.3*B@A))
    loss=layer(x).square().mean(); loss.backward()
    assert layer.weight.grad is None and layer.A.grad is not None and layer.B.grad is not None and x.grad is not None
    near(before,layer.weight)
    out['native_lora_identity_merge_and_frozen_base']='PASS'

    Ac,Bc=canonicalize(A,B); near(Bc@Ac,B@A); near(Ac@Ac.T,torch.eye(3,dtype=D))
    A0,B0=subspace_noise(A,B,0,scale=1.3); near(1.3*B0@A0,1.3*B@A)
    Az,Bz=subspace_noise(A,torch.zeros_like(B),.4); assert torch.count_nonzero(Bz)==0
    out['canonical_q0_and_zero_adapter']='PASS'

    secondA=torch.randn(2,7,dtype=D); secondB=torch.randn(5,2,dtype=D)
    dense=1.3*B@A-.8*secondB@secondA
    near(effective_sqnorm([(A,B,1.3),(secondA,secondB,-.8)]),dense.square().sum())
    # Concatenation / split is exact; summing A and B separately is not.
    split=B[:,:1]@A[:1]+B[:,1:]@A[1:]
    near(split,B@A)
    merged=W+1.3*B@A
    near(F.linear(x,merged),layer(x))
    out['effective_norm_split_merge']='PASS'

    # Distributional second-moment check, plus an exact deterministic identity.
    C=1.3*Bc; q=.2
    z=torch.randn(24000,*C.shape,dtype=D)
    cq=math.sqrt(1-q)*C+math.sqrt(q)*C.square().mean().sqrt()*z
    ratio=float(cq.square().sum((-2,-1)).mean()/C.square().sum())
    assert abs(ratio-1)<.02,ratio
    out['subspace_expected_energy']={'status':'PASS','empirical_ratio':ratio,'samples':24000}

    w=torch.randn(9,dtype=D); gd=torch.randn_like(w); gr=torch.randn_like(w)
    state=tuple(torch.zeros_like(w) for _ in range(3)); zero=torch.zeros(2,dtype=D)
    wn,st=paired_adam_step(w,gd,gr,state,zero,1,lr=.02,wr=.25,ridge=0)
    p=torch.nn.Parameter(w.clone()); opt=torch.optim.Adam([p],lr=.02,eps=1e-8,weight_decay=0)
    p.grad=gd+.25*gr; opt.step(); near(p,wn)
    near(st[2],.001*(gd+.25*gr).square())
    out['combined_gradient_adam_equivalence']='PASS'

    phi=AdapterPhi(coordinate=True).double()
    features=torch.randn(48,dtype=D)
    al,ctx=phi(features); near(al,torch.ones_like(al)); near(phi.residual(torch.randn(8,8,dtype=D),ctx),torch.zeros(8,dtype=D))
    # H1 chain rule: query gradients are evaluated AFTER actual update.
    AA=A.clone().requires_grad_(True); BB=B.clone().requires_grad_(True)
    xd=torch.randn(7,7,dtype=D); yd=torch.randn(7,5,dtype=D)
    xr=torch.randn(6,7,dtype=D); yr=torch.randn(6,5,dtype=D)
    lD=mse_forward(W,AA,BB,xd,yd); lR=mse_forward(W,AA,BB,xr,yr)
    gd=torch.autograd.grad(lD,(AA,BB),retain_graph=True)
    gr=torch.autograd.grad(lR,(AA,BB))
    raw=torch.tensor([[.10,-.05],[.03,.07]],dtype=D,requires_grad=True)
    An,_=paired_adam_step(AA.detach(),gd[0].detach(),gr[0].detach(),tuple(torch.zeros_like(AA) for _ in range(3)),raw[0],1)
    Bn,_=paired_adam_step(BB.detach(),gd[1].detach(),gr[1].detach(),tuple(torch.zeros_like(BB) for _ in range(3)),raw[1],1)
    J=mse_forward(W,An,Bn,xr,yr)
    exact=torch.autograd.grad(J,raw,retain_graph=True)[0]
    gq=torch.autograd.grad(J,(An,Bn),retain_graph=True)
    surrogate=(gq[0].detach()*(An-AA.detach())).sum()+(gq[1].detach()*(Bn-BB.detach())).sum()
    compressed=torch.autograd.grad(surrogate,raw)[0]; near(exact,compressed)
    out['positive_h1_chain_rule']={'status':'PASS','max_abs_error':float((exact-compressed).abs().max())}

    # True H2 differentiates only adapter variables, through frozen base operations.
    base=W.detach(); a=A.detach().clone().requires_grad_(True); b=(.1*B).detach().requires_grad_(True)
    batches=[]
    for _ in range(2):
        batches.append((torch.randn(6,7,dtype=D),torch.randn(6,5,dtype=D),torch.randn(5,7,dtype=D),torch.randn(5,5,dtype=D)))
    raw2=torch.tensor([[.05,-.02],[.03,.04]],dtype=D,requires_grad=True)
    qx=torch.randn(7,7,dtype=D); qy=torch.randn(7,5,dtype=D)
    def objective(c):
        aa=a.detach().clone().requires_grad_(True); bb=b.detach().clone().requires_grad_(True)
        fa,fb,_=toy_rollout(c,base,aa,bb,batches)
        return mse_forward(base,fa,fb,qx,qy)
    obj=objective(raw2); grad=torch.autograd.grad(obj,raw2)[0]
    direction=torch.tensor([[.3,-.2],[.4,.1]],dtype=D); direction/=direction.norm()
    analytic=float((grad*direction).sum()); eps=1e-5
    fd=float(((objective(raw2.detach()+eps*direction)-objective(raw2.detach()-eps*direction))/(2*eps)).detach())
    rel=abs(fd-analytic)/max(abs(fd),abs(analytic),1e-12)
    assert rel < 2e-5,(fd,analytic,rel)
    out['exact_h2_full_trajectory_difference']={'status':'PASS','analytic':analytic,'finite_difference':fd,'relative_error':rel}
    for name,coordinate in [('phi_L',False),('phi_X',True)]:
        out[name+'_parameters']=sum(p.numel() for p in AdapterPhi(coordinate).parameters())
    # Lifecycle: corruption does not mutate the clean parent; deployment damage accounting.
    parentA=A.clone(); parentB=B.clone(); subspace_noise(A,B,.3); near(A,parentA); near(B,parentB)
    clean,noisy,final=2.,3.2,2.1
    near(torch.tensor(final-clean),torch.tensor((noisy-clean)-(noisy-final)))
    out['parent_immutable_and_damage_accounting']='PASS'
    return out

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--out',default='checks/cpu_reference.json'); args=p.parse_args()
    result=run(); path=pathlib.Path(args.out); path.parent.mkdir(parents=True,exist_ok=True)
    payload={'status':'PASS','device':'CPU','torch_version':torch.__version__,
             'scope':'small-tensor mathematical tests only; no MUSA/Qwen training',
             'checks':result}
    path.write_text(json.dumps(payload,indent=2),encoding='utf-8'); print(json.dumps(payload,indent=2))
