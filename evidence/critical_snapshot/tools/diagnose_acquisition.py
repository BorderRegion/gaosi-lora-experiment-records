"""Distinguish isolated support memorization from unseen wording transfer."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
import torch_musa
from gaosi.model import AdapterModel
from gaosi.data import rows
from gaosi.evaluate import evaluate_qa


def main():
    torch.set_num_threads(8)
    job = Path('records/jobs/profile_3b_phi_L_H1.json')
    if job.exists():
        while True:
            state = json.loads(job.read_text())
            if state['status'] != 'RUNNING':
                break
            proc = Path(f"/proc/{state['child_pid']}/stat")
            if not proc.exists() or proc.read_text().split()[21] != state['child_proc_start_ticks']:
                raise RuntimeError('Missing profile child without terminal job record; inspect')
            time.sleep(5)
    ckpt = torch.load('runs/capacity_3b_all7_r32_lr1e-4_s17/latest.pt', map_location='cpu', weights_only=False)
    c = ckpt['config']
    model = AdapterModel(c['model'], c['rank'], c['targets'], c['organization'], 'musa')
    model.load_adapter(ckpt['adapter'])
    data = rows(c['data'])
    selected = {f'dev_parent:e{i}:a{j}' for i in range(0, 64, 8) for j in range(8)}
    results = {}
    for role, n in [('support', 4), ('query', 2)]:
        for variant in range(n):
            examples = [r for r in data if r['role'] == role and r['fact_id'] in selected
                        and r['sample_id'].endswith(':' + str(variant))]
            name = f'{role}_{variant}'
            results[name] = evaluate_qa(model, examples, f'checks/acquisition_diagnosis_{name}.jsonl')
            print(json.dumps({name: results[name]}), flush=True)
    Path('checks/acquisition_diagnosis.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
