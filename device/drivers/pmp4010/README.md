# pmp4010 — 机身抬降电机 A 驱动

PMP4010 ×4，逻辑总线 `can-2`。物理映射由 `device/config/device.json` 提供。厂家 E06 / D01 手册已收到，资料与反馈能力的唯一声明见 [硬件资料索引](../../../docs/硬件手册/PMP4010/README.md)。

当前是**受全局OD守卫保护的非实时实现**：OD共83条、77条verified、6条unverified；设备上电自动使能、内置终端等也未确认。`PMP4010_OD_VERIFIED=0` / `kOdVerified=false` 保持，全局发帧守卫关闭生产协议路径。`km3_canopen` 已有有界SDO/NMT与状态判定；`km3_driver_api` 仍缺，主工程按目标存在性保护接线。C++读写、CiA402及原始计数目标已实现，生产路径仍被OD阻止。

## 当前状态

- [x] 骨架生成（`new-driver.sh`）
- [x] 对象字典填充（83条中77 verified、6 unverified；含结构条目，见 [CONTRACT.md](CONTRACT.md)）
- [x] 生成物同步（`driver/od_generated.hpp`、仓库根目录 `tools/hwtui/canopen_consts.py`）
- [x] 隔离 C++ 验证：OD零帧契约15用例/4199检查，已核对假设备夹具18用例/887检查；默认及旁路宏显式为0的生产负编译均成功，旁路=1的隔离编译成功。普通与ASan/UBSan的5项CTest均通过。
- [x] 板端只读探测（历史1 Mbps通信成功，节点1对1000h:00h上传应答；不等于全部4台验收）
- [x] 完整身份读取与对照（既有1000h探测及本次1008h/100Ah、1018h:01h–04h、200Ch:02h–03h共8项读取完成；厂商ID/产品代码与E06不匹配，兼容性待厂家澄清，见 [验证记录](../../../docs/硬件手册/PMP4010/验证记录-2026-10-15.md#identity-readonly-20261015)）
- [x] **驱动实现**（有界SDO、类型化原始计数接口、CiA402序列；全局OD闸门保持关闭，尚未接入RT或板端验收）
- [x] **运动命令实现**（`--motion enable/disable/stop/home` 已填实并由离线假设备验证；三条准入闸门保留，生产运动仍被未核对OD阻止）
- [ ] **板端/带载运动验收**（本轮无硬件访问、部署或实际运动）

**身份差异**：本次节点1读到设备名 `NiMotion Servo`、软件版本 `V3.3`；厂商ID `0x00000509`、产品代码 `0x03000001`，分别不同于E06表10-5的 `0x2D`、`0x01`。读取及对照完成不表示型号/固件兼容性已确认。200Ch:03h读值8，对应E06 §3.6.1 表3-13的1 Mbps；7对应800 kbps，任务模板中的速率码说明有误。

**阻塞项**：上述身份差异须厂家澄清，另有6条unverified（见 [CONTRACT.md](CONTRACT.md#未核对的对象6-条-unverified)），分别涉及不支持的能力、缺失元数据/原图及主机策略，须逐项处置；手册原件或厂家澄清不能替代实际运动、失联和带载验收。`verified`仅表示已核对手册事实。

## 文件与边界

| 文件 | 职责 |
| --- | --- |
| `driver/od.json` | 唯一结构化字典，逐项保存status/cite及冲突说明 |
| `driver/od_generated.hpp`、`tools/hwtui/canopen_consts.py` | 由 `tools/hwdriver/gen-od.py` 同源生成，禁止手改 |
| `driver/pmp4010_driver.hpp` / `.cpp` | 生命周期、配置/宽度/权限校验、全局守卫及调用标准层的CiA402编排 |
| `CONTRACT.md` | 上游调用、非实时线程、发帧与验收边界 |
| `tools/pmp4010.py` | Python维护入口，复用共享hwtui标准层 |

驱动只依赖 `km3_core`、`km3_hal`、`km3_canopen` 及未来 `km3_driver_api`；不在实时线程执行总线事务。现有C++接口和错误类型以头文件为准。SDO即使只读也会发送请求；C++仅有隔离假设备验证，尚无生产/板端读写验收。字符串采用固定256字节结果，旧vector读接口须预留容量；详见[契约](CONTRACT.md#有界-c-实现与本地验收边界)。

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
python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
bash tools/check-drivers.sh
bash tools/check-manual-citations.sh
bash tools/check-docs.sh
python3 device/drivers/pmp4010/tools/pmp4010.py --test
python3 -m unittest discover -s tools/tests -p 'test_deploy_hwtools.py'
bash -n tools/deploy_hwtools.sh
```

C++检查包括零帧守卫、默认负编译、固定内存夹具实际事务/失败清理及共享金标重放，不以宿主mock替代实机验收。默认无旁路编译应被生成头的 `#error` 拒绝；`KM3_ALLOW_UNVERIFIED_OD=1` 只用于隔离CI/mock验证，不是板端运行许可。独立夹具的 `PMP4010_OD_VERIFIED=1` 为私有测试定义，不传播到生产或OD零帧目标；不允许将它用于连接电机的构建。

```sh
cmake -S device/drivers/pmp4010/tests -B /tmp/pmp4010-test -DKM3_SANITIZER=address
cmake --build /tmp/pmp4010-test -j2
ctest --test-dir /tmp/pmp4010-test --output-on-failure
python3 tools/hwdriver/gen-canopen.py --check
python3 tools/hwdriver/gen-pmp4010-access.py --check
```

使用 `tools/deploy_hwtools.sh --host <host> --version <version>` 先做dry-run，dry-run不执行SSH。显式apply才交付版本目录、原子切换current并在验证失败时回滚。交付布局为 `<release>/hwtui/` 与 `<release>/drivers/pmp4010/{pmp4010.py,od.json}`；不单独scp一个工具脚本，否则共享库和OD可能缺失。板端配置仍从 `/etc/km3/device.json`（后备 `/opt/km3/current/device.json`）读取，不由工具包替换。本轮未部署生产版本；临时源码验证与生产交付的边界见本轮验证记录。

位置目标会先读软件上下限，所有控制字写会先确认 CiA402 选择器，读取失败即拒绝。速度目标因手册定义的用户单位/s→rpm换算链尚未接通，当前明确返回 EInvalidConfig（包括通用SDO写）；不得把 counts_per_unit 当作该链已生效。字符串与607Dh:02h均在OD生成台账内，字符串上限256字节是主机容量策略。
