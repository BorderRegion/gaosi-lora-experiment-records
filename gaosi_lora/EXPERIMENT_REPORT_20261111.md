# Gaosi-LoRA 阶段实验报告（公开记录版）

实验结论截止：2026-11-11；公开整理：2026-11-13。备份状态按 11 月 11 日 19:33 的最终凭据统一，实验结论未因公开整理而更新。

**当前结论：方法能执行，出现了语言保持和个别任务的正面信号，但尚未证明整体优于 Adam。关键记录、小检查点及父状态/O4/关键诊断状态已保全；历史大检查点按用户选择部分保留。**

`method_validated=false`；`full_goal_complete=false`。

## 1. 研究目标与方法

目标是在学习新知识、恢复受扰知识时，改善新任务学习与旧能力保留的取舍。主模型为 Qwen2.5-3B Base，设备为 AutoDL MTT S4000 48GB / MUSA；7B 用于规模复核。

先用普通 LoRA 学出承载知识的父适配器，再在其有效低秩增量中施加受控高斯扰动。跨 episode 训练更新网络 φ；正式适应时冻结 φ，由 support、retain 等数据的梯度条件驱动适配器恢复或更新。O1/O2/O3 的预训练基座固定，O4 每轮结束后把适配器增量累积合入 FP32 母本。

默认 rank32、alpha32、覆盖 q/k/v/o 与 gate/up/down 七类矩阵；比较 φ-L 与 φ-X。主噪声在 QR 规范化后的低秩坐标中定义，属于适配子空间扰动。φ 不读取原始噪声或干净参数答案。

完整实验仍包含四种组织及各自匹配的 Adam 对照：

| 组织 | 实验设置 |
| --- | --- |
| O1 | 共享单 LoRA，A/B 均可更新 |
| O2 | 固定输入子空间 A，仅更新 B |
| O3 | 总 rank32，stable16 + plastic16，按阶段更新 |
| O4 | 本轮适配器恢复后合并，下一轮新建 LoRA |

H1/H2、预算匹配的 φ_clean、正式恢复、新学习、D+/D− 选择性不恢复、回流、持续学习、7B、独立种子及真实文本验证均保留在原协议范围内。原协议见 [EXPERIMENT_PROTOCOL.md](../Gaosi_LoRA_MTT48_Codex_20261028/EXPERIMENT_PROTOCOL.md)。

## 2. 已完成的工作与审阅边界

| 阶段 | 已完成内容 | 证据边界 |
| --- | --- | --- |
| O1 L-H2 核心 | 192 episodes / 2560 inner updates | 产物审计、保存梯度的逐 update CPU Adam 重放与科学审阅完成 |
| O1 X-H2 核心 | 192 episodes / 2560 inner updates | 同上 |
| 共同 warm L-H1 | 继承 H2 的 512 步 φ 与完整 meta-Adam，再训练 2048 步，累计 2560 步 | 已完成独立审阅；并非独立从零训练 |
| 冻结开发网格 | 60/60 条运行：40 φ、10 standard Adam、10 paired Adam；另有 H1 补充 10/10 | 汇总重核 4488 份非张量文件、75456 行保存预测 |
| O4 父状态 | 两套历史/当前父状态构建并通过原学习门槛 | 保存产物已审阅；不代表 φ 产生收益，关键大状态已备份 |
| O1-X 组织短适配 | 64 episodes / 512 updates | 产物、CPU Adam 重放、组织状态及科学审阅完成 |
| O2-X 组织短适配 | 64 episodes / 512 updates，终态 result=PASS | 终态、配置、事件、梯度账本及 φ+Adam 检查点已恢复并核 SHA；完整科学审计和逐 update CPU Adam 重放仍待完成 |
| 7B 前期探针 | 普通 LoRA 学习及 clean/light/medium 恢复已有 PASS 记录 | 尚未完成学习型 φ 的规模有效性验证 |

