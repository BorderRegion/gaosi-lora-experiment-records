"""Record the inactive handoff regression, retaining both report histories."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    helper = ROOT / 'records/scheduled_review_resilience_20261109_0925/complete_resilient_review.py'
    assert sha(helper) == '22e63dd58d555ae7cc2aa5a1f1b614546732c241f373518678b21b8f508fa24e'
    spec = importlib.util.spec_from_file_location('transport', helper)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    receipt = json.loads((HERE / 'publication_receipt.json').read_text())
    assert receipt['status'] == 'IDLE_HANDOFF_PREPARATION_PUBLISHED_ISOLATED_CPU_REGRESSION_PASSED_NOT_ACTIVATED'
    checks = {**receipt['source_sha256'], **receipt['production_sources_sha256']}
    for name in ['validation_remote.json', 'publication_receipt.json']:
        checks[str((HERE / name).relative_to(ROOT))] = sha(HERE / name)
    assert sha(HERE / 'validation_remote.json') == receipt['remote_validation_sha256']
    for name, expected in checks.items():
        assert sha(ROOT / name) == expected, name
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p.items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
print(json.dumps({name:base64.b64encode((r/name).read_bytes()).decode() for name in ['REPORT.md','NEXT_STEPS.md']}))
'''
    before = json.loads(transport.remote(code, checks))
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    note = f'''## 完成边界的控制器交接流程已隔离回归，尚未启用（北京时间 {stamp}）

`records/controller_handoff_preparation_20261110_1630/`已同步交接程序、进程身份适配器和原样生产job.py的真实Linux隔离进程回归。两端6种场景PASS：正常交接、alias提交前/后的中断恢复、替换队列真实FAIL传播，以及错误start ticks/已释放科学边界在任何信号前拒绝。4个执行场景均仅运行替换队列一次，下游原wait_for函数沿原逻辑job路径读到真实终态；每组还拒绝未固定receipt、错误原控制器身份及错误alias共3类输入。原job/log字节和生产源码SHA保留，没有模型forward或训练。远端验证SHA256=`670dd89e259286826de95e87cded8f3f054971f2bed4c38031cecf913ca8a88f`。

交接设计以单独命名的标准job.py记录新队列真实退出码，原依赖路径原子指向新job；原控制器仅在完成叶子且等待科学审阅时退役，原RUNNING及部分wall time另存，不伪造PASS/数值FAIL。程序要求真实完成leaf证据、未发布继续凭据、明确等待事件和精确PID/ticks/argv/cwd/亲子关系；替换队列先受activation gate约束。独立科学审阅和每条meta审计门槛不变。

实际生产plan、alias和activation均未创建，O2、原15控制器和原半小时observer没有被修改或重启。实际使用前仍须完成当前leaf的artifact审计/CPU Adam重放，准备固定receipt的新版collector与snapshot-review接入并在已停止的完成边界交接observer；identity_adapter模块已测试不等于已配置观察器。独立seed确认控制器等待另一类边界，须另行验证，不能虚构meta完成事件。

主clean候选继续使用`records/organization_clean_pairing_20261110_1620/organization_queue_corrected_v2.py`；独立seed29/43用`records/confirmation_clean_pairing_20261110_1430/`修正入口。当前没有新增效果结论或提前训练采样；沿用00:15的144/512已保存进度，下一次北京时间00:45。完整协议active，method_validated=false，full_goal_complete=false。

'''
    payload, after = {}, {}
    for name in ['REPORT.md', 'NEXT_STEPS.md']:
        path = ROOT / name
        local_old, remote_old = path.read_bytes(), base64.b64decode(before[name])
        for side, content in [('local', local_old), ('remote', remote_old)]:
            backup = HERE / 'documentation_before' / side / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open('xb') as handle:
                handle.write(content)
        path.write_bytes(note.encode() + local_old)
        content = note.encode() + remote_old
        payload[name] = {'before_sha256': hashlib.sha256(remote_old).hexdigest(),
                         'after_sha256': hashlib.sha256(content).hexdigest(),
                         'content': base64.b64encode(content).decode()}
        after[name] = {'local_sha256': sha(path), 'remote_sha256': payload[name]['after_sha256']}
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,value in p.items():
 path=r/name;current=hashlib.sha256(path.read_bytes()).hexdigest()
 if current==value['after_sha256']:continue
 assert current==value['before_sha256'],name
 content=base64.b64decode(value['content']);assert hashlib.sha256(content).hexdigest()==value['after_sha256']
 temporary=path.with_suffix('.idle_handoff_preparation.tmp')
 with temporary.open('xb') as f:f.write(content)
 temporary.replace(path)
print(json.dumps({name:hashlib.sha256((r/name).read_bytes()).hexdigest() for name in p}))
'''
    updated = json.loads(transport.remote(code, payload))
    assert all(updated[name] == after[name]['remote_sha256'] for name in after)
    transport.sync(Path(__file__))
    result = {'status': 'BOTH_REPORT_HISTORIES_PRESERVED_INACTIVE_HANDOFF_PREPARATION_RECORDED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'remote_references_rehashed': len(checks), 'documents': after,
              'production_handoff_activated': False, 'numerical_training_modified': False,
              'method_validated': False, 'full_goal_complete': False}
    output = HERE / 'documentation_verification.json'
    with output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    transport.sync(output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
