

### 2026-11-04 00:51（北京时间）：H1 由原控制器启动，首轮输入核验

既有半小时观察器的第105次采样确认共同warm H1已于00:39由原core启动（child PID972549/start ticks99803902）。00:51归档时episode64已记录15/16步，尚无新增episode提交；共同warm已完成512步，不能把当前未提交步计为已完成预算。原15控制器身份有效、原GPU锁inode31148027670未变，无告警。84项归档源文件及归档SHA已核验，运行审阅已同步：records/pipeline_review_20261103_1651.json。

归档before_episode_064.pt与已审定L-H2的phi_0512.pt整个文件字节相等，SHA256为4998e1ef0596fd40f797bb208ff1ebc7374b422916dc4d7cfc8981f577b83b65。此证据确认共同phi/Adam输入制品，尚不独立证明运行中optimizer状态的加载。首轮任务/父状态/剂量/噪声非计时元数据及已保存15步token/forward预算与L-H2对应轮一致。

发现旧离线日志准备工具把noise字典中的实测boundary_seconds也要求完全相等，实际1.326秒与1.658秒的正常耗时差异会导致误拒绝。原工具与历史证据保留；新工具位于 records/H1_ledger_review_20261103_1652/review_commonwarm_ledger.py，仅将该实测耗时排除相等比较，仍核字段集合、有限非负耗时、其余噪声字段逐项精确一致。19项模拟回归检查通过，含真实H1首轮noise元数据的计时差异；不把模拟后续轮当H1结果。新工具及启动审阅已同步核SHA。

H1至少提交一轮后用上述新工具做部分日志核对；完整终态加 --require-complete。仍须完整artifact audit、保存梯度CPU Adam重放、warm及绝对步数核验与独立科学审阅，方可发布H1放行凭据。H1/H2效果补充的check仍WAITING，未新启评测。继续原30分钟观察器，下次约01:21；未改运行数值源码、配置或控制器。全协议未完成，method_validated=false。