O2 的 512 步终态证据更新了旧文档中的 192/512、216/512 历史观察。旧数值仍保留用于追溯，不能再当作本报告的最新完成度。训练程序的 PASS、文件完整性与方法有效性分别判断。

## 3. 当前实验结论

### 3.1 综合表现尚未胜过 Adam

开发集使用预先冻结的评分：known/fact/rule 等权、噪声档位等权，综合 candidate 与 strict 答题表现，并对超过阈值的旧知识/语言 NLL 残差扣分。此评分越高越好；以下数值不是通用准确率。

| 方法/快照 | 冻结开发评分 |
| --- | ---: |
| L2048 | 0.9960937500 |
| L2560 | 0.9957682292 |
| X2048 | 0.9960937500 |
| X2560 | 0.9964192708 |
| standard Adam | 0.9982096354 |
| paired Adam | 0.9972148363 |

按既定规则选择 X 作为后续架构，但尚未冻结正式 φ。X2560 分数低于 standard Adam 约 0.00179036，低于 paired Adam 约 0.00079557。2048→2560 的改进未满足严格大于 0.01 的延长门槛，因此没有延长到 3584。开发高分接近饱和，不能据此宣称方法验证成功。

### 3.2 正面信号与负面结果同时存在

- **语言保持信号较一致：**X2560 在 10 个匹配开发条件下，语言 NLL 均低于两类 Adam。
- **旧知识损失总体仍偏 Adam：**X2560 的旧知识 NLL 仅在 1/10 条件下低于两类 Adam，另外 9/10 更高。不能把较低语言损失概括为全面保留能力更强。
- **局部正面例子：**零噪声倍增规则 f(x)=2x、K64、seed17 中，各方法均答对 4 个范围内规则 query；X2560 的旧知识 candidate/strict 为 512/502，standard Adam 为 507/495，paired Adam 为 505/489；语言 NLL 分别为 2.11891620、2.13143104、2.13245772。这支持该条件下较好的学习—保持取舍，但零噪声格不能证明受损恢复或训练噪声的额外价值。
- **对应反例：**同规则的 medium 扰动格中，paired Adam 的旧知识 strict 为 511/512，四个 φ 快照为 478–494/512；φ 的语言 NLL 仍较低。局部收益不能外推为整体优势。

上述结果来自一个 φ 训练种子的开发比较。规则 query 仅有 4 个且在 support 数值范围内，尚不足以证明一般规则推理、外推、统计显著性或跨模型优势。预算匹配 φ_clean 尚未完成，因此也不能把差异归因于训练时加噪本身。

### 3.3 数值诊断的结论有限

统一数值路径的固定 known/K4 例子中，H1/H2/H4 的 5548 个终态张量比较最大绝对差为 0。对保存的完整 H4 梯度，H1 的相对 L2 误差为 0.522044、余弦为 0.979183；H2 分别为 0.611468、0.954427。该例中 H2 并未更接近完整梯度，不能假设更长截断必然更好。

这些是保存证据比较，未独立重算真实模型导数，亦不能用于排序训练效果。原 mixed-path 失败证据保留。有限的十二条诊断结果仍待最终综合审计，不能写成全部通过，也不应据此扩大调参网格或改变原冻结主线。

## 4. 尚未完成的关键工作

1. 完成 O2 终态的完整产物审计、逐 update CPU Adam 重放与科学审阅，再决定后续实验放行。
2. 在真实完成/等待边界完成控制器交接，继续 O3/O4 组织适配及原定下游；不得因 SSH 中断重复提交训练。
3. 启用已准备的两阶段 φ_clean 配对修正。旧单段 256-episode 日程的尾段索引为 192–255，而 mix 短适配为 0–63，造成 32 个新增任务及全部 64 个 support/query 流种子不匹配。修正版采用 core2560 + 独立 specialization512，并继承各自 φ 与完整 Adam；CPU 流程已检查，真实训练尚未启用。
4. 完成各组织匹配对照、冻结正式 φ，继续正式恢复/新学习、选择性 D+/D−、回流与持续学习。
5. 完成关键独立 φ 种子 29/43、7B、统计与真实文本验证，并汇总全部有效负结果。

