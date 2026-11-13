"""Check frozen-boundary differences on a smooth CPU FP64 LoRA fixture."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import torch
from gaosi import meta
from gaosi.model import FactorSpec
from gaosi.runtime import atomic_save
from gaosi.transition import Phi, scalar_stats, zero_state
from diagnose_fixed_tail_gradient import run_captured


class Fixture(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.A = torch.nn.Parameter(torch.randn(2, 3, dtype=torch.float64) * .1)
        self.B = torch.nn.Parameter(torch.randn(3, 2, dtype=torch.float64) * .1)
        self.llm = SimpleNamespace(config=SimpleNamespace(num_hidden_layers=1))
        self.specs = {name: FactorSpec(name, 0, 'q_proj', name, 'single', 2, 1., pair)
                      for name, pair in [('A', 'B'), ('B', 'A')]}

    def forward(self, batch):
        predicted = torch.tanh(batch['x'] @ self.A.T @ self.B.T)
        return (predicted - batch['target']).square().mean()


class Stream:
    def __init__(self, batches):
        self.batches, self.cursor = batches, 0

    def batch(self, device):
        result = self.batches[self.cursor]
        self.cursor += 1
        return result


def main():
    torch.set_num_threads(2)
    torch.manual_seed(9153)
    model = Fixture()
    initial = {k: p.detach().clone() for k, p in model.named_parameters()}
    stats = {k: scalar_stats(p) for k, p in initial.items()}

    def batch():
        return {'x': torch.randn(2, 3, dtype=torch.float64),
                'target': torch.randn(2, 3, dtype=torch.float64),
                'labels': torch.ones(1, 3, dtype=torch.long)}

    support = {k: [batch() for _ in range(4)] for k in ['task', 'language', 'keep']}
    queries = {k: [batch()] for k in support}
    results = []
    original = meta.prepare, meta.support_gradients
    for K in [2, 4]:
        phi = Phi('L').double()
        with torch.no_grad():
            phi.net[-1].weight.normal_(0, .01)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            streams = {k: Stream(v) for k, v in support.items()}
            run_captured(meta, torch, atomic_save, output, [.001, .01, .1],
                         model, initial, zero_state(initial), phi, streams, stats,
                         K, .5, .0003, effective_tokens=2, horizon='EXACT_H2_tail',
                         queries=queries, reference={'language': torch.tensor(-100.),
                                                    'keep': torch.tensor(-100.)},
                         log_callback=lambda **kw: None)
            result = json.loads((output / 'tail_sensitivity.json').read_text())
            boundary = torch.load(output / 'tail_boundary.pt', weights_only=False)
            assert boundary['state']['step'] == K - 2
            assert len(boundary['batches']) == 6
            assert result['baseline_terminal_exactly_equal']
            assert result['baseline_inner_exactly_equal']
            assert result['phi_unchanged'] and result['boundary_unchanged']
            assert result['measurements'][0]['relative_discrepancy'] < 1e-4
            assert all(s.cursor == K for s in streams.values())
            assert (meta.prepare, meta.support_gradients) == original
            results.append({'K': K, 'prefix_steps_fixed': K - 2,
                            'relative_discrepancies': [r['relative_discrepancy']
                                                       for r in result['measurements']]})
    print(json.dumps({'status': 'PASS', 'scope': 'Smooth CPU FP64 fixture, not MUSA correctness',
                      'results': results}))


if __name__ == '__main__':
    main()
