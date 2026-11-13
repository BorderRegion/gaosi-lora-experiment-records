"""Record the published inactive v2, preserving both documentation histories."""
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
    publication = json.loads((HERE / 'publication_receipt.json').read_text())
    assert publication['status'] == 'PRIMARY_CLEAN_PAIRED_V2_PUBLISHED_AND_CPU_FIXTURES_PASSED_NOT_ACTIVATED'
    remote_validation = json.loads((HERE / 'validation_remote.json').read_text())
    assert remote_validation['status'] == 'PASS'
    checks = dict(publication['production_sources_sha256'])
    checks.update(remote_validation['source_sha256'])
    checks.update({str((HERE / name).relative_to(ROOT)): sha(HERE / name)
                   for name in ['publication_receipt.json', 'validation_remote.json']})
    assert sha(HERE / 'validation_remote.json') == publication['remote_validation_sha256']
    for name, expected in checks.items():
        assert sha(ROOT / name) == expected, name
    code = '''import base64,hashlib,json,sys
from pathlib import Path
r=Path('/root/autodl-tmp/gaosi_lora');p=json.load(sys.stdin)
for name,expected in p.items():assert hashlib.sha256((r/name).read_bytes()).hexdigest()==expected,name
print(json.dumps({name:base64.b64encode((r/name).read_bytes()).decode() for name in ['REPORT.md','NEXT_STEPS.md']}))
'''
    remote_before = json.loads(transport.remote(code, checks))
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    note = f'''## 主clean两阶段队列v2已验证，待边界交接（北京时间 {stamp}）

主组织队列后续交接应使用`records/organization_clean_pairing_20261110_1620/organization_queue_corrected_v2.py`，SHA256=`7e3b37b630891150dd6f939dc21dc52c5d4024e962d50c38151aa80cfa6e5724`。旧版修正候选保留；其错误假设meta result.json含config的问题已在隔离环境复现。v2改读真实config sidecar，核对完整配置、latest.pt路径、两阶段预算及clean自身core继承，再允许最终freeze。

本地及远端CPU流程回归均PASS，覆盖2560/3584 core预算、完成O1/O2后的结果复用，以及配置/检查点错配拒绝。远端验证SHA256=`f08e47a038f6b2a93b6a33d9e24ab731fe7dabe72829a9200850c774954e97a3`。这些是无模型forward、无训练的队列模拟，不能视为真实训练、导数或效果结论。所有生产数值源码、原组织队列及旧候选SHA均保持原值。

两阶段clean继续从零core，再继承自身phi/完整Adam并重置specialization episode至0，匹配mixed的非噪声任务和样本流；总预算不变，不新增未配对实验。v2已同步但未启用，须在完整叶子结束且等待科学审阅的边界保全旧证据、维护原逻辑job依赖并显式交接进程身份。当前O2与控制器未被中断或重启；每条真实clean阶段仍须完整artifact审计、CPU Adam重放及科学审阅。独立seed29/43另用`records/confirmation_clean_pairing_20261110_1430/`的已验证修正入口。

本次仅补充准备状态，没有进行提前训练巡检。沿用00:15采样144/512进度及原半小时observer，下一次北京时间00:45。完整协议仍active，method_validated=false，full_goal_complete=false。

'''
    payload, after = {}, {}
    for name in ['REPORT.md', 'NEXT_STEPS.md']:
        local = ROOT / name
        local_old = local.read_bytes()
        remote_old = base64.b64decode(remote_before[name])
        for side, content in [('local', local_old), ('remote', remote_old)]:
            target = HERE / 'documentation_before' / side / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as handle:
                handle.write(content)
        local.write_bytes(note.encode() + local_old)
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
 temporary=path.with_suffix('.primary_pairing_v2.tmp')
 with temporary.open('xb') as f:f.write(content)
 temporary.replace(path)
print(json.dumps({name:hashlib.sha256((r/name).read_bytes()).hexdigest() for name in p}))
'''
    updated = json.loads(transport.remote(code, payload))
    assert all(updated[name] == after[name]['remote_sha256'] for name in after)
    for path in [HERE / 'README.md', Path(__file__)]:
        transport.sync(path)
    result = {'status': 'BOTH_DOCUMENTATION_HISTORIES_PRESERVED_PRIMARY_V2_RECORDED',
              'recorded_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'remote_reference_files_rehashed': len(checks), 'documents': after,
              'training_or_controller_modified': False, 'method_validated': False,
              'full_goal_complete': False}
    output = HERE / 'documentation_verification.json'
    with output.open('x') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    transport.sync(output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
