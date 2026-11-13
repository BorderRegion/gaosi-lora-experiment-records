"""Validate exclusive metadata aliases and actual unchanged summary consumers."""
import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from install_aliases import create_links, mapping
from validate_candidate import run_main, expected_records


def isolated(call):
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='gaosi_metadata_alias_') as temporary:
        try:
            os.chdir(temporary)
            Path('records').mkdir()
            return call(Path(temporary))
        finally:
            os.chdir(previous)


def reject(call):
    try:
        call()
    except RuntimeError:
        return
    raise AssertionError('Existing metadata collision was accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    results = []

    def actual_producer(root):
        links = create_links(root)
        assert all(not Path(name).exists() for name in links)
        text = (HERE / 'original/confirmation_queue.py').read_text()
        summary, paths = run_main(text, root)
        for kind in ['recovery', 'continual']:
            for alias, target in zip(summary[kind + '_records'], paths[kind]):
                assert Path(alias).is_symlink() and Path(alias).resolve() == Path(target).resolve()
                assert Path(alias).read_bytes() == Path(target).read_bytes()
        target = Path(paths['recovery'][0])
        target.unlink()
        assert not Path(summary['recovery_records'][0]).exists()

    isolated(actual_producer)
    results.append('Actual original producer main resolves all four manifest references; aliases never imply premature or retained completion.')

    def collisions(root):
        name, target = list(mapping().items())[-1]
        alias = Path(name)
        alias.write_bytes(b'PRESERVED_HISTORICAL_RESULT')
        reject(lambda: create_links(root))
        assert alias.read_bytes() == b'PRESERVED_HISTORICAL_RESULT'
        assert all(not os.path.lexists(p) for p in list(mapping())[:-1])
        alias.unlink()
        os.symlink('stale_target.json', alias)
        reject(lambda: create_links(root))
        assert os.readlink(alias) == 'stale_target.json'
        alias.unlink()
        (alias.parent / target).write_bytes(b'PRESERVED_TARGET')
        reject(lambda: create_links(root))
        assert (alias.parent / target).read_bytes() == b'PRESERVED_TARGET'

    isolated(collisions)
    results.append('Existing historical files, stale symlinks and existing targets reject before any alias creation.')

    if args.full:
        for family in ['recovery', 'selective', 'reflow', 'continual']:
            validator = importlib.import_module('validate_' + family + '_statistics')
            summary_module = importlib.import_module('summarize_' + family)

            def consumer(root):
                validator.fixture()
                expected = summary_module.summarize('full', draws=20)
                ready_file = Path('records/confirmation_ready_v1.json')
                ready = json.loads(ready_file.read_text())
                links = create_links(root)
                kind = 'continual' if family == 'continual' else 'recovery'
                aliases = [p for p in links if f'confirmation_{kind}_' in p]
                for original, alias in zip(ready[kind + '_records'], aliases):
                    Path(original).rename(Path(alias).parent / links[alias])
                ready[kind + '_records'] = aliases
                ready_file.write_text(json.dumps(ready))
                actual = summary_module.summarize('full', draws=20)
                # Only the source path/hash ledger changes; statistical results stay identical.
                assert set(expected) == set(actual)
                changed = [key for key in expected if expected[key] != actual[key]]
                assert changed and all('provenance' in key or 'source' in key for key in changed), changed
                summary_module.write_summary(actual, 'fixture_export')
                assert Path('fixture_export/result.json').exists()

            isolated(consumer)
            results.append(f'{family}: unchanged full-grid parser/statistics/export pass with aliases; numerical summary identical to baseline.')
    record = {'status': ('ALL_FOUR_CONSUMERS_ALIAS_INTEGRATION_PASS' if args.full else 'ALIAS_CONTRACT_PASS'),
              'validated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'checks': results, 'simulated_metadata_and_statistics_only': True,
              'real_training_or_effect_claimed': False,
              'source_sha256': {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [Path(__file__), HERE / 'install_aliases.py']}}
    with args.out.open('x') as handle:
        json.dump(record, handle, indent=2)
        handle.write('\n')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
