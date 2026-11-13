"""Fix the unexecuted staged queue's result-schema mismatch, preserving v1."""
import datetime as dt
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / 'records/clean_schedule_pairing_20261110_1407/organization_queue_corrected.py'


def main():
    original = SOURCE.read_text()
    old = """    preserve_json('records/clean_training_stage_pairing_v1.json', {
        'status': 'TWO_ACTUAL_CLEAN_STAGES_COMPLETED_AND_REVIEWED',
        'core': clean_core_result['config']['out'], 'specialization': clean_result['config']['out'],"""
    new = """    # gaosi.meta writes settings to config.json; its result.json deliberately
    # omits config. Validate the actual sidecars instead of assuming a field.
    clean_configs = []
    for result, settings in [(clean_core_result, clean_core), (clean_result, clean)]:
        directory = Path(result['checkpoint']).parent
        saved = json.loads((directory / 'config.json').read_text())
        if (saved != dict(settings, out=str(directory))
                or Path(result['checkpoint']) != directory / 'latest.pt'
                or result['inner_updates'] != sum(settings['episode_K'])):
            raise ValueError('Actual clean result/config/checkpoint differs from its paired stage')
        clean_configs.append(saved)
    if clean_configs[1]['initial_phi'] != clean_core_result['checkpoint']:
        raise ValueError('Clean specialization does not inherit its actual clean core')
    preserve_json('records/clean_training_stage_pairing_v1.json', {
        'status': 'TWO_ACTUAL_CLEAN_STAGES_COMPLETED_AND_REVIEWED',
        'core': clean_configs[0]['out'], 'specialization': clean_configs[1]['out'],"""
    assert original.count(old) == 1
    corrected = original.replace(old, new)
    output = HERE / 'organization_queue_corrected_v2.py'
    compile(corrected, str(output), 'exec')
    with output.open('x') as handle:
        handle.write(corrected)
    difference = HERE / 'organization_queue_corrected_v2.py.diff'
    with difference.open('x') as handle:
        handle.write(''.join(difflib.unified_diff(original.splitlines(True), corrected.splitlines(True),
                     fromfile=str(SOURCE.relative_to(ROOT)), tofile=str(output.relative_to(ROOT)))))
    value = {'status': 'UNEXECUTED_PRIMARY_QUEUE_SCHEMA_CORRECTION_PREPARED',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'issue': 'Production gaosi.meta result has no config field; v1 assumed one when recording paired clean stages.',
             'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
             'corrected_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
             'original_numerical_sources_modified': False,
             'training_or_controller_modified': False, 'method_validated': False, 'full_goal_complete': False}
    with (HERE / 'preparation.json').open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
    print(json.dumps(value))


if __name__ == '__main__':
    main()
