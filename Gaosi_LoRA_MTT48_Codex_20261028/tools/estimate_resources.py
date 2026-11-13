"""Static byte arithmetic, NOT a throughput/peak-memory claim."""
from __future__ import annotations
import json,argparse,pathlib
SPECS={
 'Qwen2.5-3B':dict(params=3.09e9,h=2048,intermediate=11008,layers=36,heads=16,kv_heads=2),
 'Qwen2.5-7B':dict(params=7.61e9,h=3584,intermediate=18944,layers=28,heads=28,kv_heads=4),
}

def estimate(rank=32):
    rows=[]
    for name,s in SPECS.items():
        h=s['h']; kv=s['kv_heads']*(h//s['heads']); ff=s['intermediate']
        shapes={'q_proj':(h,h),'k_proj':(kv,h),'v_proj':(kv,h),'o_proj':(h,h),
                'gate_proj':(ff,h),'up_proj':(ff,h),'down_proj':(h,ff)}
        for coverage,names in [('all7',list(shapes)),('attn4',list(shapes)[:4]),('qv',['q_proj','v_proj'])]:
            n=sum(rank*(shapes[n][0]+shapes[n][1]) for n in names)*s['layers']
            nb=sum(rank*shapes[n][0] for n in names)*s['layers']
            rows.append(dict(model=name,rank=rank,coverage=coverage,AB_parameters=n,B_only_parameters=nb,
                             bf16_base_GB=s['params']*2/1e9,adapter_master_grad_mD_mR_v_GB=n*20/1e9,
                             subtotal_GB=(s['params']*2+n*20)/1e9,one_extra_fp32_adapter_vector_GB=n*4/1e9,
                             **{'not_included':'activations, high-order graph, logits, temporary tensors, PEFT caches; NOT peak VRAM'}))
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rank',type=int,default=32);p.add_argument('--out');a=p.parse_args()
    payload={'source':'official Qwen model configs; actual instantiated trainable count must be audited',
             'units':'decimal GB','rows':estimate(a.rank)}
    txt=json.dumps(payload,indent=2);print(txt)
    if a.out:
        path=pathlib.Path(a.out);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(txt)
