# 独立种子 clean 两阶段配对修正（已准备，未启用）

原独立确认队列将 core 与 specialization 拼成单段 clean，导致后段 episode 索引、任务及支持/查询种子不能严格配对。主组织 seed17 的同类问题及实际日志证据见 `../clean_schedule_pairing_20261110_1407/`。

这里单独版本化队列、确认入口和 guard 接入。core 预算仍取冻结开发选择，clean 从零初始化；随后继承自身 core 的 phi 与完整 Adam 状态，从 episode0 开始64×8短适配。独立 seed29/43、任务顺序、元数据、主比较、选择性、回流和持续学习范围沿用原协议。最终 clean 名称及下游记录路径保持原定义，不额外启动旧的未配对 clean。

`validation_local.json` 使用标准库 JSON checkpoint stand-in；`validation_remote.json` 使用真实 PyTorch CPU 存取。两个预算2560/3584、两个独立种子及O2/O4第二组织共20个模拟meta入口，核完整非噪声配置及逐episode路由，44次非法配置被拒绝。真实 evidence guard 的 prepare/validate-only 路径也被调用，四份新增程序纳入其输入SHA固定；没有执行模型forward、训练或导数验证。

`publication_receipt.json` 确认已同步且原生产源码SHA保持不变。原在线确认控制器已导入旧队列；因此磁盘上放置新版不等于在线已修正。

启用之前必须完成有证据的控制器交接，并维护所有下游等待的原逻辑job身份/状态，保全旧job/log/进程证据。不能中断活跃训练。组织队列与独立确认队列均须在各自完成或等待边界交接；不得重启原失败诊断。

交接后的确认入口为 `python records/confirmation_clean_pairing_20261110_1430/confirmation_queue_corrected.py`。这不是现在提交训练的命令授权记录：本目录没有执行交接、启动控制器或提交训练。每个实际meta leaf仍需完整artifact审计、CPU逐update Adam重放和独立科学审阅，才能继续或冻结。

`method_validated=false`，`full_goal_complete=false`。当前等待O2，正常训练巡检保持1800秒。
