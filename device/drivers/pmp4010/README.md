# pmp4010 — 机身抬降电机 A 驱动

PMP4010 ×4，逻辑总线 `can-2`。物理映射由 `device/config/device.json` 提供。厂家 E06 / D01 手册已收到，资料与反馈能力的唯一声明见 [硬件资料索引](../../../docs/硬件手册/PMP4010/README.md)。

当前是**有证据的受保护骨架**：OD共79条、73条verified、6条unverified；设备上电自动使能、内置终端等也未确认。`PMP4010_OD_VERIFIED=0` / `kOdVerified=false` 保持，全局发帧守卫关闭生产协议路径。`km3_canopen` 与 `km3_driver_api` 当前不存在，`CMakeLists.txt` 仍按目标存在性保护接线；补齐OD也不会自动实现C++ CANopen。

## 文件与边界

| 文件 | 职责 |
| --- | --- |
| `driver/od.json` | 唯一结构化字典，逐项保存status/cite及冲突说明 |
| `driver/od_generated.hpp`、`tools/hwtui/canopen_consts.py` | 由 `tools/hwdriver/gen-od.py` 同源生成，禁止手改 |
| `driver/pmp4010_driver.hpp` / `.cpp` | 生命周期、配置校验、全局发帧守卫；协议路径未实现 |
| `CONTRACT.md` | 上游调用、非实时线程、发帧与验收边界 |
| `tools/pmp4010.py` | Python维护入口，复用共享hwtui标准层 |

驱动只依赖 `km3_core`、`km3_hal` 及未来 `km3_canopen` / `km3_driver_api`；不在实时线程执行总线事务。现有C++接口和错误类型以头文件为准。SDO即使只读也会发送请求；C++尚不提供已验收的读写服务。

## 工具使用

```sh
python3 device/drivers/pmp4010/tools/pmp4010.py --help
python3 device/drivers/pmp4010/tools/pmp4010.py --test
python3 device/drivers/pmp4010/tools/pmp4010.py --bus can-2 --init --read-only
```

`--test` 严格离线：校验本地配置、OD与共享MockBus，不能证明控制器硬件回环或对端通信。`--init --read-only` 为 `--init --verify` 的别名，检查板端配置/服务而不写配置。下表只描述能力，不授权现场发帧或运动。

| 操作 | 总线行为 | RT控制器active时 |
| --- | --- | --- |
| `--test` | 离线，无socket/子进程 | 不受限 |
| `--od-dump` | 仅展示本地OD元数据，不打开总线 | 不受限 |
| `--init --verify` / `--init --read-only` | 读取配置/服务状态 | 不受发帧守卫限制 |
| `--raw`、`--bus-stats` | 被动监听/统计 | 不受发帧守卫限制 |
| `--scan` | 主动SDO上传请求，只读对象但会发帧 | 须显式 `--i-know-rt-is-running` |
| `--nmt`及其他发帧操作 | 会改变节点状态或发送请求 | 须显式 `--i-know-rt-is-running` |
| `--motion` | 全局运动闸门关闭，拒绝 | RT确认也不能绕过运动闸门 |

`--scan` 用标准层探测及身份读回；`--timeout 0.05` 可设每次事务的主机等待秒数，属于主机策略，不是厂家标度。只读上传仍可能影响总线负载，不能把 `--scan` 描述为被动探测。工具不负责接口up/down、速率修改或服务重启。

## 本地验证与交付

```sh
python3 tools/hwdriver/gen-od.py --model pmp4010 --check
bash tools/check-drivers.sh
bash tools/check-manual-citations.sh
bash tools/check-docs.sh
python3 device/drivers/pmp4010/tools/pmp4010.py --test
python3 -m unittest discover -s tools/tests -p 'test_deploy_hwtools.py'
bash -n tools/deploy_hwtools.sh
```

C++检查由驱动测试验证守卫与零帧，不以宿主mock替代实机验收。默认无旁路编译应被生成头的 `#error` 拒绝；`KM3_ALLOW_UNVERIFIED_OD=1` 只用于隔离CI/mock验证，不是板端运行许可。实际编译命令与结果见本轮验证记录。

使用 `tools/deploy_hwtools.sh --host <host> --version <version>` 先做dry-run，dry-run不执行SSH。显式apply才交付版本目录、原子切换current并在验证失败时回滚。交付布局为 `<release>/hwtui/` 与 `<release>/drivers/pmp4010/{pmp4010.py,od.json}`；不单独scp一个工具脚本，否则共享库和OD可能缺失。板端配置仍从 `/etc/km3/device.json`（后备 `/opt/km3/current/device.json`）读取，不由工具包替换。本轮未部署生产版本；临时源码验证与生产交付的边界见本轮验证记录。
