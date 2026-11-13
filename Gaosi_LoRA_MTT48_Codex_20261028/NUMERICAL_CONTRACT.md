# 数值契约｜LoRA状态、噪声、φ、终点元梯度

**本文件定义新runner的数学，不表示完整Qwen元训练器已实现。** reference/core.py是可运行小张量原型；tools/probe_backend.py可在目标设备测试部分底层路径。不得把它们当生产训练命令。

## 1. 单一转移入口

    transition(base_frozen, adapter_state, moments, history,
               support_D, support_R, phi, scalar_parent_stats,
               step, K, q_apply, organization, gradient_mode)
       -> adapter_next, moments_next, history_next, diagnostics

基座forward必须参与对输入及adapter的自动微分。`requires_grad_(False)`冻结基座参数；不能用`no_grad()`包住整个基座forward，也不能把某层输出detach：后层的误差需要经过冻结层传到前层LoRA。

query不进入此接口。元训练外层读取meta-query；正式应用只用support/retain，φ为固定参数。生产只收集A/B的梯度，不能意外让embedding、lm_head、norm重新可训练。

LoRA基座与因子不在forward里自动merge/unmerge；它会改变可微图和参数身份。需要部署合并时，在轨迹完成后单独执行。

使用PEFT时固定实际版本并验证导出/重载/forward一致性。高阶路径可采用本包原生LoRALinear＋torch.func.functional_call，数学与对应PEFT设置在FP32下匹配；不能为了某个高阶算子简单把LoRA层换成不等价方法。所有组共享attention后端、mask及精度。

## 2. 噪声和factor gauge

同一个ΔW可写成`s*B*A = s*(B*T)*(T^-1*A)`。对A/B原始数值加相同σ可能产生不同有效权重破坏。主协议先用thin QR统一输入基，再对有效系数C加高斯。

`A.T=Q R; Ac=Q.T; Bc=B R.T`的乘积严格等价（有限精度误差另报）；所有分支包括q=0都做相同变换并重置moment。QR只在episode/夜间边界做，非内层每步。

主噪声在C中各元素iid Gaussian，但ΔW中的噪声为`Z Q.T`，有固定低秩协方差。不能把其称为全参数iid Gaussian或完整分布保持。

单分支能量推导：

    E ||sqrt(1-q) C + sqrt(q)*sigma*Z||²
       = (1-q)||C||² + q*sigma²*d_out*r = ||C||²

交叉项期望为零；`sigma=||C||/sqrt(d_out*r)`。这不是单个采样严格等能量，也不保持所有一阶统计/谱或能力。多支sum存在交叉项，需直接算合成增量。

有效范数可无需实例化完整权重：

    ||sBA||²_F = s² sum((B.T@B) * (A@A.T))
    ||Σ_j s_j B_j A_j||²_F
      = Σ_ij s_i s_j sum((Bi.T@Bj) * (Ai@Aj.T))

差值用一组正系数和一组负系数计算。统计用FP32/必要FP64，负的极小舍入只在sqrt前裁到0，日志保留原值。

noise seed独立于data seed；同父模型方法共用同一受损适配器字节或相同噪声样本，不能每种方法独立挑幸运噪声。parent stats允许读，parent完整A/B只能构造起点/评价，不供φ直接回拉。

## 3. 普通基线与学习型转移

内层 losses 是有效答案token均值 LD、保留token均值 LR；分母明确且固定语义，不在删除F后擅自改变R权重。

    gD = grad_psi(LD)
    gR = grad_psi(LR)
    g = gD + wR*gR

如果启用global-norm裁剪，将由g得出的同一个clip factor作用于gD和wR*gR。只有可训练因子计入范数；O2的A视为不可更新，不能用其梯度参与配平后丢掉。

    mD' = .9*mD + .1*gD
    mR' = .9*mR + .1*wR*gR
    v'  = .999*v + .001*(gD+wR*gR)^2
    denom = sqrt(v_hat + 1e-16) + 1e-8
    uD=mD_hat/denom; uR=mR_hat/denom

β偏差校正按真实inner step计一次；wR在一条轨迹内固定，默认.25，dev可.1或1。weight_decay=0先避免隐形收缩。

    alpha = exp(log(4)*tanh(raw[:2]))
    Δψ = -eta*(alphaD*uD + alphaR*uR) + eta*.1*r*e
    r = sqrt(mean((uD+uR)^2)+1e-24) - 1e-12

φ-L的e=0；φ-X为共享坐标网络tanh输出。主phi的输出头零初始化，r非负且两路抵消时不强行产生大幅残差。baseline包括未训练phi、对应正ridge Adam、标准库Adam、固定factor-LR；不要把默认LoRA的A/B lr没调好当phi独创性。

`eta`的开发初值为3e-5/1e-4/3e-4，不能从全参数方案直接固定1e-5。两因子局部scale由明确LoRA s参与forward，不在更新后又乘一次alpha/r。

## 4. φ输入：48维的完整约定

所有统计按整个因子张量，不按chunk。基于真实连续量构造feature，H2中保留窗口内可微依赖；离散标志导数为0。smooth RMS/std加1e-24，日志另存原始量。`logrms=clip(log(rms+1e-12),-30,10)/10`。

