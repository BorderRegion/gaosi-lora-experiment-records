"""Run actual manifest-producing main against isolated simulated leaf metadata.

No model imports, training, controller launches, or scientific claims. This
checks a producer/consumer path contract missed by the original CPU validation.
"""
import ast
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_observed_v8_pipeline import MODULES, transform as original_transform


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        json.dump(value, handle)


def nodes(text):
    return {n.name: n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}


def expected_records(text):
    functions = nodes(text)
    found = {}
    for name, label in [('evaluate_seed', 'recovery'), ('continual_seed', 'continual')]:
        calls = [n.value for n in functions[name].body if isinstance(n, ast.Expr)
                 and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                 and n.value.func.id == 'preserve_json'
                 and label + '_ready' in ast.unparse(n.value.args[0])]
        assert len(calls) == 1
        found[label] = calls[0]
    return found


def run_main(text, fixture_root):
    functions = nodes(text)
    writes = expected_records(text)
    write('checks/confirmation_cpu.json', {'status': 'PASS', 'scope': 'SIMULATED'})
    write('records/phi_architecture_selected.json', {})
    write('records/all_phi_frozen.json', {'manifests': {'O1': 'records/fixture_freeze.json'}})
    write('records/fixture_freeze.json', {'recovery_K': 32})
    write('records/final_parents_ready.json', {'matched_R_exposure': True,
                                            'phi_frozen_before_training': True})
    write('records/factor_recovery_frozen_v1.json', {'formal_data_used': False, 'selection_K': 32})
    write('records/entity_reflow_parents_ready_v1.json', {})
    write('records/reflow_lrs_frozen_v1.json', {'formal_data_used': False, 'recovery_K': 32})

    def emit(label, seed):
        call = writes[label]
        env = {'seed': seed, 'outcomes': [], 'tasks': [('SIMULATED_TASK', 'SIMULATED_DATA')],
               'path': 'SIMULATED_SCHEDULE', 'second': 'O2',
               'pilot': {'base_reference': {'scope': 'SIMULATED'}}, 'preserve_json': write}
        module = ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=copy.deepcopy(call))],
                                                     type_ignores=[]))
        exec(compile(module, '<actual_producer_metadata_call>', 'exec'), env)

    env = {'json': json, 'Path': Path, 'POLICY': {'independent_phi_seeds': [29, 43]},
           'preserve_json': write, 'wait_for': lambda *args: {},
           'select_second': lambda *args: {'organization': 'O2'},
           'train_seed': lambda *args: {},
           'evaluate_seed': lambda seed, *args: emit('recovery', seed),
           'continual_seed': lambda seed, *args: emit('continual', seed)}
    module = ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(functions['main'])], type_ignores=[]))
    exec(compile(module, '<actual_controller_main>', 'exec'), env)
    env['main']()
    summary = read('records/confirmation_ready_v1.json')
    paths = {label: [eval(compile(ast.Expression(copy.deepcopy(call.args[0])), '<writer_path>', 'eval'),
                         {'seed': seed}) for seed in [29, 43]] for label, call in writes.items()}
    return summary, paths


def check(summary, paths):
    assert summary['seeds'] == [29, 43]
    assert not summary['overall_experiment_completion']
    statuses = {'recovery': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE',
                'continual': 'INDEPENDENT_EIGHT_ROUND_CONFIRMATION_COMPLETE'}
    for label, expected in paths.items():
        assert summary[label + '_records'] == expected, 'summary paths differ from actual producers'
        for seed, path in zip([29, 43], expected):
            record = read(path)
            assert record['seed'] == seed and record['status'] == statuses[label]


def rejected(call):
    try:
        call()
    except (AssertionError, FileNotFoundError):
        return True
    raise AssertionError('Invalid metadata contract was accepted')


def fixture(text, test):
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='gaosi_confirmation_paths_') as root:
        try:
            os.chdir(root)
            summary, paths = run_main(text, root)
            return test(summary, paths)
        finally:
            os.chdir(previous)


def main():
    original = (HERE / 'original/confirmation_queue.py').read_text()
    candidate = (HERE / 'candidate/confirmation_queue.py').read_text()
    legacy = (ROOT / 'tools/confirmation_queue.py').read_text()
    results = []
    fixture(legacy, check)
    results.append('Original legacy namespace remains internally consistent.')
    fixture(original, lambda summary, paths: rejected(lambda: check(summary, paths)))
    results.append('Current generated v8 manifest fails the real writer/manifest contract.')
    fixture(candidate, check)
    results.append('Candidate main references all four actual produced metadata files.')

    def adversarial(summary, paths):
        check(summary, paths)
        changed = copy.deepcopy(summary)
        changed['recovery_records'][1] = changed['recovery_records'][0]
        rejected(lambda: check(changed, paths))
        changed = copy.deepcopy(summary)
        changed['continual_records'] = list(reversed(changed['continual_records']))
        rejected(lambda: check(changed, paths))
        changed = copy.deepcopy(summary)
        changed['recovery_records'] = [f'records/confirmation_recovery_ready_s{s}_v1.json' for s in [29, 43]]
        for seed, path in zip([29, 43], changed['recovery_records']):
            write(path, {'status': 'INDEPENDENT_MAIN_SELECTIVE_REFLOW_COMPLETE', 'seed': seed,
                         'scope': 'STALE_HISTORICAL_FIXTURE'})
        rejected(lambda: check(changed, paths))
        target = Path(paths['continual'][1])
        target.unlink()
        rejected(lambda: check(summary, paths))
        write(target, {'status': 'INDEPENDENT_EIGHT_ROUND_CONFIRMATION_COMPLETE', 'seed': 29})
        rejected(lambda: check(summary, paths))

    fixture(candidate, adversarial)
    results.append('Reject duplicate, reordered, stale-existing, missing and wrong-seed paths.')

    before, after = nodes(original), nodes(candidate)
    assert before.keys() == after.keys()
    for name in before:
        if name != 'main':
            assert ast.dump(before[name]) == ast.dump(after[name]), name
    assert [ast.dump(n) for n in before['main'].body[:-1]] == [ast.dump(n) for n in after['main'].body[:-1]]
    results.append('All training/evaluation/continual functions and main scheduling before final manifest are unchanged.')

    spec = importlib.util.spec_from_file_location('candidate_builder', HERE / 'candidate/build_observed_v8_pipeline.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for name in MODULES:
        source = (ROOT / 'tools' / (name + '_queue.py')).read_text()
        old, new = original_transform(name, source), builder.transform(name, source)
        if name == 'confirmation':
            assert old == original and new == candidate
        else:
            assert old == new, name
    results.append('Candidate builder changes only the confirmation final manifest across all fifteen controllers.')
    record = {'status': 'PATH_CONTRACT_REGRESSION_PASS_CANDIDATE_NOT_ACTIVATED',
              'validated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'checks': results, 'simulated_leaf_metadata_only': True,
              'training_or_runtime_correctness_inferred': False,
              'live_sources_modified': False,
              'source_sha256': {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [Path(__file__), HERE / 'prepare_candidate.py',
                                          HERE / 'candidate/build_observed_v8_pipeline.py',
                                          HERE / 'candidate/confirmation_queue.py']}}
    with (HERE / 'validation.json').open('x') as handle:
        json.dump(record, handle, indent=2)
        handle.write('\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
