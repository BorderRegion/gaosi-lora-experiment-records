"""Deterministic synthetic tasks with fact/role IDs; no network or LLM API.

Parent-adapter training uses support expressions only. Final parents may learn
final facts only AFTER phi selection/freezing; final query never enters training.
"""
from __future__ import annotations
import argparse,hashlib,json,pathlib,random
VALUES={
 'hometown':['oslo','lima','perth','bern','pisa','kyoto','accra','dover'],
 'assigned metal':['copper','silver','gold','iron','tin','zinc','nickel','lead'],
 'assigned fruit':['apple','pear','plum','peach','mango','melon','cherry','lemon'],
 'assigned instrument':['flute','harp','drum','piano','violin','cello','trumpet','guitar'],
 'assigned tree':['oak','pine','birch','cedar','maple','elm','willow','spruce'],
 'assigned color':['red','blue','green','yellow','purple','orange','black','white'],
 'assigned animal':['fox','wolf','hare','deer','otter','horse','tiger','zebra'],
 'assigned badge':['circle','square','triangle','star','oval','cross','diamond','spiral'],
}
TRAIN=["Record: What is the {attr} of {entity}?\nAnswer: ",
       "Complete the entry. {entity}'s {attr}: ",
       "Question: Name the {attr} assigned to {entity}.\nAnswer: ",
       "Catalog entry for {entity}. The {attr} is "]
QUERY=["For {entity}, which value is recorded under {attr}?\nAnswer: ",
       "Retrieve {entity}'s {attr} from the catalog.\nValue: "]

def facts(name,entities,attrs,seed):
    rng=random.Random(seed);result=[]; attrs=list(VALUES)[:attrs]
    # Balanced answers per attribute; independent randomized bindings.
    maps={}
    for attr in attrs:
        vals=(VALUES[attr]*((entities+7)//8))[:entities];rng.shuffle(vals);maps[attr]=vals
    for e in range(entities):
        entity=f"{name}_Nerin{e:03d}"
        for j,attr in enumerate(attrs):
            result.append(dict(fact_id=f'{name}:e{e}:a{j}',entity=entity,attribute=attr,
                               attribute_index=j,answer=maps[attr][e],candidates=VALUES[attr]))
    return result

def write_jsonl(path,rows):
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8')

def fact_rows(name,fs):
    rows=[]
    for fact in fs:
        for role,templates in [('support',TRAIN),('query',QUERY)]:
            for t,template in enumerate(templates):
                rows.append(dict(sample_id=f"{fact['fact_id']}:{role}:{t}",world_id=name,kind='fact',
                    role=role,prompt=template.format(attr=fact['attribute'],entity=fact['entity']),
                    answer=fact['answer'],fact_id=fact['fact_id'],entity=fact['entity'],candidates=fact['candidates']))
    return rows

def build(dest,seed=314159):
    dest.mkdir(parents=True,exist_ok=True);manifest={'seed':seed,'parents':{},'worlds':[]}
    all_ids=set();rowcount=0
    def save(name,rows):
        nonlocal rowcount
        ids=[r['sample_id'] for r in rows]
        assert len(ids)==len(set(ids)) and not all_ids.intersection(ids)
        all_ids.update(ids);rowcount+=len(rows);write_jsonl(dest/f'{name}.jsonl',rows)
    for i,name in enumerate(['meta_parent_a','meta_parent_b','dev_parent','final_parent']):
        fs=facts(name,64,8,seed+i);rows=fact_rows(name,fs);save(name,rows)
        F=[f['fact_id'] for f in fs if f['attribute_index']==0];R=[f['fact_id'] for f in fs if f['attribute_index']!=0]
        manifest['parents'][name]={'bindings':len(fs),'target_ids':F,'retain_ids':R,
            'permitted_use':'after_phi_freeze_only' if name=='final_parent' else name,
            'query_is_training_label':False}
        assert len(F)==64 and len(R)==448 and not set(F)&set(R)
    for split,n,nfacts,offset in [('meta',64,48,1000),('dev',16,8,2000),('final',32,16,3000)]:
        for i in range(n):
            name=f'{split}_world_{i:03d}'
            if i<nfacts:
                fs=facts(name,8,4,seed+offset+i);rows=fact_rows(name,fs);kind='fact'
            else:
                rng=random.Random(seed+offset+i);a=rng.randint(1,10);b=rng.randint(0,10);rows=[];kind='affine_mod11'
                for role,xs in [('support',range(7)),('query',range(7,11))]:
                    for x in xs:
                        rows.append(dict(sample_id=f'{name}:{role}:{x}',world_id=name,kind=kind,role=role,
                            prompt=f'In system {name}, f is an affine function modulo 11. What is f({x})?\nAnswer: ',
                            answer=str((a*x+b)%11),candidates=[str(k) for k in range(11)],x=x))
                # Hidden oracle coefficients for offline generation/audit only, never phi features.
                manifest.setdefault('rule_oracles',{})[name]={'a':a,'b':b,'modulus':11,'support_identifies_family':True}
            save(name,rows);manifest['worlds'].append({'world_id':name,'split':split,'kind':kind,'file':name+'.jsonl'})
    manifest['total_rows']=rowcount
    manifest['external_language_pool']='Not included. Split an authorized corpus into inner/meta_query/dev/final by document before chunking.'
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    digests={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dest.glob('*.jsonl'))}
    (dest/'SHA256.json').write_text(json.dumps(digests,indent=2))
    return {'status':'PASS','rows':rowcount,'files':len(digests),'unique_sample_ids':len(all_ids),
            'parent_bindings_each':512,'formal_target_bindings':64,'formal_retain_bindings':448,
            'world_counts':{'meta':64,'dev':16,'final':32},
            'not_validated':'tokenizer difficulty, parent acquisition, LLM learning, language corpus, MUSA'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--seed',type=int,default=314159)
    p.add_argument('--summary');args=p.parse_args();result=build(pathlib.Path(args.out),args.seed)
    print(json.dumps(result,indent=2))
    if args.summary:pathlib.Path(args.summary).write_text(json.dumps(result,indent=2))
