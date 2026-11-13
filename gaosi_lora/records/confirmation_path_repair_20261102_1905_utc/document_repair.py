"""Prepend the verified repair note, preserving each host's own report history."""
import base64
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MARKER = 'confirmation_path_repair_20261102_1905_utc/alias_readback.json'
def sha(data):
    return hashlib.sha256(data).hexdigest()

sources = {
    'records/pipeline_review_20261102_1917.json': '418484f49de5bee470376f4b043b0397a63ffdb6912096bdf1bf8d866833df5c',
    'records/pipeline_observer_20261101_1220/progress_20261102_1917.json': '9381cc193764b7bb993e3180b10c44809f4a14dd260a7b05f69e91b7de903470',
    'records/confirmation_path_repair_20261102_1905_utc/alias_validation_remote.json': '8c220bea88bc138223d6d213e0362ff94f973d1bc6ab35743826e5e1f219c2b4',
    'records/confirmation_path_repair_20261102_1905_utc/alias_activation.json': '3468420f1a6867266ed3ae5ef95a40a6136c36ef7d1f365f188164a39a4cb96d',
    'records/confirmation_path_repair_20261102_1905_utc/alias_readback.json': '1e023f00ea8a081e99a8d452fef027b4982dbbce1018671d48be1016c716bcc5',
}
for name, expected in sources.items():
    assert sha((ROOT / name).read_bytes()) == expected, name
review = json.loads((ROOT / next(iter(sources))).read_text())
run = review['meta_runs_checked'][0]
assert run['committed_episodes_checked'] == 123
assert run['last_committed_inner_updates'] == 1456
assert not review['method_validated']
validation = json.loads((HERE / 'alias_validation_remote.json').read_text())
assert validation['status'] == 'ALL_FOUR_CONSUMERS_ALIAS_INTEGRATION_PASS'
readback = json.loads((HERE / 'alias_readback.json').read_text())
assert readback['status'] == 'ACTIVATION_READ_BACK_VERIFIED'
now = dt.datetime.now(dt.timezone.utc)
beijing = now.astimezone(dt.timezone(dt.timedelta(hours=8)))
note = f'''## {beijing:%Y-%m-%d %H:%M}（北京时间）：X 进度复核与确认种子统计路径修复

北京时间11月3日03:17的既有半小时采样及其独立复核确认，phi_mix_O1_X_H2_core_s17_observed_blocking_v8已提交123/192个episode、1456/2560次inner更新（56.875%）。检查点phi及Adam有限、配置一致，123行已提交raw梯度均有限，最大norm=0.5486493067867867。15个控制器在该采样中存活，无告警。证据：records/pipeline_review_20261102_1917.json与records/pipeline_observer_20261101_1220/progress_20261102_1917.json；这里只重述已审定采样，不声称新增训练采集。

确认种子控制器的manifest引用四个_v1就绪记录，而实际产出使用_observed_blocking_v8后缀。已在远端安装四个明确、相对路径的同seed兼容symlink，仅覆盖recovery/continual的seed29/43。全部路径在安装前均不存在；目标仍缺失时不代表完成，不搜索历史替代结果。原控制器已载入的源码无需重启；候选builder/controller仍隔离，生产源码未替换。

修复已通过实际未修改的recovery、selective、reflow、continual四类完整网格统计入口测试（模拟数据、100次bootstrap），数值结果与无别名基准相同，仅按实际输入改变input_sha256来源账。缺目标和路径冲突测试通过。这是元数据与统计集成验证，不是训练效果证据。激活前后确认控制器身份、producer/builder源码及原GPU lock inode均保持。验证/激活/回读证据：records/confirmation_path_repair_20261102_1905_utc/alias_validation_remote.json、records/confirmation_path_repair_20261102_1905_utc/alias_activation.json、{MARKER}。上述记录均已同步并核验SHA。

完整L已完成审计与科学复核；X仍未完成，须等完整192轮/2560步后再做产物审计、Adam重放及独立科学复核。method_validated=false；运行稳定与统计路径可用不能证明效果优于Adam。共同warm H1、开发选择/匹配延长、四组织与clean、正式恢复/选择性/回流/持续、7B、统计、独立phi seeds29/43及协议条件追加继续保留。继续既有1800秒观察器，不新建训练或监控。本次只补充报告；以下保留本端完整历史。

'''
data = note.encode()
target = ROOT / 'REPORT.md'
original = target.read_bytes()
assert sha(original) == '4dfe5172c939c108bbf76d743ab4c442a9c590984580962f0fceae7f453f43d2'
assert MARKER.encode() not in original
with (HERE / 'REPORT.before_documentation.local.md').open('xb') as handle:
    handle.write(original)
