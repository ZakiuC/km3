# 恢复原自实现 Python 手动测试入口

Asia/Shanghai 2026-10-20，按用户“还是改回原来自己实现的”恢复入口选择。正式driver此前未迁移，本轮没有业务代码回滚；C++源码、dirty edits和版本目录全部保留。

已将中性稳定入口 `/opt/km3/hwtools/pmp4010-manual-test` 建为原Python版本目录 `/opt/km3/hwtools/manual-test-20261020-e137c603` 的符号链接；此前不存在。上轮自建兼容链接 `/opt/km3/hwtools/pmp4010-cpp-test` 经核实原指向488add26版本后，原子改指同一Python目录。因此上一条C++稳定启动命令也会进入原自实现。

推荐启动：

```sh
ssh -t root@192.168.0.188 '/opt/km3/hwtools/pmp4010-manual-test/run-manual.sh'
```

默认node1、can1（配置解析）、manual-linear、4mm/电机轴转，原速度默认1000U/s（审阅显示rpm），原软件零点、相对/绝对位置、速度、步长、使能/失能、2312受限复位、快停均保留。启动需按Enter确认连接与会话授权，无需手填OD理由：冻结simple.py第101–113行 authorize()自动记录理由，第684–703行确认页仅提示Enter。通用帮助里 --od-gaps-bypass 的理由参数属于其他通用子命令，手动启动脚本不要求它。正式OD状态未改，原其他保护未改。

现有程序进程不会因链接变化切换；如仍在旧C++界面，请正常退出后重新运行上面命令。本轮未终止任何进程、未停止/重启服务，未启动真实TUI、打开CAN、扫描SDO、使能、复位或运动。

原冻结包SHA256：`e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237`。本地归档hash确认；板端payload-manifest SHA256：`8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51`，27个清单项逐项hash/大小/符号链接核对匹配。launcher SHA256：`56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1`，两条稳定链接解析后相同。

验证：原load_smoke开启audit阻断socket/进程等I/O，20 imports、native33/Python33/int3214、缺库预期拒绝、forbidden_io_attempts=0、rc0；sh -n与Python直接 --help均rc0。旧launcher即使 --help也会新建logs目录，因此没有调用它，而沿用原部署记录的直接Python帮助入口；没有向旧版本目录写入日志/字节码。未改配置或current链接。

本轮只新增本证据目录和两个受控远端链接；旧版本不改，临时链接均已原子移入正式路径，无残留。无commit/push，不重跑源码大套件。原始冻结源码摘取及归档校验见[frozen-source-review.json](frozen-source-review.json)，完整可复核执行脚本见[restore.py](restore.py)。该脚本含已完成状态的严格前置断言，重复执行会在旧状态不符时停止；以下只读验证命令可单独复跑。

## before-and-manifest — rc=0

时间：2026-10-20T10:56:10.069291+08:00 至 2026-10-20T10:56:10.358909+08:00。 [argv/rc](before-and-manifest.command.json) · [stdout](before-and-manifest.stdout) · [stderr](before-and-manifest.stderr)

远端完整脚本：[before-and-manifest.remote.py](before-and-manifest.remote.py)。

stdout:

```text
payload_manifest=PASS entries=27
launcher_sha256=56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1
hostname=RK3588-Tronlong
architecture=aarch64
pmp4010-manual-test=ABSENT
pmp4010-cpp-test=/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26
```

stderr:

```text
(empty)
```

## arm-load-smoke — rc=0

