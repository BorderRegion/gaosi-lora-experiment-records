# 环境就绪状态（进行中）

| 检查 | 状态 | 当前证据 |
|---|---|---|
| MUSA 初始化 | PASS | 实际识别 S4000，48 GiB |
| ordinary_lora_ready | PASS | 真实 3B/7B all7-r32 backward 和128步训练完成；可学性开发继续 |
| phi_H1_ready | PASS（计算路径） | 真实3B φ-L/X 含 moments/features 的两步短配置通过；不代表已学去噪 |
| phi_H2_ready | PASS（计算路径） | 真实3B φ-L/X、seq512/每路≥512有效token的精确两步完整状态反传通过；峰值28.7/29.4GB |
| 7B_ready | PARTIAL | 实际加载、LoRA backward、SGD H2及128步普通训练通过；正式φ更新未测 |

使用系统 Python，保留原 torch/MUSA 栈；原始输出收集在 records/ 与 checks/。
容器 RAM cgroup 100 GiB；数据盘 50 GiB；训练使用 BF16 基座和 FP32 adapter。
H2 使用逐微批精确 VJP/HVP 重计算和坐标头 checkpoint；不是一阶近似。
真实 profile 使用的父 adapter 尚未达到知识获取门槛，仅作为后端证据。
