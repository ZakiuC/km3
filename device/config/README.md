# 设备配置校验与 SYNC

`device.json` 是现场模板；`device.id` 的 REPLACE 必须在部署时替换。
校验使用生产 C++ 解析器和 `ValidateConfig`，避免另写一份宽松规则：

```sh
bash device/config/validate-config.sh "$PWD/build-host/km3-rt-controller" /path/device.json
```

此入口在创建线程、实时设置及打开设备之前退出；非零表示加载或校验失败。

`network.sync_period_us` 对应 `RtParams::sync_period_us`，缺省 4000 微秒，
与未来周期控制使用同一参数快照。明确写入时只接受 JSON 整数 `1..1000000`；
拒绝 null、布尔、字符串、小数、负数和溢出。1 秒上限用于限制主站调度参数范围，
防止单位错误，并非电机时序保证。合法数值不等于总线带宽可行；默认 4000 微秒
来自 can-2 的带宽预算，1 微秒周期必然超过经典 CAN 的帧容量。

默认不打开 CAN、不发 SYNC。现场服务要显式添加 `--enable-sync` 才发帧；
本次没有修改或部署 systemd 服务。开启要求完整配置成功加载并通过校验，
并存在 `bus.can[]` 中唯一的 `logical=can-2, protocol=canopen` 端点。
物理接口只读取该端点 `iface`，位速率只读取 `bitrate`；程序核对 netlink 实际
速率，不重新配置接口。配置失败绝不回退为默认硬件发送。

SYNC 使用独立 SCHED_OTHER 线程，以 CLOCK_MONOTONIC 绝对期限推进，
预构造 COB-ID 0x080、DLC 0 的经典帧，每次最多一帧；发送失败计数并继续到下一个
未来周期，过期周期跳过，不突发补发。该线程独立于控制环及可能阻塞的日志边界。
实际调度策略和优先级在启动时核对并输出；累计成功/失败、连续失败、跳过周期和
最大迟到量通过原子统计交日志边界输出。统计是各字段近似快照，不保证跨字段一致。
日志阻塞不会阻塞发帧线程。

首次帧在工作线程完成调度校验后的一个周期；信号/限时结束或工作线程致命错误时
停止，join 后不再有帧。发送失败保留计数；不会自动重启控制器。`1007h=0` 时停发
不会自动产生 EMCY，当前心跳/看门狗也未启用，不能把停发作为安全停止。
所有电机对象保持原状：不写 1005h/1006h、PDO 类型、60C2h 或任何其他对象。
本实现仅供发帧能力验收；SCHED_OTHER 抖动尚未通过未来 CSP/运动时序验收。

`SocketCanDevice` 遵循 `IDevice` 单调用方独占约定，不内部加锁。
`Stats()` 的 tx/rx 是本对象应用计数，`LinkInfo()` 的错误/状态是内核接口累计值；
bus-off 总数取观察通知与内核累计值的最大值，避免重复相加。Close/Open 不清零对象
计数；接口销毁/重建后的累计值比较不提供新代际语义。`Stats`/`StatusOf` 的 netlink
读取允许等待至多 100ms，不能放入 SYNC 周期；该周期只非阻塞发送与有界收取错误通知。
`LastLinkError`/`LastSystemError` 暴露遥测与系统错误，错误帧不会伪装成业务帧。
`RecoverFromBusOff` 是显式 netlink CAN_RESTART（可能因权限不足失败），不会重开
socket、切换链路、重配速率或发送任何电机报文。

`network.sync_cob_id` 默认来自生成的 CiA 301 `COB_SYNC` 常量（128），允许配置
标准 11 位非零 ID，显式非整数、零或越界会拒绝。PDO 传输只提供标准层 API，
没有生产 CLI 启用开关；型号工厂仍受全局 OD 闸门约束，节点须来自已核对配置。
