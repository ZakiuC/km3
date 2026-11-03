# 已暂存一次执行入口

`execute_staged_once.py` 是最终候选入口；`execute_authorized_once.py` 保留为共享取证函数及早期组合方案，不运行它的 main。精确冻结输入及待执行 argv 分别见 `frozen-input.json`、`planned-execution-command.json`。该文件的授权标志本身不授予运动权限；必须等根代理明确的一次执行指令。

当前实测仅执行过三次 SSH：`readiness-passive`、`stage`、`cold-load`，均 rc=0。未执行 CAN、enable、Go、reset。readiness 与此前 reset 后身份、boot/config/current、三服务完整属性、RT fds、rcvlist、CAN linkinfo/flags相同，计数增量见 `readiness-counter-delta.json`。两份最终冻结源码已存入 bootstrap 固定目录（payload 外），逐项读回 SHA 一致；ARM 速度库冷加载成功，冷加载期间禁止创建任何 socket。

最终入口先验证自身、冻结清单、原两文件、原 bootstrap、共享 runner、passive 和 baseline 的 SHA，并检查已完成 stage/cold 的 rc 记录。本地独占声明后取一次 fresh 被动状态；任何变化即停止。之后通过 `ssh ... 'python3 -I -B -'` 的 stdin 在远端重新校验两文件 SHA，独占创建 `execution.attempt`，执行原冻结 bootstrap bytes 一次。不会重传文件或重建 stage。

任何应用 rc 非零或未明确确认 OperationEnabled 清零，立即保存 `motion-immediate-feedback.json` 与 summary 并退出，不自动追加板端命令。rc 只按应用退出码记录，不推断为通信丢包。只有正常结束且确认该状态位清零才采集 after 被动状态和本次固定 run 目录的有界日志/hash。失能状态位不证明机械停机；不执行回程、reset、重试或服务操作。

本地验证：`python3 -I -B docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/motion-live/verify_runner.py`，rc=0，9 tests，OK。覆盖计划零 SSH、SHA 改动、缺失暂存记录、被动变化、应用失败、未确认失能、正常后采样、本地防重入及远端 marker/hash 拒绝重放；远端 wrapper 的本地试验只用合成文件。四文件 compile 检查成功，本地默认 plan rc=0。原始输出与 rc 保留在同目录。