时间：2026-10-20T10:56:10.359367+08:00 至 2026-10-20T10:56:10.944455+08:00。 [argv/rc](arm-load-smoke.command.json) · [stdout](arm-load-smoke.stdout) · [stderr](arm-load-smoke.stderr)

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectionAttempts=1 -o ConnectTimeout=8 root@192.168.0.188 'python3 -I -B /opt/km3/hwtools/manual-test-20261020-e137c603/load_smoke.py --check-missing-library'
```

stdout:

```text
payload_manifest=PASS source_head=7541b67edad414ef9b12999dd615f5ea228ed101
import=canopen_consts path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/canopen_consts.py
import=canopen_wire path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/canopen_wire.py
import=canopen path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/canopen.py
import=canbus path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/canbus.py
import=mockbus path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/mockbus.py
import=tui path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/tui.py
import=discover path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/discover.py
import=hwtui.canopen path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/canopen.py
import=hwtui.tui path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/tui.py
import=hwtui.discover path=/opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/discover.py
import=pmp4010_velocity path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_velocity.py
import=pmp4010_params path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_params.py
import=pmp4010_position path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_position.py
import=pmp4010_bench path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_bench.py
import=pmp4010_tui path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_tui.py
import=pmp4010 path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010.py
import=pmp4010_manual path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_manual.py
import=pmp4010_simple path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_simple.py
import=pmp4010_faults path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_faults.py
import=pmp4010_brake path=/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010_brake.py
{"library_realpath": "/opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/libpmp4010_velocity.so.1.0.0", "library_sha256": "59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404", "abi": 1, "machine": "aarch64", "python": "3.10.12", "mode": "packaged-library"}
vector=PASS manual_default_300rpm native_status=0 exact=300/1
vector=PASS manual_default_inverse native_status=0 exact=50000/1
vector=PASS measured_exact_46875_over_2048rpm native_status=0 exact=46875/2048
vector=PASS measured_fraction_inverse native_status=0 exact=50000/1
vector=PASS measured_rated_3000rpm native_status=0 exact=3000/1
vector=PASS measured_rated_inverse native_status=0 exact=6553600/1
vector=PASS zero_direction_0 native_status=0 exact=0/1
vector=PASS zero_direction_1 native_status=0 exact=0/1
vector=PASS gear3_forward native_status=0 exact=900/1
vector=PASS gear3_inverse native_status=0 exact=50000/1
vector=PASS signed_forward native_status=0 exact=-900/1
vector=PASS signed_inverse native_status=0 exact=-50000/1
vector=PASS negative_denominator_normalized native_status=0 exact=-300/1
vector=PASS fractional_write_rejected native_status=0 exact=500/3
vector=PASS positive_int32_overflow native_status=0 exact=2147483648/1
vector=PASS negative_int32_overflow native_status=0 exact=-2147483649/1
vector=PASS int32_minimum_inverse native_status=0 exact=-2147483648/1
vector=PASS int32_maximum_inverse native_status=0 exact=2147483647/1
vector=PASS wide_numerator native_status=0 exact=-2376844874321125483512594432000/1
vector=PASS wide_numerator_inverse native_status=0 exact=-2147483648/1
vector=PASS wide_denominator native_status=0 exact=-8589934592/1229782937674641135
vector=PASS wide_denominator_inverse native_status=0 exact=-2147483648/1
vector=PASS all_maximum native_status=0 exact=-128849018880/1
vector=PASS all_maximum_inverse native_status=0 exact=-2147483648/1
vector=PASS maximum128_cross_cancel native_status=0 exact=1/1
vector=PASS maximum128_identity native_status=0 exact=340282366920938463463374607431768211455/1
vector=PASS maximum128_negative native_status=0 exact=-340282366920938463463374607431768211455/1
vector=PASS parse128_overflow native_status=6
vector=PASS output128_overflow native_status=6
vector=PASS zero_calibration_0 native_status=2
vector=PASS zero_calibration_1 native_status=2
vector=PASS zero_calibration_2 native_status=2
vector=PASS zero_calibration_3 native_status=2
missing_library=EXPECTED_REJECTION 缺少本地速度换算库；请安装 libpmp4010_velocity.so，或设置 PMP4010_VELOCITY_LIBRARY 为绝对路径。已检查：/opt/km3/hwtools/manual-test-20261020-e137c603/__intentionally_missing_velocity__.so
{"result": "PASS", "native_vectors": 33, "python_vectors": 33, "native_int32_vectors": 14, "forbidden_io_attempts": 0, "hardware_acceptance": false}
```

stderr:

```text
(empty)
```

## launcher-syntax — rc=0

时间：2026-10-20T10:56:10.944562+08:00 至 2026-10-20T10:56:11.187758+08:00。 [argv/rc](launcher-syntax.command.json) · [stdout](launcher-syntax.stdout) · [stderr](launcher-syntax.stderr)

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectionAttempts=1 -o ConnectTimeout=8 root@192.168.0.188 'sh -n /opt/km3/hwtools/manual-test-20261020-e137c603/run-manual.sh'
```

