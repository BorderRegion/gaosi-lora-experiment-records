from __future__ import annotations

import json
from pathlib import Path
import re

import torch
from torch.nn import functional as F

from .data import collate, qa_tokens


def normalize(text):
    # Strict full-string equality after whitespace/case normalization, never a
    # startswith check. Extra commentary/punctuation remains an error.
    return ' '.join(text.strip().casefold().split())


@torch.no_grad()
def evaluate_qa(model, data, output=None, batch_size=8, generate=True):
    device = next(model.parameters()).device
    tokenizer = model.tokenizer
    flat = [(i, candidate) for i, row in enumerate(data) for candidate in row['candidates']]
    scores_by_row = [[] for _ in data]
    for begin in range(0, len(flat), batch_size):
        chunk = flat[begin:begin + batch_size]
        batch = collate([qa_tokens(tokenizer, data[i], c) for i, c in chunk], tokenizer, device)
        hidden = model.llm.model(input_ids=batch['input_ids'],
                                attention_mask=batch['attention_mask'], use_cache=False).last_hidden_state
        targets = batch['labels'][:, 1:]
        valid = targets.ne(-100)
        logits = model.llm.lm_head(hidden[:, :-1][valid]).float()
        token_scores = -F.cross_entropy(logits, targets[valid], reduction='none')
        indices = torch.arange(len(chunk), device=device).unsqueeze(1).expand_as(targets)[valid]
        sums = torch.zeros(len(chunk), device=device).scatter_add_(0, indices, token_scores).cpu().tolist()
        for (i, candidate), score in zip(chunk, sums):
            scores_by_row[i].append(score)
    generations = []
    if generate:
        previous_side = tokenizer.padding_side
        tokenizer.padding_side = 'left'
        try:
            for begin in range(0, len(data), batch_size):
                chunk = data[begin:begin + batch_size]
                ids = tokenizer([row['prompt'] for row in chunk], add_special_tokens=False,
                                padding=True, return_tensors='pt')
                ids = {k: v.to(device) for k, v in ids.items()}
                # A new generate call creates a new cache; no state survives an
                # adapter write/merge. Training always uses use_cache=False.
                generated = model.llm.generate(**ids, do_sample=False, max_new_tokens=16,
                                               use_cache=True, pad_token_id=tokenizer.pad_token_id)
                generations.extend(tokenizer.batch_decode(generated[:, ids['input_ids'].shape[1]:],
                                                           skip_special_tokens=True))
        finally:
            tokenizer.padding_side = previous_side
    examples = []
    for index, row in enumerate(data):
        candidates = row['candidates']
        scores = scores_by_row[index]
        correct = candidates.index(row['answer'])
        _, correct_labels = qa_tokens(tokenizer, row)
        answer_tokens = sum(t != -100 for t in correct_labels[1:])
        best = max(range(len(scores)), key=scores.__getitem__)
        margin = scores[correct] - max(s for i, s in enumerate(scores) if i != correct)
        result = {'sample_id': row['sample_id'], 'fact_id': row.get('fact_id'),
                  'world_id': row['world_id'], 'answer': row['answer'],
                  'candidate_scores': dict(zip(candidates, scores)),
                  'rank_correct': int(best == correct), 'margin': margin,
                  'correct_sequence_nll': -scores[correct], 'answer_tokens': answer_tokens}
        if generate:
            text = generations[index]
            result.update(generation=text, strict_EM=int(normalize(text) == normalize(row['answer'])),
                          abnormal=int(not text.strip() or len(text.split()) > 8))
        examples.append(result)
    n = len(examples)
    result = {'n': n, 'candidate_accuracy': sum(x['rank_correct'] for x in examples) / n,
              'mean_margin': sum(x['margin'] for x in examples) / n,
              'nll': sum(x['correct_sequence_nll'] for x in examples) / sum(x['answer_tokens'] for x in examples),
              'answer_tokens': sum(x['answer_tokens'] for x in examples)}
    if generate:
        result.update(strict_EM=sum(x['strict_EM'] for x in examples) / n,
                      abnormal_rate=sum(x['abnormal'] for x in examples) / n)
    if output is not None:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in examples))
    return result


@torch.no_grad()
def evaluate_language(model, stream, tokens=4096):
    device = next(model.parameters()).device
    previous = stream.state_dict()
    total_loss, count = 0., 0
    while count < tokens:
        batch = stream.batch(device)
        n = int(batch['labels'][:, 1:].ne(-100).sum().cpu())
        total_loss += float(model(batch).cpu()) * n
        count += n
    stream.load_state_dict(previous)
    return {'nll': total_loss / count, 'answer_tokens': count}
