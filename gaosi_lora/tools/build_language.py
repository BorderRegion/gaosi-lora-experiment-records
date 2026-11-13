"""Document-disjoint WikiText pools, fixed before any adapter training."""
import hashlib
import json
import os
from pathlib import Path
import random
import re

os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('HF_HOME', '/root/autodl-tmp/hf_cache')
from huggingface_hub import HfApi, hf_hub_download
import pyarrow.parquet as pq


def main():
    repo = 'Salesforce/wikitext'
    info = HfApi().dataset_info(repo)
    revision = info.sha
    root = Path('data/language')
    root.mkdir(parents=True, exist_ok=True)
    documents = []
    sources = []
    for split in ['train', 'validation', 'test']:
        name = f'wikitext-2-raw-v1/{split}-00000-of-00001.parquet'
        path = Path(hf_hub_download(repo, name, repo_type='dataset', revision=revision))
        sources.append({'file': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        current = []
        for text in pq.read_table(path, columns=['text'])['text'].to_pylist():
            # A level-one heading starts a new Wikipedia article. Lower-level
            # headings stay with it, so adjacent paragraphs cannot leak.
            if re.match(r'^\s*= [^=].*[^=] =\s*$', text):
                if current:
                    documents.append((split, ''.join(current)))
                current = [text]
            else:
                current.append(text)
        if current:
            documents.append((split, ''.join(current)))
    unique = {}
    for split, text in documents:
        normalized = ' '.join(text.split())
        if len(normalized) < 256:
            continue
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        unique.setdefault(digest, (split, text))
    ids = sorted(unique)
    random.Random(271828).shuffle(ids)
    pools = {k: [] for k in ['inner', 'meta_query', 'dev', 'final']}
    for i, digest in enumerate(ids):
        pool = list(pools)[i % 4]
        original_split, text = unique[digest]
        pools[pool].append({'document_id': digest, 'text': text,
                            'original_split': original_split, 'pool': pool})
    files = {}
    for pool, rows in pools.items():
        path = root / f'{pool}.jsonl'
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        files[pool] = {'documents': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest = {'status': 'PASS', 'repo': repo, 'revision': revision,
                'license': 'cc-by-sa-3.0', 'sources': sources, 'pools': files,
                'split_unit': 'whole_article_before_token_chunking',
                'seed': 271828, 'unique_documents': len(ids),
                'note': 'Original corpus splits reshuffled into four new article-disjoint pools; no standard WikiText benchmark score is claimed.'}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    main()
