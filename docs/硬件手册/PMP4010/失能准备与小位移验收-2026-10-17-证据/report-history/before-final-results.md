# 失能准备与小位移验收 — 2026-10-17

当前已完成最终包冻结和隔离板端纯加载复验。失能准备实现、离线测试及新包加载已有结果；最终两种真实TUI/PTY Mock排练亦已完成；现场准备和一次小位移结果仍待主线程通知后单独收录。`hardware_acceptance=false`，不以既往加载或模拟成功代替本次现场验收。

## 用户授权与执行范围

用户已确认 `192.168.0.188`、`can1`、节点1和现场安全，授权独立失能准备动作及随后一次约+0.1°的小角度动作。主线程保留执行协调、预览审阅和现有 OD 闸门审批。准备与运动必须分别使用原 TUI 审阅和精确确认串，准备授权不替代运动确认。

独立准备范围：strict disabled、PP模式1、606C=0、607C=0，5次采样、间隔0.2s；只对此准备动作允许 bit10=0。607A 写实时起点并读回，再写6081=100 U/s并读回，每项关键状态和位置仍须复查。此准备动作无6040、NMT、cleanup、持久化或自动重试；取消、超时或隔离不能换客户端继续。

准备结束后须重新满足原 PP bit10 静止守卫并经过运动的两段确认。如果 bit10 仍为0，必须停止，不为改变状态而使能、不追加例外或重试运动。独立急停、制动、机械停止和抱闸结果不能从软件失能推定。

本报告先完成仅本地整理；随后主线程指定最终冻结包并授予唯一板端纯加载窗口，已完成SSH、新 /tmp 上传和纯加载，窗口结束立即交还互斥权。未打开CAN，未执行设备参数写入、使能或运动，未更改服务、配置或current；后续现场操作归指定唯一操作者。

## 实现边界与回归

本次实现只改 `pmp4010_bench.py`、`pmp4010_tui.py`，新增 `_test_prepare.py`；其余源未由本报告任务修改。最终冻结三文件哈希见 [最终源核查](失能准备与小位移验收-2026-10-17-证据/final-owned-source-check.json)，按变更清单的final字段逐项实读，3/3匹配。初次整理文件仅保留当时读取的结构，不作为通过断言。

独审发现的两项问题为：已取消准备 review 可被篡改为 enable；采样首读 fault 可被末读状态覆盖。主线程已报告修复，并在实现证据中保存相关独立定向回归。报告保留原发现及失败，不因最终通过删除历史。独审单独复测使用内存脚本、无落盘原始记录，不伪造其raw命令或输出；本报告引用实现worker落盘的review-fix-directed 24条定向回归及原始stdout/stderr/rc。

已读原始结果：

| 检查 | 结果 | 证据范围 |
| --- | --- | --- |
| 型号 Python 全集 | 241 tests，rc=0 | model-delivery-retry |
| 准备定向测试 | 24 tests，rc=0 | review-fix-directed |
| 测试台交付 PTY | rc=0 | bench-pty-delivery |
| hwtui / tools/tests | 217 / 8，均rc=0 | 最终验收索引 |
| C++ Host/ASan/TSan | 复用旧17/17，各rc=0 | 本轮未重跑 |
| ARM64 构建/数学库 | 核hash复用旧构建 | 本轮未重建 |

新增准备专项24条及1条help回归的完整命令、stdout、stderr、rc保留；型号全集从先前216增至241。C++与数学实现未改的复用依据见 [复用证明](失能准备与小位移验收-2026-10-17-证据/initial-local-snapshot/acceptance/cpp-math-reuse-proof.json)，不能描述为本轮重新执行了17×3或ARM构建。

## 已知失败与重试

实现交付索引明确保留初次和重试结果：

- `bench-pty-delivery`：rc=`0`。
- `bench-pty-final`：rc=`0`。
- `bench-pty-help-start`：rc=`1`。
- `bench-pty-retry`：rc=`1`。
- `bench-pty`：rc=`1`。
- `capture-delivery`：rc=`0`。
- `capture-mock`：rc=`0`。
- `first-full`：rc=`0`。
- `model-delivery-retry`：rc=`0`。
- `model-delivery`：rc=`1`。
- `model-final-after-help`：rc=`0`。
- `model-final`：rc=`0`。
- `model-frozen`：rc=`0`。
- `prepare-directed-final`：rc=`0`。
- `prepare-final`：rc=`0`。
- `prepare-first`：rc=`0`。
- `review-fix-directed`：rc=`0`。

`model-delivery` 曾为241条中的9项失败（帮助画面断言），随后 `model-delivery-retry` rc=0；早期三次PTY rc=1，最终交付PTY rc=0。各次输出均原样保存，原因细分仍以实现方最终说明为准，不把失败自动归因于现场设备。

## 真实 TUI 本地排练与现场待办

现有 rehearsal-1 使用真实curses/PTY、原Form、原review和execute路径，但传输为MockBus；607A对齐后bit10=1是夹具合成行为。它用于有利路径排练，不证明固件将置位，也不证明真实运动。自动核证第一次因“稳定采样”阶段名匹配错误rc=1，修正核证脚本后对同一原始数据核证rc=0，未重新执行动作；原失败保留。

