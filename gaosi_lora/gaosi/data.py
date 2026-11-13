from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random

import torch


def rows(path, role=None):
    result = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return [r for r in result if role is None or r['role'] == role]


def qa_tokens(tokenizer, row, answer=None):
    prompt = tokenizer.encode(row['prompt'], add_special_tokens=False)
    answer = row['answer'] if answer is None else answer
    completion = tokenizer.encode(answer, add_special_tokens=False) + [tokenizer.eos_token_id]
    # Tokenize pieces consistently for both training and candidate scoring;
    # the model sees the exact Base continuation prefix, no chat template.
    return prompt + completion, [-100] * len(prompt) + completion


def collate(examples, tokenizer, device):
    width = max(len(x[0]) for x in examples)
    ids, labels, mask = [], [], []
    segments, positions = [], []
    for example in examples:
        tokens, targets = example[:2]
        n = width - len(tokens)
        ids.append(tokens + [tokenizer.pad_token_id] * n)
        labels.append(targets + [-100] * n)
        mask.append([1] * len(tokens) + [0] * n)
        if len(example) == 3:
            segment = example[2]
            segments.append(segment + [-1] * n)
            pos, previous = [], None
            current = 0
            for item in segment:
                current = current + 1 if item == previous else 0
                pos.append(current)
                previous = item
            positions.append(pos + [0] * n)
    result = {k: torch.tensor(v, dtype=torch.long, device=device)
              for k, v in [('input_ids', ids), ('labels', labels), ('attention_mask', mask)]}
    if segments:
        result['segment_ids'] = torch.tensor(segments, dtype=torch.long, device=device)
        result['position_ids'] = torch.tensor(positions, dtype=torch.long, device=device)
    return result


class FactStream:
    """Cycle binding IDs before varying wording, with a serializable cursor."""
    def __init__(self, data, tokenizer, seed=17, seq=512, microbatch=1):
        if any(r.get('role') != 'support' for r in data):
            raise ValueError('Training stream accepts support expressions only')
        self.data = data
        self.tokenizer = tokenizer
        self.seed = seed
        self.seq = seq
        self.microbatch = microbatch
        self.groups = {}
        for row in data:
            self.groups.setdefault(row.get('fact_id', row['sample_id']), []).append(row)
        self.ids = sorted(self.groups)
        self._order_lap = None
        self._order = None
        self.cursor = 0
        self.covered = set()
        self.exposures = 0
        self.supervised_tokens = 0
        self.input_tokens = 0
        self._pending = None

    def next_row(self):
        if self._pending is not None:
            row = self._pending
            self._pending = None
            return row
        lap = self.cursor // len(self.ids)
        if self._order_lap != lap:
            self._order = self.ids.copy()
            random.Random(self.seed + 104729 * lap).shuffle(self._order)
            self._order_lap = lap
        key = self._order[self.cursor % len(self.ids)]
        group = self.groups[key]
        row = group[lap % len(group)]
        self.cursor += 1
        return row

    def batch(self, device):
        examples, used = [], []
        for _ in range(self.microbatch):
            tokens, labels, segments = [], [], []
            segment_id = 0
            while True:
                row = self.next_row()
                x, y = qa_tokens(self.tokenizer, row)
                if len(x) > self.seq:
                    raise ValueError('An entire answer would be truncated')
                if len(tokens) + len(x) > self.seq:
                    self._pending = row
                    break
                tokens.extend(x)
                labels.extend(y)
                segments.extend([segment_id] * len(x))
                segment_id += 1
                used.append(row)
            examples.append((tokens, labels, segments))
        for row in used:
            self.covered.add(row.get('fact_id', row['sample_id']))
        self.exposures += len(used)
        self.supervised_tokens += sum(sum(t != -100 for t in example[1][1:]) for example in examples)
        self.input_tokens += sum(len(example[0]) for example in examples)
        return collate(examples, self.tokenizer, device)

    def state_dict(self):
        return {'cursor': self.cursor, 'covered': sorted(self.covered), 'exposures': self.exposures,
                'supervised_tokens': self.supervised_tokens, 'input_tokens': self.input_tokens,
                'pending': self._pending, 'seed': self.seed}

    def load_state_dict(self, state):
        assert state['seed'] == self.seed
        for key in ['cursor', 'exposures', 'supervised_tokens', 'input_tokens']:
            setattr(self, key, state[key])
        self.covered = set(state['covered'])
        self._pending = state['pending']
        self._order_lap = None


class LanguageStream:
    def __init__(self, path, tokenizer, seq=512, seed=17, microbatch=1):
        self.seed, self.seq, self.microbatch = seed, seq, microbatch
        self.tokenizer = tokenizer
        docs = rows(path)
        random.Random(seed).shuffle(docs)
        self.chunks = []
        self.document_ids = []
        for doc in docs:
            tokens = tokenizer.encode(doc['text'], add_special_tokens=False)
            # No token or context chunk crosses a document boundary.
            for offset in range(0, len(tokens) - 1, seq):
                chunk = tokens[offset:offset + seq]
                if len(chunk) >= 2:
                    self.chunks.append(chunk)
                    self.document_ids.append(doc['document_id'])
        self.cursor = 0
        self.supervised_tokens = 0

    def batch(self, device):
        examples = []
        for _ in range(self.microbatch):
            tokens = self.chunks[self.cursor % len(self.chunks)]
            self.cursor += 1
            examples.append((tokens, tokens))
        self.supervised_tokens += sum(len(x) - 1 for x, _ in examples)
        return collate(examples, self.tokenizer, device)

    def state_dict(self):
        return {'cursor': self.cursor, 'supervised_tokens': self.supervised_tokens, 'seed': self.seed}

    def load_state_dict(self, state):
        assert state['seed'] == self.seed
        self.cursor = state['cursor']
        self.supervised_tokens = state['supervised_tokens']


def token_count(batch):
    return int(batch['labels'][:, 1:].ne(-100).sum().cpu())


def route_batches(stream, device, effective_tokens=512):
    batches = []
    count = 0
    while count < effective_tokens:
        batch = stream.batch(device)
        count += token_count(batch)
        batches.append(batch)
    return batches, count
