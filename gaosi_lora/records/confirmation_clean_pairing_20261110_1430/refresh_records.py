"""Preserve both documentation histories, publish factual preparation status."""
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
    checks = dict(receipt['published_source_sha256'])
    checks.update({str((HERE / name).relative_to(ROOT)): sha(HERE / name) for name in
                   ['source_publication.json', 'publication_receipt.json', 'validation_remote.json']})
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p.items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
print(json.dumps({name:base64.b64encode((r/name).read_bytes()).decode() for name in ['REPORT.md','NEXT_STEPS.md']}))
'''
    remote_before = json.loads(transport.remote(code, checks))
    note = '''## 独立种子clean配对修正准备（北京时间 2026-11-10 22:35）

后续独立seed29/43确认队列的单段clean日程已另行准备两阶段修正版：clean core从零训练，短适配继承自身phi和完整Adam状态，并重置episode至0。冻结预算、数据、架构、学习率和主比较范围保持不变；不是增加另一个未配对clean组。新版确认入口显式拒绝旧拼接日程、错误来源、缺失/错误Adam状态及未重置的索引，仍接入原artifact与科学审阅guard。原gaosi数值源码及旧工具SHA均保持原值。

本地标准库模拟与远端真实PyTorch CPU检查点读写回归均PASS：2560/3584两种预算、seed29/43及O2/O4第二组织共20个模拟meta入口；非噪声配置、逐episode任务/保留数据/样本流种子相等，44次非法配置被拒绝，prepare/validate-only确认新增程序纳入SHA固定。没有执行模型训练或forward，不能把这些流程检查当作导数、效果或独立种子结果。

程序与凭据见`records/confirmation_clean_pairing_20261110_1430/`，远端CPU验证SHA256为`c0fba64876643faec0cdd19cbce07f3ee82280290d3d60482137886d63caa641`。修正版已同步但尚未启用；原组织/确认控制器已导入旧代码，仍须在已完成或等待边界执行有记录的交接，保全旧证据并维护下游逻辑job身份。当前O2没有被中断或重启，下一次正式训练巡检仍为北京时间22:45。每条实际clean core和短适配仍须独立完成审计、CPU Adam重放及科学审阅。完整协议继续active，method_validated=false，full_goal_complete=false。

'''
    payload, after = {}, {}
    for name in ['REPORT.md', 'NEXT_STEPS.md']:
        local = ROOT / name
        for side, content in [('local', local.read_bytes()), ('remote', base64.b64decode(remote_before[name]))]:
            target = HERE / 'documentation_before' / side / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as handle:
                handle.write(content)
        local.write_bytes(note.encode() + local.read_bytes())
        remote_old = base64.b64decode(remote_before[name])
        remote_new = note.encode() + remote_old
        payload[name] = {'before_sha256': hashlib.sha256(remote_old).hexdigest(),
                         'after_sha256': hashlib.sha256(remote_new).hexdigest(),
                         'content': base64.b64encode(remote_new).decode()}
        after[name] = {'local_sha256': sha(local), 'remote_sha256': payload[name]['after_sha256']}
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,value in p.items():
 path=r/name;current=hashlib.sha256(path.read_bytes()).hexdigest()
 if current==value['after_sha256']:continue
 assert current==value['before_sha256'],name
 content=base64.b64decode(value['content']);assert hashlib.sha256(content).hexdigest()==value['after_sha256']
 temporary=path.with_suffix('.confirmation_pairing.tmp')
 with temporary.open('xb') as f:f.write(content)
 temporary.replace(path)
print(json.dumps({name:hashlib.sha256((r/name).read_bytes()).hexdigest() for name in p}))
'''
    updated = json.loads(transport.remote(code, payload))
    assert all(updated[name] == after[name]['remote_sha256'] for name in after)
    for path in [HERE / 'README.md', Path(__file__)]:
        transport.sync(path)
    result = {'status': 'BOTH_DOCUMENTATION_HISTORIES_PRESERVED_AND_PAIRING_PREPARATION_RECORDED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'remote_reference_files_rehashed': len(checks), 'documents': after,
              'training_or_controller_modified': False, 'method_validated': False, 'full_goal_complete': False}
    output = HERE / 'documentation_verification.json'
    with output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    transport.sync(output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
