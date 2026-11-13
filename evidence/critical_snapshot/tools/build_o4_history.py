"""Explicitly merge a verified meta parent into one O4 historical FP32 base."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
import torch_musa
from gaosi.model import AdapterModel
from gaosi.merge import MergeMaster


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--parent', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    verified = json.loads((Path(a.parent).parent / 'result.json').read_text())
    assert verified['acquisition_gate_passed']
    data = torch.load(a.parent, map_location='cpu', weights_only=False)
    c = data['config']
    assert 'final' not in c['data']
    model = AdapterModel(c['model'], c['rank'], c['targets'], 'O4', 'musa')
    model.load_adapter(data['adapter'])
    master = MergeMaster(model)
    info = master.merge(model)
    master.save(a.out, c['base_revision'])
    manifest = {'status': 'PASS', 'parent': a.parent, 'historical_bindings': c['data'],
                'current_adapter_requirement': 'Must independently train another allowed meta binding set on this historical base before O4 phi specialization',
                'master': a.out, 'master_digest': master.digest(), 'merge': info}
    Path(a.out + '.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
