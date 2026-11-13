"""Download public pinned models/corpus; never modifies the Python environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('HF_HOME', '/root/autodl-tmp/hf_cache')
os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '120')
import requests
from huggingface_hub import HfApi, snapshot_download


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=['3B', '7B'], required=True)
    args = parser.parse_args()
    repo = f'Qwen/Qwen2.5-{args.model}'
    out = Path('/root/autodl-tmp/models') / repo.split('/')[1]
    record = Path('records') / f'model_{args.model}.json'
    record.parent.mkdir(exist_ok=True)
    start = time.time()
    api = HfApi()
    info = api.model_info(repo, files_metadata=True)
    revision = info.sha
    print(json.dumps({'event': 'model_resolved', 'repo': repo, 'revision': revision,
                      'endpoint': os.environ['HF_ENDPOINT']}, ensure_ascii=False), flush=True)
    patterns = ['*.safetensors', '*.json', 'merges.txt', 'vocab.json',
                'tokenizer*', 'README.md', 'LICENSE*', '*.tiktoken']
    snapshot_download(repo, revision=revision, local_dir=str(out),
                      allow_patterns=patterns, max_workers=3)
    files = {str(p.relative_to(out)): {'bytes': p.stat().st_size, 'sha256': sha256(p)}
             for p in sorted(out.rglob('*')) if p.is_file() and '.cache' not in p.parts}
    for sibling in info.siblings:
        if sibling.rfilename in files and sibling.lfs:
            expected = sibling.lfs.sha256
            assert files[sibling.rfilename]['sha256'] == expected, sibling.rfilename
    result = {'status': 'PASS', 'repo': repo, 'revision': revision, 'path': str(out),
              'endpoint': os.environ['HF_ENDPOINT'], 'files': files,
              'download_and_hash_seconds': time.time() - start,
              'license': getattr(info.card_data, 'license', None),
              'resolved_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    record.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
