# H1 共同 warm 日志审阅修订

00:51 的既有定时归档确认 H1 已由原控制器启动。首个 episode64 的任务、剂量、噪声种子和所有损伤数值与 L-H2 对应轮精确一致；QR 边界耗时分别约 1.326 秒和 1.658 秒。

旧准备工具把整个 noise 字典比较，因而会错误拒绝正常的耗时差异。此修订只将实测 `boundary_seconds` 排除出配对相等条件，仍要求两侧字段集合相同、耗时有限非负，其余每个噪声字段完全相等。原准备目录及验证证据保留。运行中的数值源码、训练配置及控制器均未修改。

验证使用归档 L-H2 转换的模拟 H1 日志，并引入真实 H1 首轮的 noise 元数据，检查允许实测耗时差异、拒绝噪声种子/损伤变化、缺字段和异常耗时。它不把模拟后续轮当真实 H1 结果。

H1 至少提交一轮后可进行部分日志检查；完整终态时执行：

```bash
python records/H1_ledger_review_20261103_1652/review_commonwarm_ledger.py \
  --observation records/pipeline_monitor_<H1终态UTC>_utc \
  --reference records/observed_blocking_core_monitor_20261101_070654_utc \
  --require-complete --out records/H1_complete_inner_review_<新UTC>.json
```

仍须执行完整 artifact audit、保存梯度 CPU Adam 重放、共同 warm phi/Adam 张量与绝对步数核验及独立科学审阅。工具不生成放行凭据，`method_validated=false`。
