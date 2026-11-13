# X 完整 core 日志复核准备

此目录是独立的本地归档检查工具及验证记录，不改变生产源码、控制器或训练配置。

`review_core_inner_ledger.py` 从保全的定时归档读取 L/X core，重新核验归档清单中的全部文件哈希，检查已提交 episode/梯度/inner 步序、最后两步 exact 标记、三路有效 token、有限目标、更新计数和已记录 guard。`--require-complete` 另外要求完整 192 轮/2560 步以及待科学审阅的终态文件。它不加载张量，不验证实际计算图或高阶导数，不生成放行 receipt。

完整 L 归档的 192 轮/2560 步结果与原独立日志审阅的分组、有效 token 和预算逐项一致。X 的 16:49 归档只覆盖已提交 167 轮/2160 步，明确标为未完成；要求完成时会拒绝。缺失 exact 标记、路线 token 不足、梯度账本缺行和源文件哈希改变的本地修改样例均拒绝。验证结果见 `validation.json`。

确认 X job 和 sidecar 真实终态后，先在远端执行原产物审计和保存梯度的 CPU Adam 重放，再对对应完整定时归档执行：

```bash
python records/X_core_completion_preparation_20261103_0852/review_core_inner_ledger.py \
  --observation records/pipeline_monitor_<终态采样时间>_utc \
  --architecture X --require-complete \
  --out records/X_core_complete_inner_review_<新UTC>.json
```

审阅结果仍须与终态身份、完整张量审计、Adam 重放和数学/真实模型的导数证据共同判断。仅日志通过不能证明方法有效、数值后端正确或全协议完成。既有后续范围不变：共同 warm H1、开发比较与匹配延长、组织适配与 clean、正式恢复/选择性/回流/持续、7B、统计、独立 phi seeds29/43及协议条件追加。