with (HERE / 'report_note.md').open('xb') as handle:
    handle.write(data)
payload = {'note': base64.b64encode(data).decode(), 'note_sha256': sha(data),
           'expected': '52efba4cd114bea738a8e9af1ed9d915a6fad6ac327a7ef5d24a8f1e00561b66',
           'record_dir': str(HERE.relative_to(ROOT)), 'marker': MARKER, 'sources': sources}
remote = '''from pathlib import Path
import base64,hashlib,json,os,sys
p=json.load(sys.stdin); root=Path('/root/autodl-tmp/gaosi_lora'); target=root/'REPORT.md'
def sha(x):return hashlib.sha256(x).hexdigest()
for name,expected in p['sources'].items():assert sha((root/name).read_bytes())==expected,name
old=target.read_bytes(); assert sha(old)==p['expected']; assert p['marker'].encode() not in old
note=base64.b64decode(p['note']); assert sha(note)==p['note_sha256']
backup=root/p['record_dir']/'REPORT.before_documentation.remote.md'
with backup.open('xb') as h:h.write(old)
tmp=root/p['record_dir']/'REPORT.with_documentation.remote.tmp'
with tmp.open('xb') as h:h.write(note+old)
assert sha(target.read_bytes())==p['expected']
os.replace(tmp,target)
actual=target.read_bytes(); assert actual==note+old
print(json.dumps({'before_sha256':sha(old),'after_sha256':sha(actual),'backup':str(backup.relative_to(root)),'history_preserved':True}))
'''
ssh = ['ssh', '-S', '/tmp/gaosi_autodl_12666_reboot.sock', '-p', '12666',
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', 'root@training.example.invalid']
reply = subprocess.run(ssh + ['/usr/bin/python -c ' + shlex.quote(remote)],
                       input=json.dumps(payload).encode(), capture_output=True, timeout=60)
with (HERE / 'report_remote_response.json').open('x') as handle:
    json.dump({'returncode':reply.returncode,'stdout':reply.stdout.decode(),'stderr':reply.stderr.decode()},handle,indent=2)
if reply.returncode:
    raise RuntimeError('Remote documentation requires read-back; inspect report_remote_response.json before further writes.')
remote_receipt = json.loads(reply.stdout)
assert target.read_bytes() == original
with (HERE / 'REPORT.with_documentation.local.tmp').open('xb') as handle:
    handle.write(data + original)
(HERE / 'REPORT.with_documentation.local.tmp').replace(target)
assert target.read_bytes() == data + original
record = {'status':'VERIFIED_REPAIR_AND_RUNTIME_EVIDENCE_DOCUMENTED',
          'recorded_at_utc':now.isoformat(), 'previous_goal_turn':'verified_wait_live_observer_and_waiter',
          'current_goal_turn':'progress_verified_metadata_repair_documented_on_both_hosts',
          'source_sha256':sha(Path(__file__).read_bytes()), 'note_sha256':sha(data),
          'evidence_sha256':sources,
          'local_report':{'before_sha256':sha(original),'after_sha256':sha(target.read_bytes()),'history_preserved':True},
          'remote_report':remote_receipt, 'training_modified_or_restarted':False,
          'extra_training_telemetry_polled':False,'method_validated':False,'full_goal_complete':False}
path = HERE / 'report_documentation_receipt.json'
with path.open('x') as handle:
    json.dump(record,handle,ensure_ascii=False,indent=2); handle.write('\n')
spec = importlib.util.spec_from_file_location('sync', ROOT/'records/observed_blocking_core_20261030_0810/review_snapshot.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
for p in [Path(__file__),HERE/'report_note.md',path]:
    print(json.dumps(module.sync_review(p),ensure_ascii=False))
print(json.dumps(record,ensure_ascii=False))
