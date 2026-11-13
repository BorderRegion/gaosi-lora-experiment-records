# v8 后续执行说明（已准备、未启动）

当前 L 为 `phi_mix_O1_L_H2_core_s17_observed_blocking_v8`，完整原始日程 192 episodes / 2560 inner updates。保持现有观察器每 30 分钟检查；不得重复启动、恢复失败 v7 或覆盖运行中源码和配置。

本快照有 15 个隔离控制器、原始源码、转换 diff 和输入 SHA。覆盖 L/X、共同 512 步起点的 H1、开发选择与匹配延长、四组织与 matched clean、正式父状态/恢复/选择性/回流/持续、7B、统计和独立 phi seed29/43。条件追加实验仍需按实际结果决定，不能因队列结束而宣称全协议完成。

`validation.json` 的 19 项 CPU 流程测试、`launch_validation.json` 的 6 项预检测试，以及 `../guarded_continuation_20261030_1550/cpu_validation_final.json` 的 19 项 tiny-Qwen/失败fixture测试均 PASS。测试验证普通更新等同原trainer、异常在更新前停止和来源/预算衔接，不证明 MUSA 高阶导数正确。

L 完成后，由 agent 依次处理：

1. 确认原 job 与训练 sidecar 终态，完整 192/2560 日程与冻结配置一致。
2. 对保全的运行目录执行 `tools/audit_observed_meta.py --run runs/phi_mix_O1_L_H2_core_s17_observed_blocking_v8 --out <新的审计文件>`。只做 CPU 产物审计，不启动训练。
3. 独立复核完整 raw 梯度与 phi/Adam 连续性、异常尺度、H2 导数证据及其局限。审计 PASS、有限梯度、blocking 重复性均不能单独代替科学判断。旧 v7/诊断 phi 不具备提升资格。
4. 仅在证据支持继续时，agent 写 `reviews/phi_mix_O1_L_H2_core_s17_observed_blocking_v8.json`。receipt 必须包含状态 `ALLOW_NEXT_PROTOCOL_STAGE_WITH_LIMITATIONS`、准确 run 路径、reviewer/UTC、rationale、limitations、审计文件路径/SHA，以及非空的科学证据路径到 SHA 映射。不要预先创建或伪造 receipt。
5. 在项目根目录运行 `python tools/start_observed_v8_pipeline.py --validate-only`，复核通过后运行同脚本开始 15 个新控制器。launcher 拒绝现有下游结果、重复 job/launch、旧 RUNNING 控制器及变化的固定源码。保留启动记录，不自动重试部分启动。

后续每个 meta leaf 都走 `tools/train_guarded_meta.py`，保留 scale/confirmation 原校验，复用原 `/tmp/gaosi_musa_device0.lock`，进程内 blocking。完成后控制器每 30 分钟等待 agent 对该运行的独立审计与 review；不存在自动批准。普通恢复和评价仍执行原协议。

X 两 episode readiness 已仅用实际输入完成 prepare/validate-only，103 个文件固定，尚未训练。准备 manifest 在 `../guarded_continuation_20261030_1550/X_readiness_preparation_manifest.json`，不占用未来正式 meta_entries 路径。

默认 phi 预算 30720，现有匹配延长全部触发则 39936；失败、诊断、父状态、恢复/评价和条件追加费用另计。当前 `method_validated=false`，完整任务未完成。
