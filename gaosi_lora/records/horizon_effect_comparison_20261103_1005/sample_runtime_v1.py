"""Fetch and rehash H1 comparison text evidence after an existing scheduled sample."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
SSH = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observation', required=True)
    args = parser.parse_args()
    anchor = ROOT / args.observation
    source = RECORD / 'collect_runtime_v1.py'
    deployment = json.loads((RECORD / 'runtime_deployment_v1.json').read_text())
    assert sha(source) == deployment['files_sha256']['collect_runtime_v1.py']
    output = ROOT / str(Path(args.observation)).replace('pipeline_monitor_', 'horizon_effect_monitor_')
    assert output.parent == ROOT / 'records' and not output.exists()
    assert not Path(str(output) + '.tar.gz').exists()
    remote_source = '/root/autodl-tmp/gaosi_lora/' + str(source.relative_to(ROOT))
    command = '/usr/bin/python ' + shlex.quote(remote_source) + ' --observation ' + shlex.quote(args.observation)
    reply = subprocess.run(SSH + [command], text=True, capture_output=True, check=True, timeout=180)
    info = json.loads(reply.stdout)
    archive = ROOT / info['archive']
    assert archive == Path(str(output) + '.tar.gz')
    with archive.open('xb') as handle:
        subprocess.run(SSH + ['cat ' + shlex.quote('/root/autodl-tmp/gaosi_lora/' + info['archive'])],
                       stdout=handle, stderr=subprocess.PIPE, check=True, timeout=180)
    assert sha(archive) == info['sha256']
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            target = (ROOT / member.name).resolve()
            assert member.isfile() and target.is_relative_to(output) and not target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as src, target.open('xb') as dest:
                dest.write(src.read())
    manifest = json.loads((output / 'archive_source_sha256.json').read_text())
    for name, expected in manifest.items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(output) and sha(path) == expected
    result = json.loads((output / 'result.json').read_text())
    assert result == info['result']
    assert result['anchor'] == args.observation and result['anchor_result_sha256'] == sha(anchor / 'result.json')
    assert result['collector_sha256'] == sha(source) and result['gpu_lock_inode'] == 31148027670
    assert not result['alerts'], 'Preserve evidence and inspect alerts; never restart automatically'
    review = {'status': 'HORIZON_COMPARISON_SUPPLEMENT_ARCHIVE_INDEPENDENTLY_REHASHED_TEXT_ONLY',
              'reviewed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source': str(Path(__file__).relative_to(ROOT)), 'source_sha256': sha(Path(__file__)),
              'observation': str(output.relative_to(ROOT)), 'archive_sha256': sha(archive),
              'files_independently_rehashed': len(manifest),
              'pass_jobs': result['pass_jobs'], 'running_jobs': result['running_jobs'],
              'running': [{k: row[k] for k in ['name', 'identity', 'recorded_recovery_steps', 'evaluation_steps', 'last_event']}
                          for row in result['jobs'] if row['job']['status'] == 'RUNNING'],
              'declarations_present': result['declarations_present'],
              'comparison_controller': result['comparison_controller'],
              'comparison_controller_identity': result['comparison_controller_identity'],
              'alerts': [], 'training_modified_or_restarted': False, 'method_validated': False,
              'full_goal_complete': False, 'limitations': result['limitations']}
    path = output / 'review.json'
    with path.open('x') as handle:
        json.dump(review, handle, indent=2, allow_nan=False)
        handle.write('\n')
    spec = importlib.util.spec_from_file_location('sync', ROOT / 'records/observed_blocking_core_20261030_0810/review_snapshot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = module.sync_review(path)
    print(json.dumps(review))
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
