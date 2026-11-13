## 2026-10-30 11:37：episode7追踪未复现，启动受控同步/异步前缀对照

前轮简检/重放取证属progress。episode7 traced reconstruction于11:26:28退出0，8个inner step的ld/lr、
各路token和exact标记与03:09结束的新前缀episode7完全相同；该同状态重放raw phi norm却只有
0.061408095512922534（原新前缀9.47850229661424e21）。6次support HVP边界均有限，
incoming最大0.0161841、outgoing最大0.0533408，未触发异常capture。
39文件（含原始解析梯度、完整日志/源）已下载并核SHA，
records/episode7_trace_results_20261030_0331/agent_review.json；
归档SHA256=e358b57e81e792f41bd213d22863ede6efa3aaafc90b29fd2aea0c40ef515679。
此结果没有定位根因或修复路径；独立短重放正常不能推翻长训练的巨大/非有限梯度证据。

11:32:05启动两个新、各限8个K8 episode的开发前缀：
1. diagnose_core_prefix_v7_launch_blocking_r1，wrapper522975，child522977/start ticks60519450，
   仅该子进程设置MUSA_LAUNCH_BLOCKING=1。
2. diagnose_core_prefix_v7_async_control_r2，wrapper523002，child523003/start ticks60519461，
   子进程显式unset该变量，等待第一项释放设备。
二者使用完全相同冻结的v7源码/生产输入配置，复用原instrumented prefix工具，原锁inode31148027670；
本次没有修改生产数值源码、全局环境、驱动、torch，亦未重启正式主训练和下游队列。
安装的torch_musa源明确识别此变量，副本保留在远端对照记录目录；实效仍要看实际证据。
每个诊断会在自己的输出里做最多8次phi/optimizer更新（合计128 inner updates、16 meta updates），
不是无更新重放，不计主预算，禁止推广诊断phi。正式保存预算仍640，科学有效性未获验证。
二者是从初始化独立运行的对照；若早期梯度不同，后续phi和轨迹可分叉，不能称后续episode仍同状态。

11:34:24实核两child身份有效，仅blocking child持设备锁、env确为1；control仍原device_guard等待。
blocking首步48.67秒，相比原异步约16秒明显更慢，对照合计初估约1.5–2小时，需测时修订；
这仅是本组诊断耗时，完整协议完工日期仍无法可靠估计。
证据records/prefix_launch_blocking_comparison_20261030_0333/launch.json及两个新configs。

原11:55一次性记录器在到期前已停止并换为覆盖该对照的本地有界记录器，始终只保留一个，
使用原本地观察锁。当前PID4154412/start ticks57710112，首采样11:55:17（03:55:17 UTC），
之后每次采样约30分钟一次，最多8次；到全部结束、失败/身份异常/巨大梯度时停止并要求agent复核。
状态records/prefix_launch_blocking_comparison_20261030_0333/observer_state.json。
准备时发现重复采集路径需要动态更新，已在首次SSH前修正并校验连续两次的远端语法/目标路径；
此为本地观察器替换，未触及任何GPU任务。不能用观察器PASS或有限梯度宣称H2已验证。
完整实验goal继续active。以下为历史。

