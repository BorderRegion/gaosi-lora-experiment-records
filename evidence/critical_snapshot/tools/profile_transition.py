"""Real-model Gaosi moment/feature/H1/H2 readiness; no effect claim."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
import torch_musa

from gaosi.data import rows, FactStream, LanguageStream, route_batches
from gaosi.model import AdapterModel, memory, synchronize
from gaosi.meta import rollout, QueryStream, functional_loss
from gaosi.noise import canonical_noise
from gaosi.runtime import seed_all
from gaosi.transition import Phi, scalar_stats, detach_tree, zero_state


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--seq', type=int, default=128)
    p.add_argument('--effective-tokens', type=int, default=32)
    p.add_argument('--architecture', choices=['L', 'X'], required=True)
    p.add_argument('--horizon', choices=['H1', 'EXACT_H2_tail'], required=True)
    p.add_argument('--retain-facts', action='store_true')
    args = p.parse_args()
    torch.set_num_threads(8)
    seed_all(17)
    result = {'status': 'RUNNING', 'scope': 'real MUSA production transition readiness, not learned denoising effect',
              'command': vars(args)}
    try:
        checkpoint = torch.load(args.checkpoint, map_location='cpu', weights_only=False)
        c = checkpoint['config']
        model = AdapterModel(c['model'], c['rank'], c['targets'], c['organization'], 'musa', scale=c['scale'])
        result['model'] = model.summary()
        clean, _ = canonical_noise(checkpoint['adapter'], model.specs, 0., 17)
        model.load_adapter(clean)
        stats = detach_tree({k: scalar_stats(v) for k, v in model.adapter().items()})
        noisy, noise = canonical_noise(checkpoint['adapter'], model.specs, .03, 17)
        parameters = {k: v.to('musa').requires_grad_(True) for k, v in noisy.items()}
        phi = Phi(args.architecture).to('musa')
        streams = {'task': FactStream(rows(c['data'], 'support'), model.tokenizer, 17, args.seq),
                   'language': LanguageStream('data/language/inner.jsonl', model.tokenizer, args.seq, 17)}
        if args.retain_facts:
            streams['keep'] = FactStream(rows(c['data'], 'support'), model.tokenizer, 17, args.seq)
        queries = {}
        for key in ['task', 'keep']:
            qs = QueryStream(rows(c['data'], 'query'), model.tokenizer, seed=99, seq=args.seq)
            queries[key], _ = route_batches(qs, 'musa', args.effective_tokens)
        qs = LanguageStream('data/language/meta_query.jsonl', model.tokenizer, args.seq, 99)
        queries['language'], _ = route_batches(qs, 'musa', args.effective_tokens)
        with torch.no_grad():
            reference = {key: functional_loss(model, {}, batches) for key, batches in queries.items()}
        phi.zero_grad(set_to_none=True)
        torch.musa.reset_peak_memory_stats()
        synchronize()
        started = time.time()
        parameters, state, diag = rollout(model, parameters, zero_state(parameters), phi,
                                           streams, stats, 2, .03, 1e-4,
                                           args.effective_tokens, .25, args.horizon,
                                           queries, reference, chunk=262144,
                                           log_callback=lambda **x: print(json.dumps(x), flush=True))
        synchronize()
        gradients = [p.grad for p in phi.parameters() if p.grad is not None]
        norm = sum(float(g.detach().square().sum().cpu()) for g in gradients) ** .5
        assert norm > 0 and all(torch.isfinite(g).all() for g in gradients)
        result.update(status='PASS', seconds=time.time() - started, terminal=diag,
                      phi_gradient_norm=norm, memory=memory(), inner_steps=2,
                      compute_counters=model.compute_counters,
                      full_state_components=['adapter', 'mD', 'mR', 'v', 'loss_ema', 'gradient_cosine_ema',
                                             'update_ema', 'previous_loss', 'last_relative_update'],
                      noise_summary={k: v for k, v in noise.items() if k != 'factors'})
    except BaseException as error:
        result.update(status='FAIL', error=repr(error), traceback=traceback.format_exc(), memory=memory())
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