| 索引 | 定义 |
|---|---|
| 0–5 | 旧6类onehot：embedding/head、QK、VO、gate/up、down、norm/bias；本轮只有中间四类会激活，不新增embedding/norm适配器 |
| 6 | depth/(layers-1) |
| 7 | has_depth |
| 8 | log1p(numel)/20 |
| 9,10 | log1p(rows)/12, log1p(cols)/12 |
| 11 | ndim/4 |
| 12,13 | is_bias/is_norm，LoRA因子为0 |
| 14 | sqrt(q)；factor噪声用其明示p，scheme在run_id另记，不假定两者等剂量 |
| 15,16 | k/max(K-1,1), log1p(k)/5 |
| 17,18 | log1p(LD_support), log1p(LR_support) |
| 19,20 | logrms(gD), logrms(gR) |
| 21,22 | cos(gD,gR), 梯度符号冲突坐标比例 |
| 23,24,25,26 | logrms(uD), logrms(uR), cos(uD,uR), logrms(uD+uR) |
| 27 | tanh((mean(ψ)-parent_mean)/(parent_rms+eps)) |
| 28 | clip(log(smooth_std(ψ)/(parent_std_floor+eps)),-5,5)/5 |
| 29 | log1p(RMS(ψ)/(parent_rms_floor+eps))/5 |
| 30 | 已计划本因子相对噪声尺度；只用q和标量统计，不能读取原Z |
| 31,32 | gD/gR中绝对值超过5倍自身RMS的比例，只作特征 |
| 33,34,35,36 | LD/LR/cos/上一实际相对update RMS的EMA，decay=.9 |
| 37,38 | tanh(LD-prevLD), tanh(LR-prevLR)，首步0 |
| 39 | is_matrix_or_higher，A/B均为1 |
| 40,41 | is_A, is_B |
| 42 | log1p(rank)/log(129)，不是归一化后偷偷变rank |
| 43 | log(scale)/5 |
| 44–46 | branch角色onehot：single、stable、plastic |
| 47 | 配对因子是否可训练（O2的B看到0；O3按当前day/night） |

退化因子的parent_rms_floor只用于特征数值安全，不用于凭空给零adapter注入噪声。首步EMA初始化为当前观测，update EMA首步0；下一步使用上一步真实更新，不读未来结果。

坐标头输入的8维：当前ψ相对parent标量标准化的tanh；uD/uR/uTotal各自RMS归一化tanh；gD/gR归一化tanh；sqrt(v)相对其RMS的log-tanh；gD*gR相对RMS乘积的tanh。另拼16维context。与旧方案相同，但只在adapter因子坐标上运行。

chunk默认262144；不能实体化整个adapter×16 context矩阵。H1按chunk重算phi图、对所有坐标贡献做sum，不求参数mean。H2要把共享张量统计的伴随梯度汇总后再传到support路径，不能每个chunk重新做一次完整LLM HVP。

## 5. 终点损失与梯度范围必须分别命名

元训练的父adapter在各episode重置；K步期间phi固定；外层`J_K`只在最后一步评价。本轮不同时训练q，不把初始噪声损伤作为phi罚项； q对phi独立时它的梯度为零。

H1：前K-1步不保留依赖；最终真实恢复后算gQ，对最后一步构建：

    surrogate = sum(stopgrad(gQ) * delta_phi)
    surrogate.backward()   # 正号；meta optimizer最小化

EXACT-H2-tail：前K-2步数值状态固定，后两步的ψ、moments、feature/history依赖全部保留/重放。`J_K`是完整终点目标，但梯度只覆盖最后2步。不要称“整个16步的精确梯度”。

精确K=2的验证应对完整两步函数做中心差分。K>2时有限差分只在固定边界后2步变化phi，不能让扰动后的phi重算整个前缀却期待等于tail梯度。

重放状态`x=(A,B,mD,mR,v,ema,loss_history)`，基座固定但前向运算可导。反向重计算时携带x全部伴随，support梯度`create_graph=True`。生产runner需在微型Qwen上测真实RMSNorm/SiLU/attention，而不只看参考线性层通过。

## 6. 生产精度和状态

基座BF16；adapter及其母本/梯度累积/moments/phi参数FP32。不要每步由BF16因子重建FP32母本。实际forward允许AMP，但所有对照相同dtype。LoRA branch的cast本身应可导，不能用detach省内存。

O1–O3以摘要/hash确认冻结基座未变，不需要每步把7B拷CPU做全量hash。O4允许**显式合并**改变基座，merge次数与误差单列，父FP32母本持续累积；合并后工作BF16重新构建，不保存旧KV缓存。

base权重更换、adapter写回、merge/reset后，任何旧KV/prefix cache都失效。训练use_cache=False。模型基座需要`.train()`/`.eval()`哪一种按dropout=0约定统一，不让两组随机性不同。

用gradient checkpoint优先非重入，避免冻结基座输入不require_grad导致LoRA无梯度。`enable_input_require_grads()`只能让激活有梯度，不应把embedding权重设为可训练。H2某fused attention不支持就回到同数学eager/math并对H1对照一致，日志记录速度。

MUSA H2缺算子时不能把全部代码迁到CPU再称卡上实验；小QR可以在CPU，主前反向必须明示实际设备。

## 7. 等价测试的边界

包内已实现并可执行：LoRA零输出与dense等价、冻结base仍有输入梯度、canonical q0等价、低秩范数/拆分/合并、噪声期望能量、组合Adam、H1链式梯度、含moments的两步LoRA数值导数。

未实现：完整Qwen元训练器、48维生产feature构造、组织调度、完整state重计算、实验评价。Codex需接入，这些功能不因本包测试通过而自动成立。

直接自动微分和手写链式测试必须独立从真实正query loss出发；禁止两边复制同一surrogate再说独立验证。一次0.5B/tiny检查不能代替3B真实profile，但也无需再造几十项外层文档审计。
