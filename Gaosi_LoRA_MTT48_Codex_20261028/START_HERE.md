# 直接交给 Codex｜Gaosi-LoRA / MTT S4000 48GB

你不需要此前对话。接手的是**新的LoRA版研究实验**，不是普通SFT任务，也不是ES候选搜索。

## 1. 目标

冻结预训练LLM，先让LoRA适配器真正学会一些知识；在其工作副本上施加受控高斯扰动，训练独立去噪器φ，让它根据任务/保留语料的梯度与当前适配器状态产生更新。冻结φ后，验证新学习、选择性恢复、持续学习。真正跨元episode被训练的是φ；适配器是episode内的工作状态。

用户提供AutoDL MTT S4000 48GB，有两个月免费资源。3B Base主实验，7B Base早期/规模验证；不是再用0.5B结论裁决整个方向。此次明确授权LoRA：旧文件的“不得改LoRA”不适用。全参旧实验保留，但不复跑旧大队列。

## 2. 先读这些

1. `EXPERIMENT_PROTOCOL.md`：算法与实验安排、四种LoRA组织、噪声、数据与判断。
2. `NUMERICAL_CONTRACT.md`：实现时逐项遵守，尤其噪声坐标、终点与H2。
3. `MUSA_SETUP.md`：优先现有配套栈；不要更新驱动/覆盖torch。
4. `config.json`与`experiment_queue.csv`：建议初值、有限队列。它们不是现成训练器。

有旧工程就复用数据、loss、日志与已验证梯度；旧checkpoint不兼容新feature时不要强行加载。没有旧工程也能按本包实现。只需简单CLI、JSONL、checkpoint，不搭Web/数据库/新代理框架。

## 3. 包里能直接运行的内容

```bash
python reference/test_reference.py --out checks/cpu_reference_local.json
python tools/estimate_resources.py --rank 32
python tools/build_tasks.py --out data/synthetic
python tools/probe_backend.py --device musa --out checks/musa_tiny.json
# 模型下载完成后：
python tools/probe_backend.py --device musa --model /实际模型目录/Qwen2.5-3B --rank 32 --real-h2
```

参考是CPU小张量，probe只是底层/短真实模型兼容检查，不是科学效果。生产模型注入、48维feature、Gaosi H1/H2转移、数据与评价、组织循环需要你接入实现，不能停在“测试通过”。

## 4. 实现和执行重点

- 同一份transition支持普通恢复、phi训练、phi冻结使用；冻结W0但不切断穿过基座的激活梯度。
- 噪声主加在**已经承载知识的适配器**，不是空的B=0，更不是先破坏全部冻结基座却只允许小rank修复。
- 比较O1共享LoRA、O2固定A、O3 16+16双分支、O4合并后重启；各自有同组织Adam基线。
- `q_train`和`q_apply`分开；主外层loss只在完整K步终点。初始损伤仅诊断，终点残留必须报告。
- phi-L和phi-X都实际训练；H2通过真实算子探针才用。H2失败留最小复现并继续H1，不把两次H1/FO写成精确H2。
- D−含真实非目标知识，不含目标绑定或假答案；不能把关闭adapter的全盘退回当选择性遗忘。
- 先一个种子完成关键链，再补重要重复；不要因早期负结果取消另一关键组织/7B，也不要无限调到胜出。

## 5. 执行权限与交付

正常环境适配、实现、已列短诊断、正式core，在用户既有资源/额度内自主连续推进，不逐函数确认。若主机尚未有用户上传的模型/工程，只能如实下载合法公开依赖或报告缺失，不能假装已读过。

不自动租卡、调用付费API、改宿主驱动、重启/关机或删除原始结果。免费额度若即将结束，保存完整状态并报告，不擅自切换付费。确需新增硬件/算法变更时集中列清原因。

最后交：一个REPORT.md；全部run配置和逐例曲线；adapter/phi小checkpoint；基座revision/hash；可续训状态；环境与真实卡时。报告首先回答：

1. 普通LoRA是否把任务学会？
2. phi是否更会恢复/更新？
3. 训练噪声与应用噪声各是否有用？
4. 哪种组织最好，是否只靠更多容量/计算？
5. 非目标信息和后续可学性是否真正保留？

运行完成但结果不好是有效负结果；缺算子、没训完、未覆盖事实、旧报告被错加载是缺失/无效，不能混写。
