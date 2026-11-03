# 本轮离线真实 curses/PTY 起步核证排练

状态：只读诊断已完成；本文件只规划离线排练。等待实现冻结及主线程给新 bundle，当前没有运行新动作。唯一板端窗口保持无 CAN 打开，不重复诊断。

## 目标及边界

新动作 B（起步核证）仅在 Halt 保持下核证当前位置新命令的 ACK 与内部 6062 采纳，结束确认失能；始终不解除 Halt。该排练不能报告真实位移、机械停止、抱闸或现场运动通过。

全部动作经原 PmpApp.run、真实 curses、PTY 键码、原 Form、review、精确 token 和 F5。观察器只逐次转调原方法一次，不改返回或状态。不能直接调用 controller.execute，不预设 rt_ack、OD 理由、settings、pending 或 review，也不能把 Mock 回调带到真板。

## Mock 启动状态（来自 2026-10-19T00:53:28Z 只读诊断）

| 对象 | 启动值 |
| --- | --- |
| 6040 / 6041 | 0 / 0x0260 |
| 6060 / 6061 | 1 / 1 |
| 6064 / 606C | -1470 / 0 |
| 607A / 6062 | 7800 / 7800 |
| 605D | 1 |
| 6081 | 500000 |
| 607C | 0 |
| 6067 / 6068 | 100 / 5 |

沿用冻结包的标准 MockBus/MockNode 收发编码。在专用行为夹具中明确建模：607A 写当前点不改变 6062、不置 bit10；进入新 ACK 前，内部旧 trajectory 始终为 7800。任何错误释放 Halt/恢复旧 trajectory 均须记录为夹具发现的危险行为，不能以有利 Mock 自动跳到当前点掩盖。具体 Halt 控制序列、ACK 和 6062 时序待实现/独审定稿后绑定。

至少分开三个目录保留：Halt 内新命令采纳成功；Halt 写入不被接受（或读回不匹配）；新 ACK/6062 不随当前点命令更新。每个失败场景必须被守卫拒绝并核对原清理、最终失能、隔离与后续发送，禁止 fixture 自行修复失败。

## 原键盘流程（尚未执行）

1. 启动本地 Mock-only app；抓初始 RT 风险屏，确认启动默认无 rt_ack。
2. F5 显式确认本地 Mock RT 风险；等原扫描结束；Enter 进入节点 1 详情。
3. 按 B。若出现 OD 页，键入屏上原 token（Mock 预计 `OD mock-bench 1`）再 F5；不调用对象 setter。
4. 首次安全设置逐屏通过原 Form 输入。若沿用当前三字段：下限 -1470、上限 -1434、正预算 36，各自 F5。这里只是离线夹具坐标；B 的目标仍必须为当前点 -1470、计划位移 0。现场值绝不能从此处自动重放。
5. B 动作页 F5 冻结审阅。抓完整 screen、pending 观察事件及零执行计数；暂停并发主线程审核。此时不输入动作确认 token。
6. 主线程确认该冻结页后，逐字键入该页实际 token，再 F5。预期 token 名不预先硬编码，按冻结实现给出的值核验。
7. 等原 worker 完成，抓结果、每次 607A/6040/6081 写入、内部 6062、ACK 序列、Halt 始终保持、6064 始终 -1470、606C 始终0、原 cleanup 结果。记录 exact 控制字序列以及是否存在任何清除 bit8 的 operation-enabled 控制字。
8. q 正常退出，记录退出追加写入与同 client、child rc、transport closed；不 kill/自动重试。

## 必留证据

每场保存 argv/environment、terminal.raw、keys.jsonl、app-audit.jsonl、完整 journal、冻结/最终屏、mock-trace.json、源 SHA256、验证脚本及结果、所有失败及重跑。校验原 UI review/execute 次数、Form commit、F5 来源、token 完全一致；把 Mock 结果与板端只读结果分开标注。
