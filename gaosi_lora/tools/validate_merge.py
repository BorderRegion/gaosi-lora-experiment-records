"""Check persistent-master accumulation and double-branch merge/reset."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace

import torch
from gaosi.model import NativeLoRA
from gaosi.merge import MergeMaster


class Toy(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.proj = NativeLoRA(torch.nn.Linear(9, 7, bias=False).bfloat16(), 4, 'O3')
        self.specs = {k: SimpleNamespace(factor=k[-1]) for k, v in self.named_parameters()
                      if '.branches.' in k}
        self.rank = 4

    def adapter(self):
        return {k: v for k, v in self.named_parameters() if k in self.specs}


def main():
    torch.manual_seed(17)
    model = Toy()
    master = MergeMaster(model)
    expected = master.weights['proj.weight'].clone()
    for step in range(16):
        with torch.no_grad():
            for branch in model.proj.branches.values():
                branch.B.normal_(0., 1e-4)
                expected.add_(branch.B @ branch.A, alpha=branch.scale)
        info = master.merge(model)
        torch.testing.assert_close(master.weights['proj.weight'], expected, atol=1e-7, rtol=1e-6)
        assert all(not torch.count_nonzero(b.B) for b in model.proj.branches.values())
    assert master.rank_sum == 64 and master.merges == 16
    assert all(v.dtype == torch.float32 for v in master.weights.values())
    result = {'status': 'PASS', 'scope': 'CPU persistent FP32 O4 math check, not a continual-learning result',
              'merges': master.merges, 'rank_bound': master.rank_sum,
              'working_rounding_record': info}
    Path('checks/merge_master_cpu.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
