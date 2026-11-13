# v7 原始梯度诊断准备

状态：PREPARED_NOT_ENQUEUED。现有主训练、设备锁与15个生产队列不变。

旧 diagnose_training_gradient.py 监听 torch clip，而 v7 调用 gaosi.meta.stable_phi_clip_，因此没有保存 v7 readiness 的原始梯度。新工具只在独立进程包装当前实际裁剪入口，保留裁剪前逐元素梯度、phi、裁剪后梯度、逐参数范数和诊断前一episode检查点；异常时恢复hook并保存已取得的证据。

默认从生产seed重新构造前8个已知开发episode，总64次inner updates、一次重建。生产源码、配置、数据选择和输出目录不改。它不是历史episode7的精确重放；目前没有找到历史episode6之后的保存状态。CPU读取和保存会引入同步，可能影响故障复现，执行成功也不能证明高阶梯度正确。

输出只允许 runs/diagnostics 独立目录；拒绝正式输入、变更的配置或源码、超过8个episode、超过2次重复、已有输出。执行前验证继承的既有device_guard锁，防止绕过串行化。当前仅完成CPU校验，未排队或运行64步诊断，不占用生产GPU。

后续如决定执行，应在生产自然释放设备后使用job.py显式锁，采用新job名，并把额外成本单列。保存的before_episode_7.pt仅属于新诊断轨迹，可用于该轨迹的单episode重复比较，不能标成原始episode7状态。
