# 共同 warm H1 日志审阅准备

此工具在 H1 真正完成后核对保存的日志，不改动生产源码、队列或配置。它要求 L-O1-H1 从 L-H2 的 phi_0512 与 meta-Adam 状态继续，绝对 episode64 至191 新增128轮×16步=2048步，总预算2560。逐轮对照完整 L-H2 归档的 mode、任务、父状态、retain、剂量和噪声记录，并逐步对照三路 token 和 support forward 数。H1 采用终点 replay，原实现的 exact_window 应全为 false；该标记不能证明导数正确。

实际 H1 当前尚未执行。本目录验证仅将已归档 L-H2 的后128轮转换为 **模拟 H1 日志** 来测试工具；不把模拟数据当 H1 结果。缺完整终态、预算/种子/任务/剂量变化、token不足、缺梯度、H2标记、非有限值和未授权更新均须拒绝。

取得 H1 完整终态归档后执行：

```bash
python records/H1_completion_preparation_20261103_1204/review_commonwarm_ledger.py \
  --observation records/pipeline_monitor_<H1终态UTC>_utc \
  --reference records/observed_blocking_core_monitor_20261101_070654_utc \
  --require-complete --out records/H1_complete_inner_review_<新UTC>.json
```

工具重新校验两个归档清单所有文件哈希，完整模式还要求两个 job PASS、result完整及sidecar待科学审阅，并明确H1新增2048步。它不加载张量、不复算batch或导数、不验证warm的Adam字节、不生成放行receipt。后者仍需远端完整 artifact audit、保存梯度 CPU Adam 重放和独立科学审阅，检查通过后才允许原 core 队列继续。H1/H2 效果比较另见 `records/horizon_effect_comparison_20261103_1005/`。