stdout:

```text
(empty)
```

stderr:

```text
(empty)
```

## direct-help — rc=0

时间：2026-10-20T10:56:11.187897+08:00 至 2026-10-20T10:56:11.517646+08:00。 [argv/rc](direct-help.command.json) · [stdout](direct-help.stdout) · [stderr](direct-help.stderr)

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectionAttempts=1 -o ConnectTimeout=8 root@192.168.0.188 'python3 -I -B /opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010.py --help'
```

stdout:

```text
usage: pmp4010.py [-h] [--init] [--test] [--scan] [--nmt] [--od-dump]
                  [--motion {enable,disable,stop,home}] [--position]
                  [--manual] [--manual-linear]
                  [--manual-mm-per-rev MANUAL_MM_PER_REV]
                  [--manual-velocity MANUAL_VELOCITY] [--calibrate]
                  [--params {backup,interpolation,transmission,rollback,save}]
                  [--bus-stats] [--raw] [--dry-run] [--target TARGET]
                  [--velocity VELOCITY] [--acceleration ACCELERATION]
                  [--deceleration DECELERATION] [--soft-min SOFT_MIN]
                  [--soft-max SOFT_MAX] [--max-displacement MAX_DISPLACEMENT]
                  [--move-timeout MOVE_TIMEOUT] [--motion-log MOTION_LOG]
                  [--offline-calibration ENC_INC ENC_REV MOTOR_REV OUTPUT_REV]
                  [--offline-limits MAX_V MAX_RPM MAX_A MAX_D]
                  [--start-position START_POSITION] [--backup JSON] [--apply]
                  [--set INDEX:SUB=VALUE] [--confirm-save TEXT]
                  [--confirm-interpolation-readback] [--bus {can-2}]
                  [--timeout TIMEOUT] [--state-timeout STATE_TIMEOUT]
                  [--home-timeout HOME_TIMEOUT]
                  [--homing-method {17,18,19,20,21,22,23,24,25,26,27,28,29,30,35}]
                  [--verify] [--no-tui] [--i-know-rt-is-running]
                  [--od-gaps-bypass 理由]
                  [--nmt-command {start,stop,preop,reset-node,reset-comm}]
                  [--node NODE] [--node-range NODE_RANGE] [--identity]
                  [--rows ROWS] [--cols COLS] [--ascii]
                  [--listen-ms LISTEN_MS] [--count COUNT]
                  [--duration DURATION] [--show-raw] [--dump-json]

PMP4010（机身抬降电机 A）板载调试工具 —— curses TUI 与一次性子命令。
纯只读子命令任何身份可跑；--scan 发 SDO 上传请求；--nmt/--motion 是运动类；--params 参数维护同样受对象字典闸门限制。

