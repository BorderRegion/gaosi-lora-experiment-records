"""Add a concise, evidence-bound progress note while preserving document bytes."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'records/pipeline_observer_20261110_1315'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stamp', required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'\d{8}_\d{4}', args.stamp)
    output = HERE / ('documentation_' + args.stamp + '.json')
    assert not output.exists(), 'Preserve an existing documentation receipt'
    verification = HERE / ('observation_verification_' + args.stamp + '.json')
    progress = HERE / ('meta_progress_' + args.stamp + '.json')
    checked, meta = [json.loads(p.read_text()) for p in [verification, progress]]
    assert checked['status'] == 'SCHEDULED_SNAPSHOT_AND_MONITOR_REFERENCES_REHASHED_NO_NEW_ALERT'
    assert not checked['pending_scientific_reviews']
    run, = meta['runs']
    assert run['job_status'] == 'RUNNING'
    observation = checked['observation']
    capture = json.loads((ROOT / observation / 'result.json').read_text())
    instant = dt.datetime.fromisoformat(capture['utc']) if 'utc' in capture else None
    if instant is None:
        label = re.fullmatch(r'records/pipeline_monitor_(\d{8}_\d{6})_utc', observation)[1]
        instant = dt.datetime.strptime(label, '%Y%m%d_%H%M%S').replace(tzinfo=dt.timezone.utc)
    beijing = instant.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    steps = run['committed_executed_inner_updates']
    budget = run['configured_cumulative_inner_updates']
    episodes = run['committed_episodes_supported_by_events_gradients_and_checkpoint']
    eta = run['same_speed_remaining_hours_for_this_run_only']
    due = dt.datetime.fromisoformat(checked['next_due_at_utc']).astimezone(
        dt.timezone(dt.timedelta(hours=8))).strftime('%H:%M:%S')
    note = f'''## 定时组织训练巡检（北京时间 {beijing}采样）

`{run['run']}`仍为RUNNING，已由events、raw梯度和checkpoint共同支持{episodes}个完整episode、{steps}/{budget}个已保存内层更新（{100*steps/budget:.2f}%）；当前未提交episode不计入进度。checkpoint SHA256为`{run['checkpoint']['sha256']}`，collector报告phi/Adam有限且配置一致。本次进度工具核对文本预算、exact-window及三路token，没有独立重算保存张量或真实模型导数。

归档`{observation}`的{checked['snapshot_source_files_rehashed']}份源文件已独立重核，原控制器和运行叶子的实际进程身份通过检查，没有新增未解释告警或待审阅完成meta。按已完成episode均速，从采样时刻估算本条余下约{eta:.2f}小时，不含后续成本变化、排队及其他实验，不是全协议ETA。审阅凭据为`{str(verification.relative_to(ROOT))}`与`{str(progress.relative_to(ROOT))}`。原观察器保持1800秒间隔，下一次北京时间{due}。

主clean两阶段配对应使用`records/organization_clean_pairing_20261110_1620/organization_queue_corrected_v2.py`；独立seed29/43使用`records/confirmation_clean_pairing_20261110_1430/`修正入口。两端CPU流程已验证，真实clean训练及控制器交接尚未发生。`records/controller_handoff_preparation_20261110_1630/`的6类真实隔离进程回归也已两端通过；实际启用仍须完成真实leaf审计/CPU Adam重放、固定receipt的collector与snapshot-review接入及observer交接。须在完成/等待科学审阅边界执行并逐条审阅，保持四组织、正式对照、选择性/回流/持续学习、7B和关键独立种子的完整范围。当前没有新增方法效果结论，method_validated=false，full_goal_complete=false。

'''
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    references = {str(p.relative_to(ROOT)): sha(p) for p in [verification, progress, Path(__file__)]}
    transport.sync(Path(__file__))
    code = '''import hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin);note=p['note'].encode();result={}
for name,expected in p['references'].items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
for name in ['REPORT.md','NEXT_STEPS.md']:
 path=r/name;current=path.read_bytes();before=current[len(note):] if current.startswith(note) else current
 assert note not in before
 after=note+before
 if current!=after:
  temp=path.with_suffix('.progress_note.tmp')
  with temp.open('xb') as f:f.write(after)
  temp.replace(path)
 assert path.read_bytes()==after
 result[name]={'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),'prior_bytes_preserved_as_suffix':True}
print(json.dumps(result))
'''
    remote = json.loads(transport.remote(code, {'note': note, 'references': references}))
    local = {}
    for name in ['REPORT.md', 'NEXT_STEPS.md']:
        path = ROOT / name
        current = path.read_bytes()
        before = current[len(note.encode()):] if current.startswith(note.encode()) else current
        assert note.encode() not in before
        path.write_bytes(note.encode() + before)
        local[name] = {'before_sha256': hashlib.sha256(before).hexdigest(), 'after_sha256': sha(path),
                       'prior_bytes_preserved_as_suffix': True}
    value = {'status': 'SCHEDULED_PROGRESS_RECORDED_BOTH_HOSTS',
             'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
             'observation': observation, 'evidence_sha256': references,
             'note': note, 'local_documents': local, 'remote_documents': remote,
             'training_or_controller_modified': False, 'method_validated': False, 'full_goal_complete': False}
    with output.open('x') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
    transport.sync(output)
    print(json.dumps({'status': value['status'], 'receipt': str(output.relative_to(ROOT)),
                      'receipt_sha256': sha(output), 'next_due_beijing': due}))


if __name__ == '__main__':
    main()