这些不是当前任一训练任务的剩余百分比；全协议尚无可靠总 ETA。恢复后优先取得可解释的匹配效果证据，不以增加诊断或选择性报告正结果替代原目标。

## 5. 保存状态与公开范围

截至北京时间 2026-11-11 19:33 的最终核验，核心记录/小检查点 **334966/334966**、父状态/O4/关键诊断大状态 **23/23** 的文件依赖完整。历史大状态按用户要求停止全量传输，清理后仅 2/188 文件可恢复；`all_source_files_available=false`。传输服务与周期观察已结束。

本地与备份均保留经 SHA256 核验的 2803 文件核心归档和 O2 完成状态。公开仓库包含报告、代码、配置、合成数据、运行结果、逐例预测、梯度账本与审计凭据；权重、检查点、压缩备份和第三方语言语料正文仍在原保存体系内，未纳入本次 Git 导出。公开副本不等于可直接恢复训练的全量备份。

关键源文件 SHA256：

```text
critical.normalized.tar.gz
4892f1713edc4abf638f3f7a70f3ab088c96ead8f9e96f7c7272103a3cd4e8cc

O2 latest.pt
dc88c3dfd03de88d4a824d57db7350f15b58aead8ad4a9247333024d845c291f
```

最终凭据：[selected_backup_final_state_20261111.json](records/expiry_rescue_20261111/selected_backup_final_state_20261111.json)。公开导出中的主机名已替换为 `.example.invalid` 占位符；原始 SHA 与公开副本 SHA 的对应见 [provenance.json](../publication/provenance.json)。

## 6. 主要证据索引

- 完整历史：[REPORT.md](REPORT.md)、[NEXT_STEPS.md](NEXT_STEPS.md)。其中较早的进度段落按其采样日期理解。
- 核心训练：[L 审阅](records/observed_blocking_core_completion_20261101_1143/scientific_assessment.json)、[X 终态目录](records/X_core_completion_20261103_1622/)。
- 开发汇总：[assessment_verification.json](records/development_aggregate_review_20261109_2058/assessment_verification.json)。
- 局部规则对照：[零噪声](records/development_completion_20261104_2358/phi_dev_paired_adam_t4_clean_s17_matched_endpoint_assessment.json)、[medium 扰动](records/development_completion_20261104_2358/phi_dev_paired_adam_t4_medium_s17_matched_endpoint_assessment.json)。
- O1 短适配：[scientific_assessment.json](records/O1_specialization_completion_20261110_1305/scientific_assessment.json)。
- O2 终态：[result.json](records/expiry_rescue_20261111/O2_completed_recovered/result.json)、[恢复核验](records/expiry_rescue_20261111/O2_recovered_verification.json)。
- 截断诊断：[known_k4_scientific_assessment.json](records/horizon_uniform_numerics_20261109_2330/known_k4_scientific_assessment.json)。
- clean 配对修正：[组织入口](records/organization_clean_pairing_20261110_1620/organization_queue_corrected_v2.py)、[独立种子准备目录](records/confirmation_clean_pairing_20261110_1430/)。
- 备份完整性：[优先层核验](records/expiry_rescue_20261111/priority0_completion_destination_verification.json)、[核心归档核验](records/expiry_rescue_20261111/critical_destination_verification.json)、[O2 实际恢复](records/expiry_rescue_20261111/actual_O2_CAS_restore_verification.json)、[环境核验](records/expiry_rescue_20261111/environment_destination_verification.json)。这些凭据各有时点；旧凭据中的未完成标志不能覆盖较新的完成核验。

本报告提供阶段结论，不替代原协议、运行产物或科学审阅凭据。原始结果及逐例预测的公开副本见 [critical_snapshot](../evidence/critical_snapshot/)。

