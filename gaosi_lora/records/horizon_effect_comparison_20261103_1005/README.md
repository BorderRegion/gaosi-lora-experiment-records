# 共同 warm 的 L-H1/L-H2 效果对照

现有 core 队列会训练 L-H1 共同 warm 分支，但现有 phi_development 队列只评测 L/X-H2；horizon 队列是固定状态导数诊断，不能替代训练后效果比较。本目录补充独立开发集对照，不改运行中的控制器、数值源码、训练配置或结构选择规则，不创建科学放行记录。

先固定 policy 和输入，再等待 H1 与 H2 各自完整审计及科学审阅。比较两个 L 端点的累计 2560 步：共享 H2 的 512 步 phi 和 Adam 状态，随后各 2048 步；H2 即使延长到 3584 也只复用其原 2560 快照。每个端点使用相同的 5 项开发任务、clean/medium 两剂量、K64、8/16/32/64 步评测点。

H2 复用原来 10 条 L_2560 开发评测。H1 使用独立 plan 和 10 个新 run，显式复用对应 H2 的 initial.pt；原 recovery 实现会重新生成噪声并逐张量核对。输出另核对 source、完整配置、父状态/数据/phi 哈希、每步 token 数、最终数据流状态和初始/clean 指标。任何不一致都停止检查，不静默放宽配对要求。

远端工程根目录下执行：

```bash
/usr/bin/python records/horizon_effect_comparison_20261103_1005/compare.py prepare
/usr/bin/python records/horizon_effect_comparison_20261103_1005/compare.py check
# check 达到 READY_NOT_STARTED 后才执行；不在缺失端点时新建训练任务。
/usr/bin/python records/horizon_effect_comparison_20261103_1005/compare.py run
```

prepare 不读取效果结果，只固定静态元数据与输入哈希。check 缺条件时返回 WAITING；run 同样先检查条件。run 使用既有 guarded_pipeline、MUSA_LAUNCH_BLOCKING=1 和原 GPU 锁，不重试未完成作业。可以在原有半小时巡检时调用 check，无需新增轮询器或启动另一套原流水线。只有原控制器继续运行；本补充当前仅准备、验证和归档。

输出保留逐步指标、逐任务/剂量配对差、三任务组等权的 H2−H1 分数和成本。训练成本单列共享 512 步及各自新增 2048 步。该对照仍只有训练 seed17 与小开发样本，不产生确认性统计结论或方法有效性声明。H1 完成后还需正常的 Adam 保存梯度重放和独立审阅，脚本不代替这些工作。

validate.py 检查预算、配置、源码、phi 和缺条件拒绝；validate_tensor_pairing.py 在远端 CPU 上用微型张量检查初始噪声、stream cursor 和逐步 token 配对拒绝。它们不验证导数正确性或实际方法效果。
