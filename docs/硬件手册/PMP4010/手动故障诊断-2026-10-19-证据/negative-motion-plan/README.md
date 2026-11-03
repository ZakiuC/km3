# 两个独立的单次计划

仅本地准备，未执行SSH、硬件读写、复位或运动。两脚本固定已验收0218包，不能与正在准备的中文报警新包混用：

- payload：`/var/tmp/km3-pmp4010-diag-20261019-0218ab4207eb/payload`
- archive SHA256：`0218ab4207ebf09948feb995ec360cbe6d22fb3dc94a0062cafc5b3dd4397761`
- manifest SHA256：`a41b88269819e718e1486876be8d99502a773a401ef084343b57d597fb818b71`

`pinned_payload.py` 先检查manifest及所有成员，然后限制导入来源。各脚本默认只打印计划，不导入设备工具、不开总线。显式执行标志本身不是用户/主会话的授权；实际板端调用由主会话在现场安全、设备身份、总线占用复核及计划审查后独立决定。这里没有自动串接两个动作的入口。

## reset-only

脚本 `reset_only_once.py`。拟执行时与 `pinned_payload.py` 放在同一受控目录，拟argv：

```text
python3 -I -B /var/tmp/km3-pmp4010-diag-20261019-0218ab4207eb/reset-only-plan/reset_only_once.py --execute-reset-only-once --manual-session-reason '<根线程记录的本次已有手动会话OD授权理由>'
```

`--manual-session-reason` 必须由根线程提供真实授权理由，不自动生成。先复制原args，把该理由交给原 `tool.refuse_motion(out, action, args)`；该既有接口决定OD闸门是否放行并逐条报告未核对项，不修改OD状态。然后调用原配置/接口/RT授权守卫，严格确认context为can1/1Mbps且控制器/args节点均为1、node_range为1；未通过不发送任何SDO。

通过后固定8次SDO：

1. 上传2002:01（UInt16）。
2. 上传6041:00（UInt16）。
3. 上传603F:00（UInt16）。
4. 仅全部前置成立时下载6040:00=0000：mode=0、Switch on disabled、Fault bit=0、603F=2312。
5. 下载6040:00=0080，产生bit7上升沿。
6. 下载6040:00=0000。
7. 上传603F:00一次。
8. 上传6041:00一次。

每交易至多250ms，SDO阶段总3s；WireGuard只允许唯一pending和8个精确601请求，逐个记录原始TX/RX帧，接收必须为对应581 UInt16实读或写ACK。每个下载只发送一次。任一通信错误/超时/隔离/总预算耗尽立即结束，不继续后续0、不再读回、不执行stop/disable/enable，也不进行通信恢复；finally仅关闭socket。未读写1003/200E，未写603F。执行目录独占创建，拒绝相同准备运行的第二次执行。

E06 §9.3（手册3849–3853行）只说明脱使能后的bit7上升沿手动复位。当前已知Fault=0，手册并未保证此动作会清Warning或最后603F；即使三个写均收到ACK、甚至读回603F=0，也不自动授权运动、不声称根因消除。执行后先将实际结果反馈根线程，由根线程决定后续。

## 相对-10mm、100rpm

脚本 `relative_minus10_once.py`。同目录需 `pinned_payload.py`，拟argv：

```text
python3 -I -B /var/tmp/km3-pmp4010-diag-20261019-0218ab4207eb/negative-motion-plan/relative_minus10_once.py --execute-once
```

复用原SimpleController：scan/refresh → authorize → set_value(speed,100) → review_action(relative,-10) → execute_action一次。标定、起点、驱动607D限位、607A/6063 Int32、轮廓速度上限、100rpm换算、模式/偏置/状态及所有proof守卫均由原API重新核对。明确采用4mm/电机轴转。当前603F=2312时，原_snapshot在任何运动下载前锁定目标并落诊断，脚本不reset、不清码、不绕过守卫。

唯一目标在review中冻结；既有事务按原协议先提交、使能Halt、再重写同一个冻结607A和新ACK握手，不产生第二目标。原三阶段保留各自move_timeout（默认各10秒）；ACK完成后只用原refresh监测，另有10秒观察预算。完成要求该同会话目标、位置相等、速度零、bit10同时成立。

正常完成沿用原close，仅发退出Shutdown 0006并确认operation enabled bit2清零；这不是Switch on disabled或机械停止证明。若退出未确认，结果保留target_reached并返回非零。监测异常/观察超时复用原_cleanup_failure，按责任与隔离规则清理2→0并锁定；随后close看到blocked/quarantined不会重试。通信已隔离时，后续cleanup/close均零新增发送。无第二次目标、复位或自动重试。

## 本地验证

`test_prepared.py` 是独立MockBus夹具，真实socket已由原fixture禁止。13个测试覆盖：默认计划0IO；603F2312时运动0写；一次-10mm在Mock标定下为-327680U且100rpm=218453U/s，原CW顺序及退出0006；速度超限0运动写；运动超时只清理一次且无第二目标；背景通信隔离后无cleanup/exit发送；reset精确8交易3CW与8对原始帧；前置不符0写；80写ACK丢失后无后续0或读回；后603F失败不追加6041；OD授权缺失/闸门拒绝0发送；配置alias漂移0 SDO；白名单外写在transport前拒绝。

本地命令、rc、原始输出和脚本hash见 `local-verification.json`。Mock结果不代表板端复位有效或运动验收。测试临时目录/总线由fixture清理，保留上述计划及证据。冻结源码未由此任务修改。
