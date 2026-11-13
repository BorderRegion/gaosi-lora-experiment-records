# 来源与继承边界

核对日期：2026-10-28。以下来源用于方法/硬件/软件定义，不是本项目效果证据；没有重做全面新颖性检索。所有配置、剂量、组织日程、元训练预算均为本包设计选择。

## 用户既有协议

**[E1]** `Gaosi_Phi_Loss_Alignment_20261026/ADDENDUM_FOR_CODEX.md`：训练副本与部署模型分开；q_train/q_apply分开；初始损伤不单独罚给φ；终点评价与梯度时域分开。此次通过文件工具读取，并保留这些约束。

**[E2]** `Gaosi_A80080_Codex_Full_Plan_20261026.md`：继承两路反馈、共享二阶矩、局部/坐标φ、H1/H2语义；此版明确将工作参数改成LoRA因子，不沿用全部参数更新的强制要求。

**[E3]** `gaosi_mtt_env/CODEX_MTT_48GB_ENV_HANDOFF_20261021.md`：继承设备核查、匹配torch_musa、真实高阶探针，不照搬旧“禁止LoRA/仅环境准备停止”。

历史0.5B实验中，旧φ尚未稳定超过调优Adam。手工常数策略的小开发优势不是本轮LoRA正结果。本次没有重新审计所有历史工程，没有连接用户GPU。

## 外部原始来源

**[R1] Hu et al. LoRA: Low-Rank Adaptation of Large Language Models.**
https://arxiv.org/abs/2106.09685
冻结基座并训练低秩增量；不是本项目首次提出LoRA或参数高效更新。

**[R2] Hugging Face PEFT 官方 LoRA 文档。**
https://huggingface.co/docs/peft/main/package_reference/lora
https://huggingface.co/docs/peft/main/en/conceptual_guides/lora
支持明确模块、rank、scale、常见初始化、merge、rsLoRA等；默认A随机/B零，`init_lora_weights="gaussian"`不等于我们所说的“对已学知识进行破坏”。文档main可能先于实例安装版本，现场锁版本。

**[R3] Lialin et al. ReLoRA: High-Rank Training Through Low-Rank Updates. ICLR 2024.**
https://proceedings.iclr.cc/paper_files/paper/2024/hash/d6fdc308a3b0f017f16059e5888f3346-Abstract-Conference.html
https://arxiv.org/abs/2307.05695
合并与重启的直接先例。O4是受控简化版组织对照，不冒充其完整方法、训练或成绩。

**[R4] A Rank Stabilization Scaling Factor for Fine-Tuning with LoRA.**
https://arxiv.org/abs/2312.03732
rsLoRA采用不同rank缩放；比较需记录真正的alpha/scale及调参预算。

**[R5] Hayou et al. LoRA+: Efficient Low Rank Adaptation of Large Models.**
https://arxiv.org/abs/2402.12354
A/B不同学习率已有直接先例；φ优于欠调的统一学习率不能独自构成新的更新机制证据。

**[R6] Qwen2.5-3B 官方模型卡与配置。**
https://huggingface.co/Qwen/Qwen2.5-3B
https://huggingface.co/Qwen/Qwen2.5-3B/blob/main/config.json
参数约3.09B，hidden2048、intermediate11008、36层、16Q/2KV heads。静态LoRA参数数根据这些形状计算；不称该模型最新最强，使用其许可条件。

**[R7] Qwen2.5-7B 官方模型卡与配置。**
https://huggingface.co/Qwen/Qwen2.5-7B
https://huggingface.co/Qwen/Qwen2.5-7B/blob/main/config.json
参数约7.61B，hidden3584、intermediate18944、28层、28Q/4KV heads。内存表不包含运行时峰值。

**[R8] PyTorch autograd / functional_call 官方文档。**
https://docs.pytorch.org/docs/stable/generated/torch.autograd.grad.html
https://docs.pytorch.org/docs/stable/generated/torch.func.functional_call.html
`create_graph`用于构造可微导数图。冻结参数不会自动保证任意高阶后端兼容，也不是取消激活梯度。

**[R9] 摩尔线程 S4000 官方产品信息与MUSA使用文档。**
https://www.mthreads.com/product/S4000
https://docs.mthreads.com/s4000/s4000-doc-online/introduction/
https://docs.mthreads.com/torchmusa/torchmusa-doc-online/start/
S4000 48GB，公开资料列有FP16/BF16等精度；真实算子、dtype及高级梯度支持在实例验证，不照抄营销中的全面兼容结论。

**[R10] AutoDL与摩尔线程训练框架官方说明。**
https://www.autodl.com/docs/mt_gpu/
https://docs.mthreads.com/ms-swift/introduction/
https://swift.readthedocs.io/en/v4.6/BestPractices/MUSA-support.html
优先配套镜像；官方文档覆盖LoRA/Qwen。swift文档中的S5000示例不证明S4000的速度或Gaosi二阶兼容，依赖版本不得机械照搬。

**[R11] Larson et al. An Evaluation Dataset for Intent Classification and Out-of-Scope Prediction.**
https://aclanthology.org/D19-1131/
CLINC150是候选真实文本外推，不用合成任务结果替代真实外推，也不把自选子协议称原任务完整成绩。

## 不是新增实验结果

`checks/cpu_reference.json`：本次在本地CPU运行小张量数学检查。
`checks/cpu_backend_probe.json`：仅CPU探针，无MUSA/真实Qwen。
`checks/data_check.json`：生成器的ID和结构检查，不证明数据可学。
`checks/static_memory.json`：字节计算，不是实测峰值或卡时。

本包没有论文SOTA声明，没有保证选择性抹除冻结基座知识，也没有承诺MUSA上精确H2一定可用。