options:
  -h, --help            show this help message and exit
  --init                三段式初始化：读配置与现状、写 /etc/km3/can.env，打印 root 命令
  --test                严格离线自检：配置、对象字典、共用协议 MockBus；不开真实总线
  --scan                主动 SDO 扫节点 1..127（只发上传请求，零写请求；受 RT 确认限制）
  --nmt                 发送 NMT 命令（发帧）
  --od-dump             打印对象字典声明与核对状态（只读）
  --motion {enable,disable,stop,home}
                        CiA402 使能/失能/快停/回零（发帧）；需 OD 全部核对与显式 --node
  --position            单次PP位置验收，默认离线，显式--apply才发帧
  --manual              单屏手动TUI：z软件零点、j相对/p绝对位置、f/b点动、Space/x快停；Enter确认单次动作
  --manual-linear       仅--
                        manual：直线机械测程，c设置mm/电机轴转；不设主机1度/会话行程限制，保留驱动限位与Int32保护
  --manual-mm-per-rev MANUAL_MM_PER_REV
                        仅--manual-linear：每电机轴转对应的直线mm，正有限数或分数；缺省须在TUI用c设置
  --manual-velocity MANUAL_VELOCITY
                        仅--manual：手动轮廓速度U/s，默认1000；审阅显示rpm并核设备上限
  --calibrate           读取608F/6091四项标定；--apply只读设备，默认离线
  --params {backup,interpolation,transmission,rollback,save}
                        参数备份/分批写/回滚/独立保存；默认离线dry-run，需OD闸门
  --bus-stats           接口现状、CAN 专用错误计数、kmod、实时进程（只读）
  --raw                 只监听原始帧（只读）
  --dry-run             --position/--calibrate显式离线干跑（也是默认值）
  --target TARGET       绝对目标位置，用户单位
  --velocity VELOCITY   轮廓速度，用户单位/s
  --acceleration ACCELERATION
                        轮廓加速度，用户单位/s²
  --deceleration DECELERATION
                        轮廓减速度，用户单位/s²
  --soft-min SOFT_MIN   脚本软行程下限，用户单位，必须人工给定
  --soft-max SOFT_MAX   脚本软行程上限，用户单位，必须人工给定
  --max-displacement MAX_DISPLACEMENT
                        人工单次位移上限，用户单位；另限1度电机轴
  --move-timeout MOVE_TIMEOUT
                        整次位置事务总体超时（含准备阶段），秒
  --motion-log MOTION_LOG
                        新建JSONL日志路径，不覆盖既有记录
  --offline-calibration ENC_INC ENC_REV MOTOR_REV OUTPUT_REV
                        仅离线：明确608F:1/2、6091:1/2，禁止猜测设备默认值
  --offline-limits MAX_V MAX_RPM MAX_A MAX_D
                        仅离线：明确607F/6080/60C5/60C6上限，模拟数据不代表本机
  --start-position START_POSITION
                        仅离线：明确模拟6064起点
  --backup JSON         --params backup的输出；其他参数操作的完整备份输入
  --apply               --params显式实际写入；省略时离线零发帧
  --set INDEX:SUB=VALUE
                        仅覆盖当前插补/传输类型批次，整批在发帧前按OD校验
  --confirm-save TEXT   独立save二次确认，从备份内容推导，见文档
  --confirm-interpolation-readback
                        人类确认插补批次已独立保存、下次上电并读回验证后，才可apply传输类型
  --bus {can-2}         逻辑总线；实际接口与位速率仅从 device.json 解析
  --timeout TIMEOUT     --scan/--motion 单次 SDO 应答超时秒数（默认 0.05，主机策略）
  --state-timeout STATE_TIMEOUT
                        --motion 每次状态迁移超时秒数（默认2，主机策略）
  --home-timeout HOME_TIMEOUT
                        --motion home 启动后完成期限秒数（默认10，主机策略）
  --homing-method {17,18,19,20,21,22,23,24,25,26,27,28,29,30,35}
                        --motion home
                        回零方法（默认17=负限位；35=当前位置置零）。须预先配置对应输入、速度、加速度；不支持任务示例中的方法1
  --verify, --read-only
                        只对 --init 有意义：纯只读，不修改任何文件
  --no-tui              不启用 curses 概览屏
  --i-know-rt-is-running
                        确认已知 km3-rt-controller.service 正在使用总线；发帧类子命令必需
  --od-gaps-bypass 理由   显式放行对象字典闸门，**必须给出一行理由**（会打进终端记录）。仅在 od.json 里仍有 status
                        != verified 的条目时有意义；不改变任何条目的可信度，也不改生成物。只对
                        --motion/--params/--position 生效，--nmt 不放行（NMT Start
                        会让总线上所有节点同时进入可运动状态）。使用前请先跑 --od-dump 看清放行的是哪几条。
  --nmt-command {start,stop,preop,reset-node,reset-comm}
                        --nmt 的命令（默认 start）
  --node NODE           目标节点号 0..127；NMT 省略或给 0 则为广播
  --node-range NODE_RANGE
                        --scan 的节点号范围，如 1-4 或 1。省略则扫 1..127
  --identity            --scan 时额外读设备类型、名称、软件版本、身份对象各子项
  --rows ROWS           TUI 行数（板端 stty size 返回 0 0 时的兜底，见 tui.resolve_size）
  --cols COLS           TUI 列数（同上）
  --ascii               TUI 用纯 ASCII 边框（CJK locale 下制表符号可能被判成双宽）
  --listen-ms LISTEN_MS
                        --raw 默认监听时长毫秒数（默认 2000；--count/--duration 优先）
  --count COUNT         --raw 收够多少帧后退出（0 = 不限）
  --duration DURATION   --raw 持续多少秒后退出（0 = 不限）
  --show-raw            --bus-stats 时额外打印 ip 命令的原始输出
  --dump-json           --od-dump 时额外打印完整 JSON

