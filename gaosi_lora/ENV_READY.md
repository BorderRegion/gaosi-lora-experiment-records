# 环境与运行状态（进行中）

更新于2026-10-30 08:09（UTC+8）。磁盘、进程与训练数值取自本次完整巡检。

| 项目 | 状态与证据范围 |
|---|---|
| MUSA | MTT S4000，48 GiB；沿用原配套torch/MUSA栈 |
| ordinary_lora_ready | 真实3B/7B all7-r32 backward和普通训练已通过；知识获取门槛见父状态记录 |
| phi_H1_ready | 真实3B L/X短配置计算路径已通过；不代表学习效果 |
| phi_H2_ready | v7完整3B L readiness连续两次PASS；正式L训练进行中，尚未完成效果验证 |
| 7B_ready | 实际加载、LoRA backward、SGD H2与普通训练已通过；正式phi更新仍待后续验证 |
| 当前正式训练 | phi_mix_O1_L_H2_core_s17_recompute_v7；08:09 PID344804/start ticks58376943与既有记录匹配 |
| 最近完整进度 | 08:09提交39/192 episodes、312/2560 updates（12.1875%）；phi/Adam有限，配置与冻结SHA相符 |
| 后续调度 | 15个recompute_v7调度器在08:09均为RUNNING且身份有效；依赖完整L结果与检查点门槛 |
| 数据盘 | /dev/md0挂载/root/autodl-tmp；08:09总1050 GiB、剩余983.48 GiB |
| 系统盘 | 08:09总30 GiB、剩余18.08 GiB |

容器RAM cgroup记录为100 GiB；基座BF16，适配器FP32。模型目录为
/root/autodl-tmp/models/Qwen2.5-3B；08:05确认config及两份safetensors文件存在。

H2采用逐微批精确VJP/HVP重计算和坐标头checkpoint。较早实现曾出现数值故障，
其失败运行和诊断保留；不能把较早算子probe通过当成当前训练有效性证明。
v7在episode7仍有未解释的裁剪前梯度范数2.1071e25；截至episode38未重复该尖峰，
有限checkpoint与持续推进不证明高阶梯度全部正确，也不证明phi有效。

08:09完成磁盘、GPU、训练事件与检查点核验，未见新增容器重启或日志错误。
下一次完整观测定于08:39，独立定时进程已核验存活，稳定训练期每30分钟。
完整实验目标保持进行中，L剩余时间暂保留约14–20小时粗估，完整协议仍需数日。

原始环境说明保存在records/env_ready_before_20261030_0807.md。
对应证据：records/container_expansion_check_20261030_0804.json、
records/training_monitor_20261030_0739/及records/pipeline_recompute_v7/。
最新训练证据：records/training_monitor_20261030_0809/result.json及review.json。
