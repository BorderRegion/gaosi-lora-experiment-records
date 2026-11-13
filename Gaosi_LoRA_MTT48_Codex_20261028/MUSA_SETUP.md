# MTT S4000上的实际准备：先用配套栈，不把兼容工作变成新项目

## 1. 已知和未知

官方资料确认S4000为48GB卡并支持常见浮点格式；官方MUSA训练文档覆盖Qwen/LoRA，AutoDL也有摩尔线程PyTorch迁移说明。[R9,R10] **这些不能证明本项目的高阶导、任意PEFT版本、48GB真实峰值已通过。** 现有ms-swift最新示例有S5000场景，不能把它的速度或镜像要求直接当S4000事实。

现场记录 `mthreads-gmi`、可用的`musaInfo`、`uname -m`、`free -h`、`df -h`、现有torch/torch_musa/transformers/peft版本。可用命令缺失不冒造结果。CPU RAM和磁盘按实例实际分配，不从GPU容量推断。

## 2. 安装选择

优先现成AutoDL摩尔线程镜像。保留可用torch/torch_musa/SDK配套组合，禁止先`pip install -U torch`覆盖成CUDA或CPU轮子，禁止升级宿主驱动。需要依赖时新建可复用环境或隔离安装，锁版本和镜像信息。

普通PEFT或MT-MS-SWIFT用于父adapter与强基线；自定义phi与元梯度使用原生PyTorch转移，不能把`swift sft --tuner_type lora`的正常训练当phi元训练。PEFT新版本可能要求新的依赖，不盲追最新版；无法兼容时可用包内原生LoRALinear做等价适配并核对导出格式。

不默认bitsandbytes、CUDA FlashAttention、xformers、Triton、NCCL。使用普通Adam和可验证的eager/math attention；AMP按实测支持选择。主协议BF16优先，FP16作为标记清楚的替代，FP32用于短数值检查。系统支持BF16不代表每个算子/高阶路径都可用。

## 3. 只做三个递进检查

**A. tiny数学与MUSA算子**

    python reference/test_reference.py --out checks/cpu_reference_local.json
    python tools/probe_backend.py --device musa --out checks/musa_tiny.json

本包reference测试本身是CPU FP64参考；第二条才使用MUSA，但仍是小网络。不要隐藏设备fallback。

**B. 真实3B LoRA与高阶后端**

    python tools/probe_backend.py --device musa --model /实际缓存/Qwen2.5-3B \
        --rank 32 --out checks/musa_3b_lora.json
    python tools/probe_backend.py --device musa --model /实际缓存/Qwen2.5-3B \
        --rank 32 --real-h2 --out checks/musa_3b_lora_h2_probe.json

--model只接受已下载本地路径。H2 probe是两步SGD的后端检查，正式Gaosi含moment状态仍须在runner验证。需要数值诊断可以`--dtype fp32`，不要把精度不同的结果直接作算法对比。

**C. 真实研究转移与吞吐**

Codex接好runner后，运行一个3B父adapter损伤—恢复，真实phi-L和phi-X H1，再尝试精确H2-tail；7B跑普通LoRA和phi短更新。测峰值allocated/reserved、CPU RSS、forward/backward/query、坐标头、QR/保存、实际tokens和walltime。

H1能跑则继续关键实验；H2失败单列算子/堆栈和最小复现。若只是融合核问题，用数学等价实现；若支持仍不足，不伪装成精确H2，也不阻塞所有H1/持续学习。

## 4. 资源与使用范围

3B BF16基座约6.18GB，7B约15.22GB；r32全7类适配器的FP32参数/梯度/三状态分别约1.20/1.61GB，来自静态计算，不含activations。S4000 48GB从状态容量上适合本轮，真正风险是后端和激活/重计算性能。

先序列512 microbatch1，再按真实余量提高吞吐。不要把7B下调为0.5B而不报告；如果7B/H2确实受限，列出什么能运行、什么缺失、需要哪类设备，不能只说“显存不够”。没有依据不预订额外卡。

免费两个月用于充分训练与独立确认，不意味着无限网格。正常实现/短诊断/本包core可连续推进；不自动修改实例账单、重启/关机、租卡或开收费API。用户明确授权后才自动关机。没有当前价格信息不估RMB花费。

## 5. 交付

一份`ENV_READY.md`，明确`ordinary_lora_ready`、`phi_H1_ready`、`phi_H2_ready`、`7B_ready`，各自PASS/PARTIAL/FAIL/NOT_TESTED；另附实际锁版本和原始短日志。不要在环境部分写长篇文档然后不跑真实恢复。
