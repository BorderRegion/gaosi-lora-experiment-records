# Gaosi-LoRA 实验记录

研究在冻结语言模型基座时，能否通过 LoRA 子空间高斯扰动与学习型更新器 φ，改善受损知识恢复、新知识学习和旧能力保留之间的取舍。主模型为 Qwen2.5-3B Base，主设备为 MTT S4000 48GB / MUSA；7B 为规模复核方向。

**阶段结论：出现了语言保持和个别任务的正面信号，但综合开发评分仍低于两类 Adam，目前尚未证明方法整体有效。**

实验结论截止 **2026-11-11**，公开整理于 **2026-11-13**。`method_validated=false`，`full_goal_complete=false`。本仓库同时保留失败诊断、负面结果和待完成项目。

## 先读这些

- [阶段实验报告](gaosi_lora/EXPERIMENT_REPORT_20261111.md)：研究设置、结果、局部正反例、审阅边界和后续工作。
- [原始实验协议](Gaosi_LoRA_MTT48_Codex_20261028/EXPERIMENT_PROTOCOL.md)与[数值约定](Gaosi_LoRA_MTT48_Codex_20261028/NUMERICAL_CONTRACT.md)。协议描述完整目标，不能当作已完成清单。
- [开发评分 CSV](results/development_scores.csv)、[60 条条件结果 CSV](results/development_conditions.csv)、[机器可读状态](results/status.json)。CSV 直接摘自已保存的汇总凭据。
- [开发汇总审计](gaosi_lora/records/development_aggregate_review_20261109_2058/assessment_verification.json)与[描述性对照](gaosi_lora/records/development_signal_review_20261110_0742/descriptive_comparison.json)。
- [原始运行产物](evidence/critical_snapshot/runs/)：配置、事件、结果、逐例预测及部分梯度账本；[合成数据](evidence/critical_snapshot/data/synthetic/)与[冻结源码](evidence/critical_snapshot/gaosi/)来自已核验的核心归档。

## 目前结果

预先冻结的开发评分越高越好，综合任务表现与旧知识/语言损失约束，**不是通用准确率**。

| 方法/快照 | 冻结开发评分 |
| --- | ---: |
| L2048 | 0.9960937500 |
| L2560 | 0.9957682292 |
| X2048 | 0.9960937500 |
| X2560 | 0.9964192708 |
| standard Adam | 0.9982096354 |
| paired Adam | 0.9972148363 |

按既定开发选择规则，后续架构选 X；正式 φ 尚未冻结。X2560 在 10 个匹配开发条件中的语言 NLL 均低于两类 Adam，但旧知识 NLL 仅 1/10 更低，另外 9/10 更高。十个条件来自同一个 φ 训练种子，不是十次独立重复。

零噪声倍增规则 f(x)=2x 的局部例子中，各方法均答对 4 个范围内 query，X2560 的旧知识 strict 为 502/512，standard Adam 为 495/512，paired Adam 为 489/512。对应 medium 扰动格中 paired Adam 为 511/512，四个 φ 快照仅 478–494/512。零噪声例子不能证明损伤恢复收益；目前也没有预算匹配的 φ_clean 结果来确认训练噪声的额外价值。

## 完成范围

O1 的 L-H2、X-H2 核心均完成 192 episodes / 2560 inner updates，并完成产物审计与保存梯度的 CPU Adam 重放。共同 warm L-H1、60/60 开发网格、10/10 H1 补充、O4 父状态构建及 O1-X 组织短适配已有审阅记录。

O2-X 组织短适配已达到 64 episodes / 512 updates，数值终态 PASS；完整科学审计和逐 update CPU Adam 重放尚未完成。后续 O3/O4、修正后的配对 φ_clean、正式恢复/新学习、D+/D−、回流、持续学习、独立种子 29/43、学习型 φ 的 7B 复核及真实文本验证仍有待完成。7B 前期普通 LoRA 探针的 PASS 不代表 φ 已通过规模验证。

## 目录与核验

| 目录 | 内容 |
| --- | --- |
| `Gaosi_LoRA_MTT48_Codex_20261028/` | 原协议、配置、参考实现及任务生成器 |
| `gaosi_lora/gaosi/`、`gaosi_lora/tools/` | 本地保存的实现、训练与审计工具 |
| `gaosi_lora/records/` | 阶段审阅、数值诊断、失败记录、修正准备和保存凭据 |
| `evidence/critical_snapshot/` | 核心归档中的原始文本产物与当时源码 |
| `results/` | 易读的结果摘录与状态 |
| `publication/` | 导出范围、源/公开 SHA256 对照与核验工具 |

```bash
python3 publication/verify_archive.py
```

此命令使用 Python 标准库，核对公开文件哈希、结果摘录、75456 行已保存预测及完成状态，不执行训练。

[历史 REPORT](gaosi_lora/REPORT.md)和[NEXT_STEPS](gaosi_lora/NEXT_STEPS.md)保留各时点的记录；旧的运行百分比、传输计划及未完成标记须结合日期阅读，当前结论以阶段报告为入口。

公开版移除了服务器连接主机名，并保留源文件与公开副本的 SHA256 对照。权重、检查点和备份压缩包另行保存；语言语料正文需按原 manifest 和获取脚本准备。复现实验还需要对应 MUSA 环境、模型及父适配器状态，不能仅凭本仓库直接恢复全部训练。详见[导出说明](publication/EXPORT_NOTES.md)。
