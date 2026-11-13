# Gaosi-LoRA L1 — Codex执行包

**2026-10-28｜目标设备：AutoDL MTT S4000 48GB｜状态：待执行研究方案与已测试的小张量工具。**

主线：冻结LLM基座，在已经承载知识的LoRA适配器上施加可控高斯扰动；训练数据条件去噪器φ，研究恢复、新学习、选择性不恢复和持续学习。比较四种LoRA组织，而不是只扫rank或把普通LoRA微调称作Gaosi。

## 阅读顺序

1. `START_HERE.md` — 给Codex的完整入口，无需此前聊天。
2. `EXPERIMENT_PROTOCOL.md` — 四种组织、两种噪声、主矩阵、模型/数据/评价。
3. `NUMERICAL_CONTRACT.md` — A/B/φ生命周期、终点目标、真实高阶依赖。
4. `MUSA_SETUP.md` — 配套环境和真实设备检查。
5. `config.json`、`experiment_queue.csv` — 冻结前开发初值与有限队列。
6. `REFERENCES.md` — 既有研究/官方文档与本包设计分开。

`reference/`包含：原生LoRA层、有效低秩范数、QR坐标化、高斯扰动、φ结构、Adam型小张量转移、H1/H2测试。
`tools/`包含：静态内存计算、任务生成、CPU/MUSA短探针（可选真实本地Qwen）。

**没有附带完整生产LLM元训练runner、LoRA已训练权重或GPU效果。** Codex需要接入现有工程/实现缺失部分。命令名不能代替实现。

## 当前能直接运行

```bash
python reference/test_reference.py --out checks/cpu_reference_local.json
python tools/estimate_resources.py --rank 32
python tools/build_tasks.py --out data/synthetic
# 实际S4000机器上，torch_musa可用后：
python tools/probe_backend.py --device musa --out checks/musa_tiny.json
```

在本次容器：8组CPU数学检查通过；数据生成26,552条/116文件的结构检查通过；真实MUSA和Qwen均未运行。不要把这些数值检查宣传为方法已有效。

## 保留的研究要求

训练的是φ；内层工作变量为LoRA因子。q_train与q_apply独立，主目标评价完整恢复终点；中间损伤正常，但最终非目标残留必须计入。固定基座不等于输出能力不漂移，也不意味着能永久擦掉它原有的知识。以实际新学习—保留取舍和成本判断，不以单次涨分或不更新判成功。

## 执行边界

先用已有MUSA配套环境，不升级驱动、不自动购买算力。正常实现和已列实验连续推进。四种组织各有同日程Adam基线；7B提前验证，不以小模型获胜为门槛。H2失败明确记录并继续可用分支，不能伪造二阶成功。

最终交REPORT、配置、逐例输出、曲线、小φ/adapter文件、基座哈希/路径与成本账本。旧结果只读保留，新版单独run_id。
