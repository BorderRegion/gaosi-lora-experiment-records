"""Compare the uniform numeric path with independent tiny direct AD/H4/H8."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))


def main():
    directory = Path(__file__).resolve().parent
    output = directory / 'uniform_direct_chain_cpu.json'
    assert not output.exists()
    spec = importlib.util.spec_from_file_location('uniform', directory / 'run.py')
    uniform = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uniform)
    spec = importlib.util.spec_from_file_location('direct_validation', ROOT / 'tools/validate_horizon.py')
    validation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validation)
    from gaosi import meta
    original = meta.support_gradients

    def support(model, parameters, batches, create_graph=False):
        return uniform.uniform_support(original, model, parameters, batches, create_graph)

    sys.argv = [str(Path(__file__)), '--out', str(output)]
    with patch.object(meta, 'support_gradients', support):
        validation.main()


if __name__ == '__main__':
    main()