物理标识（内核接口名）只在 device/config/device.json 里定义，
本工具按逻辑总线名 can-2 解析，不接受命令行直接指定。

示例：
  python3 pmp4010.py --init --verify        # 纯只读，看现状与差异
  python3 pmp4010.py --init                 # 写 CAN_BITRATE 并打印 root 命令
  python3 pmp4010.py --bus-stats            # 接口 + CAN 错误计数 + kmod
  python3 pmp4010.py --od-dump              # 对象字典声明与核对状态
  python3 pmp4010.py --scan --node-range 1-4 --identity   # SDO 扫节点
  python3 pmp4010.py --raw --count 20       # 只监听，抓 20 帧
  python3 pmp4010.py --nmt --nmt-command start --node 1 --i-know-rt-is-running
  python3 pmp4010.py --motion stop --node 1 # 快速停机（需 OD 全部核对）

更稳的运行方式（自动接好共用外壳的 sys.path，与当前目录无关）：
  python3 /opt/km3/hwtools/manual-test-20261020-e137c603/hwtui/discover.py --run pmp4010/pmp4010.py --
```

stderr:

```text
(empty)
```

## restore-links — rc=0

时间：2026-10-20T10:56:11.518275+08:00 至 2026-10-20T10:56:11.811341+08:00。 [argv/rc](restore-links.command.json) · [stdout](restore-links.stdout) · [stderr](restore-links.stderr)

远端完整脚本：[restore-links.remote.py](restore-links.remote.py)。

stdout:

```text
payload_manifest=PASS entries=27
launcher_sha256=56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1
before /opt/km3/hwtools/pmp4010-manual-test=ABSENT
after /opt/km3/hwtools/pmp4010-manual-test=/opt/km3/hwtools/manual-test-20261020-e137c603
before /opt/km3/hwtools/pmp4010-cpp-test=/opt/km3/hwtools/pmp4010-cpp-test-20261020-488add26
after /opt/km3/hwtools/pmp4010-cpp-test=/opt/km3/hwtools/manual-test-20261020-e137c603
resolved_launcher_sha256=56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1
temporary_links_remaining=0
```

stderr:

```text
(empty)
```

## 文档闸门

新增本README后仅运行 bash tools/check-docs.sh，真实[argv/rc](docs-gate.command.json)、[stdout](docs-gate.stdout)、[stderr](docs-gate.stderr)保存于本目录。