最终冻结源两种场景均已核证PASS，bench/tui实读哈希与最终包匹配，使用原TUI/Form/token/execute路径，真实SSH调用及真实CAN打开均为0：

| Mock场景 | 准备 | PP及退出 |
| --- | --- | --- |
| final-pty-success | 五采样、2项参数下载、0控制字，bit10=1为显式夹具合成 | 单次PP +36U，ACK0→1→0，终点-1429、速度0、确认失能；q退出含同client额外6040=6，子进程rc0 |
| final-pty-bit10-zero | 准备成功，五采样、2项参数下载，bit10保持0 | F5审阅被原静止守卫以target reached未置位拒绝；PP execute=0；整个会话6040=0、NMT=0、退出额外写=0，子进程rc0 |

[成功模拟核证](失能准备与小位移验收-2026-10-17-证据/final-pty-mock/final-pty-success/verification.json)与[bit10持续0拒绝核证](失能准备与小位移验收-2026-10-17-证据/final-pty-mock/final-pty-bit10-zero/verification.json)同目录均包含完整terminal.raw、按键、审计、journal和screen。两者都是模拟，不能证明真实固件行为或机械运动。

现场结果待主线程通知：目标/标定/起点、脚本行程和单次预算、每项preview及精确确认、准备五采样与写读回、准备后bit10、若允许的单次相对目标和ACK轨迹、最终位置/速度与确认失能、清理是否产生6040及原始日志。未经记录不填写成功。

## 最终包与隔离板端加载

最终包184426 bytes，SHA256=`e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6`。板端再次核对大小和SHA，21成员安全解压通过。最终bench/tui和交付清单一致；70项源码/产物SHA、七闸门及原始输出均持久保存。

身份分别确认hostname=`RK3588-Tronlong`、device.id=`km3-test-001`、DT model=`TL3588-EVM`。新目录为 `/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O`；SSH使用既有密钥、BatchMode与StrictHostKeyChecking。

实际加载库：`/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/payload/drivers/pmp4010/libpmp4010_velocity.so.1.0.0`；库SHA256=`59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404`。readelf为AArch64；ldd依赖全部解析；ABI=1，所有8组命令rc=0。

```sh
python3 -I -B /tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/payload/load_smoke.py --check-missing-library
```

smoke真实末行（rc=0）：

```json
{"result": "PASS", "native_vectors": 33, "python_vectors": 33, "native_int32_vectors": 14, "forbidden_io_attempts": 0, "hardware_acceptance": false}
```

16imports均来自包内；缺库分支按预期拒绝。禁止IO0是Python audit实际计数，不是独立syscall跟踪；smoke仅验证导入/数学，不运行TUI或设备控制。

35个既有文件SHA、current及三服务状态/PID/启动时间前后完全一致；current仍是 `/opt/km3/hwtools/releases/0.1.5+zh-5e31e29`。这不是所有进程/文件的连续监测。

补取RT被动快照：PID3283、argv无--enable-sync、release0.1.1二进制SHA=`68a978d6c63763e53b4fe33eb4ec844a85267c22ddcfe0efdc3b669678005570`；fd0=/dev/null、fd1/2是同一AF_UNIX socket，CAN接收表为空。此单时点/非原子快照不证明总线独占、外部主站不存在或之后不会打开CAN。

加载结束已通知主线程交还互斥权，未继续SSH。最终型号241、hwtui217、tools8和PTY均rc=0；四闸门及三生成器逐项rc=0，deps保留Python3映射提示。C++/数学和ARM仅核hash复用旧结果，本轮未重建或重跑CTest。

- [完整加载argv/远端脚本/stdout/stderr/rc](失能准备与小位移验收-2026-10-17-证据/final-local-snapshot/load/command-index.json)。
- [最新RT归属原始输出](失能准备与小位移验收-2026-10-17-证据/final-local-snapshot/load/08-owner.stdout)。
- [最终验收索引](失能准备与小位移验收-2026-10-17-证据/final-local-snapshot/acceptance/index-final.json)。
- [70项源码/产物清单](失能准备与小位移验收-2026-10-17-证据/final-local-snapshot/acceptance/source-sha256-final.json)。
- [包冻结清单](失能准备与小位移验收-2026-10-17-证据/final-local-snapshot/package/full-source-sha256.json)。

## 本地证据索引与清理

- [实现交付索引](失能准备与小位移验收-2026-10-17-证据/initial-local-snapshot/implementation/delivery-index.json)。
- [本地验收初次快照索引](失能准备与小位移验收-2026-10-17-证据/initial-acceptance-index.json)。
- [原TUI排练操作计划与已跑边界](失能准备与小位移验收-2026-10-17-证据/initial-local-snapshot/motion/操作计划.md)。
- [逐文件复制来源、大小与SHA256](失能准备与小位移验收-2026-10-17-证据/initial-copy-provenance.json)。
- [当前整理状态](失能准备与小位移验收-2026-10-17-证据/status.json)。

初次快照独立保存，不覆盖各worker仍在更新的 /tmp 原始证据；最终实现/验收/包/加载已另存final-local-snapshot。所有证据有意保留，无提交、无远端清理。本报告相关最终docs/citations闸门待最终结果收录后运行，不声明现在已通过。

