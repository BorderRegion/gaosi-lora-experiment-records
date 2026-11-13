import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time

import numpy as np
import torch


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if hasattr(torch, 'musa') and torch.musa.is_available():
        torch.musa.manual_seed_all(seed)


def rng_state():
    state = {'python': random.getstate(), 'numpy': np.random.get_state(),
             'torch': torch.get_rng_state()}
    if hasattr(torch, 'musa') and torch.musa.is_available():
        state['musa'] = torch.musa.get_rng_state_all()
    return state


def restore_rng(state):
    random.setstate(state['python'])
    np.random.set_state(state['numpy'])
    torch.set_rng_state(state['torch'])
    if 'musa' in state:
        torch.musa.set_rng_state_all(state['musa'])


def atomic_save(value, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    torch.save(value, temporary)
    temporary.replace(path)


class Run:
    def __init__(self, root, config, resume=False):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)
        self.config = config
        config_path = self.path / 'config.json'
        if config_path.exists():
            if not resume:
                raise RuntimeError(f'Run already exists: {root}')
            if json.loads(config_path.read_text()) != config:
                raise RuntimeError('Resume config differs from the saved configuration')
        else:
            config_path.write_text(json.dumps(config, indent=2))
            source = Path(__file__).resolve().parent
            snapshot = self.path / 'source'
            snapshot.mkdir(exist_ok=True)
            hashes = {}
            for file in sorted(source.glob('*.py')):
                shutil.copy2(file, snapshot / file.name)
                hashes[file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
            (self.path / 'source_sha256.json').write_text(json.dumps(hashes, indent=2))
        self.started = time.time()

    def log(self, event, **details):
        data = {'event': event, 'utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'process_wall_seconds': time.time() - self.started, **details}
        with (self.path / 'events.jsonl').open('a') as f:
            f.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + '\n')
            f.flush()
        print(json.dumps(data, ensure_ascii=False, allow_nan=False), flush=True)

    def result(self, **data):
        (self.path / 'result.json').write_text(json.dumps(data, indent=2, ensure_ascii=False))
