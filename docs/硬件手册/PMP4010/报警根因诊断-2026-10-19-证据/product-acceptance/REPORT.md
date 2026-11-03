# 未改动子系统本地验收（2026-10-19，美东时间）

本目录仅记录本地测试与增量构建；不代表硬件、制动、故障根因或运动验收。未访问 SSH、板端、真实 CAN 或部署。未修改生产代码、测试源码、OD 状态或提交。

## 基线与当前实测

| 套件 | 2026-10-17 交接初始基线 | 当前本轮实测 |
| --- | --- | --- |
| PMP4010 Python | 178 | 冻结后独立实测 435，rc=0 |
| tools/hwtui | 217 | 217，rc=0 |
| tools/tests | 8 | 8，rc=0 |
| Host CTest | 16/16 | 17/17，rc=0 |
| ASan CTest | 16/16 | 17/17，rc=0 |
| TSan CTest | 16/16 | 17/17，rc=0 |

主会话交付的本轮前基线为 hwtui 217、tools 8、C++ 各 17；与本次实测一致。历史数值是交接记录，并非本轮重跑。

三个 CTest 套件串行运行，未传并行参数；TSan 使用 `setarch -R`。20 个逻辑 CPU 使用 `-j8`。缓存实测 Host=none、ASan=address、TSan=thread；ARM64=Release/none，使用仓库 AArch64 工具链。未重新配置、清理构建目录或重复已通过的检查。

冻结后已独立执行 drivers、deps、manual-citations 三闸门与三个生成器：drivers/deps 与三生成器 rc=0，manual-citations rc=1（manual.py 五处缺相邻引用）。deps 虽 rc=0，仍提示 Python3 不在映射表中，需人工确认。docs 闸门等待最终报告更新后由报告 worker 单独执行，当前尚未验收；不得声明四闸门全通过。

92 个设备 C/C++/CMake 与 hwtui/tools-tests 文件在 Host CTest 启动后至 ARM64 构建结束间 SHA256 一致；见 `unchanged-source-check.json`，该快照不覆盖第一条命令开始前的时段。

## 精确命令、退出码和原始输出

### 01-hwtui

```sh
python3 -I -m unittest discover -s tools/hwtui -p 'test_*.py'
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:49:57.698718+00:00 → 2026-10-20T00:49:58.158637+00:00。

stdout（原始文件：`01-hwtui.stdout`）：
```text

```

stderr（原始文件：`01-hwtui.stderr`）：
```text
.........................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 217 tests in 0.408s

OK

```

### 02-tools

```sh
python3 -I -m unittest discover -s tools/tests -p 'test_*.py'
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:00.891281+00:00 → 2026-10-20T00:50:01.309056+00:00。

stdout（原始文件：`02-tools.stdout`）：
```text

```

stderr（原始文件：`02-tools.stderr`）：
```text
........
----------------------------------------------------------------------
Ran 8 tests in 0.391s

OK

```

### 03-host-build

```sh
cmake --build build-host -j8
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:05.103697+00:00 → 2026-10-20T00:50:05.203229+00:00。

stdout（原始文件：`03-host-build.stdout`）：
```text
Consolidate compiler generated dependencies of target km3_common
Consolidate compiler generated dependencies of target pmp4010_velocity
[  2%] Built target km3_common
[  7%] Built target pmp4010_velocity
Consolidate compiler generated dependencies of target test_pmp4010_velocity
Consolidate compiler generated dependencies of target test_error_code
Consolidate compiler generated dependencies of target km3_core
[ 11%] Built target test_pmp4010_velocity
Consolidate compiler generated dependencies of target test_pmp4010_verified
Consolidate compiler generated dependencies of target km3_pmp4010_mock
[ 14%] Built target test_error_code
[ 22%] Built target km3_core
[ 27%] Built target test_pmp4010_verified
[ 36%] Built target km3_pmp4010_mock
Consolidate compiler generated dependencies of target km3_link
Consolidate compiler generated dependencies of target km3-video
Consolidate compiler generated dependencies of target test_spsc_ring
Consolidate compiler generated dependencies of target km3_config
Consolidate compiler generated dependencies of target km3_proto
Consolidate compiler generated dependencies of target km3_hal
[ 39%] Built target km3_link
[ 42%] Built target km3-video
[ 45%] Built target test_spsc_ring
[ 48%] Built target km3_config
Consolidate compiler generated dependencies of target test_pmp4010
[ 54%] Built target km3_hal
Consolidate compiler generated dependencies of target test_clock
[ 58%] Built target km3_proto
Consolidate compiler generated dependencies of target test_frame
Consolidate compiler generated dependencies of target test_config
[ 63%] Built target test_clock
[ 64%] Built target test_pmp4010
Consolidate compiler generated dependencies of target test_hal
Consolidate compiler generated dependencies of target km3_canopen
Consolidate compiler generated dependencies of target test_socketcan
[ 67%] Built target test_config
Consolidate compiler generated dependencies of target km3_rt_control
[ 70%] Built target test_frame
[ 73%] Built target test_hal
[ 77%] Built target km3_canopen
[ 80%] Built target test_socketcan
Consolidate compiler generated dependencies of target km3-core
[ 85%] Built target km3_rt_control
Consolidate compiler generated dependencies of target test_sync
Consolidate compiler generated dependencies of target test_pdo
[ 88%] Built target km3-core
Consolidate compiler generated dependencies of target km3-rt-controller
Consolidate compiler generated dependencies of target test_rt_control
[ 91%] Built target test_sync
[ 94%] Built target test_pdo
[ 97%] Built target test_rt_control
[100%] Built target km3-rt-controller

```

stderr（原始文件：`03-host-build.stderr`）：
```text

```

### 04-host-ctest

```sh
ctest --test-dir build-host --output-on-failure
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:08.835905+00:00 → 2026-10-20T00:50:20.396970+00:00。

stdout（原始文件：`04-host-ctest.stdout`）：
```text
Internal ctest changing into directory: /home/zakiu/km3/build-host
Test project /home/zakiu/km3/build-host
      Start  1: spsc_ring
 1/17 Test  #1: spsc_ring ..........................   Passed    0.02 sec
      Start  2: error_code
 2/17 Test  #2: error_code .........................   Passed    0.00 sec
      Start  3: frame
 3/17 Test  #3: frame ..............................   Passed    0.00 sec
      Start  4: config
 4/17 Test  #4: config .............................   Passed    0.01 sec
      Start  5: hal
 5/17 Test  #5: hal ................................   Passed    0.00 sec
      Start  6: socketcan
 6/17 Test  #6: socketcan ..........................   Passed    0.00 sec
      Start  7: canopen_pdo
 7/17 Test  #7: canopen_pdo ........................   Passed    0.04 sec
      Start  8: canopen_sync
 8/17 Test  #8: canopen_sync .......................   Passed    0.32 sec
      Start  9: sync_config_cli
 9/17 Test  #9: sync_config_cli ....................   Passed   10.31 sec
      Start 10: clock
10/17 Test #10: clock ..............................   Passed    0.06 sec
      Start 11: rt_control
11/17 Test #11: rt_control .........................   Passed    0.00 sec
      Start 12: pmp4010_mock_contract
12/17 Test #12: pmp4010_mock_contract ..............   Passed    0.01 sec
      Start 13: pmp4010_production_od_guard
13/17 Test #13: pmp4010_production_od_guard ........   Passed    0.52 sec
      Start 14: pmp4010_verified_fixture
14/17 Test #14: pmp4010_verified_fixture ...........   Passed    0.21 sec
      Start 15: pmp4010_velocity_shared_vectors
15/17 Test #15: pmp4010_velocity_shared_vectors ....   Passed    0.00 sec
      Start 16: pmp4010_protocol_generated_check
16/17 Test #16: pmp4010_protocol_generated_check ...   Passed    0.02 sec
      Start 17: pmp4010_access_generated_check
17/17 Test #17: pmp4010_access_generated_check .....   Passed    0.02 sec

100% tests passed, 0 tests failed out of 17

Label Time Summary:
mock       =   0.78 sec*proc (5 tests)
pmp4010    =   0.78 sec*proc (5 tests)

Total Test time (real) =  11.55 sec

```

stderr（原始文件：`04-host-ctest.stderr`）：
```text

```

### 05-asan-build

```sh
cmake --build build-asan -j8
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:22.964109+00:00 → 2026-10-20T00:50:23.085120+00:00。

stdout（原始文件：`05-asan-build.stdout`）：
```text
Consolidate compiler generated dependencies of target km3_common
Consolidate compiler generated dependencies of target pmp4010_velocity
[  2%] Built target km3_common
[  7%] Built target pmp4010_velocity
Consolidate compiler generated dependencies of target km3_core
Consolidate compiler generated dependencies of target test_error_code
Consolidate compiler generated dependencies of target km3_pmp4010_mock
Consolidate compiler generated dependencies of target test_pmp4010_velocity
[ 14%] Built target km3_core
Consolidate compiler generated dependencies of target test_pmp4010_verified
[ 17%] Built target test_error_code
Consolidate compiler generated dependencies of target km3_link
[ 30%] Built target test_pmp4010_velocity
[ 30%] Built target km3_pmp4010_mock
Consolidate compiler generated dependencies of target km3_proto
Consolidate compiler generated dependencies of target km3_config
Consolidate compiler generated dependencies of target km3_hal
[ 36%] Built target test_pmp4010_verified
Consolidate compiler generated dependencies of target km3-video
[ 39%] Built target km3_link
Consolidate compiler generated dependencies of target test_clock
[ 44%] Built target km3_proto
Consolidate compiler generated dependencies of target test_spsc_ring
[ 47%] Built target km3_config
[ 52%] Built target km3_hal
Consolidate compiler generated dependencies of target test_pmp4010
[ 55%] Built target km3-video
[ 58%] Built target test_clock
Consolidate compiler generated dependencies of target test_frame
[ 61%] Built target test_spsc_ring
Consolidate compiler generated dependencies of target km3-core
[ 64%] Built target test_pmp4010
Consolidate compiler generated dependencies of target test_config
Consolidate compiler generated dependencies of target km3_canopen
[ 67%] Built target test_frame
[ 70%] Built target km3-core
[ 73%] Built target test_config
Consolidate compiler generated dependencies of target test_hal
Consolidate compiler generated dependencies of target km3_rt_control
Consolidate compiler generated dependencies of target test_socketcan
[ 77%] Built target km3_canopen
[ 82%] Built target km3_rt_control
[ 85%] Built target test_hal
[ 88%] Built target test_socketcan
Consolidate compiler generated dependencies of target test_sync
Consolidate compiler generated dependencies of target test_pdo
Consolidate compiler generated dependencies of target km3-rt-controller
Consolidate compiler generated dependencies of target test_rt_control
[ 91%] Built target test_pdo
[ 94%] Built target test_sync
[ 97%] Built target km3-rt-controller
[100%] Built target test_rt_control

```

stderr（原始文件：`05-asan-build.stderr`）：
```text

```

### 06-asan-ctest

```sh
ctest --test-dir build-asan --output-on-failure
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:30.346134+00:00 → 2026-10-20T00:50:42.476711+00:00。

stdout（原始文件：`06-asan-ctest.stdout`）：
```text
Internal ctest changing into directory: /home/zakiu/km3/build-asan
Test project /home/zakiu/km3/build-asan
      Start  1: spsc_ring
 1/17 Test  #1: spsc_ring ..........................   Passed    0.03 sec
      Start  2: error_code
 2/17 Test  #2: error_code .........................   Passed    0.01 sec
      Start  3: frame
 3/17 Test  #3: frame ..............................   Passed    0.01 sec
      Start  4: config
 4/17 Test  #4: config .............................   Passed    0.04 sec
      Start  5: hal
 5/17 Test  #5: hal ................................   Passed    0.01 sec
      Start  6: socketcan
 6/17 Test  #6: socketcan ..........................   Passed    0.01 sec
      Start  7: canopen_pdo
 7/17 Test  #7: canopen_pdo ........................   Passed    0.06 sec
      Start  8: canopen_sync
 8/17 Test  #8: canopen_sync .......................   Passed    0.33 sec
      Start  9: sync_config_cli
 9/17 Test  #9: sync_config_cli ....................   Passed   10.74 sec
      Start 10: clock
10/17 Test #10: clock ..............................   Passed    0.08 sec
      Start 11: rt_control
11/17 Test #11: rt_control .........................   Passed    0.02 sec
      Start 12: pmp4010_mock_contract
12/17 Test #12: pmp4010_mock_contract ..............   Passed    0.01 sec
      Start 13: pmp4010_production_od_guard
13/17 Test #13: pmp4010_production_od_guard ........   Passed    0.52 sec
      Start 14: pmp4010_verified_fixture
14/17 Test #14: pmp4010_verified_fixture ...........   Passed    0.23 sec
      Start 15: pmp4010_velocity_shared_vectors
15/17 Test #15: pmp4010_velocity_shared_vectors ....   Passed    0.01 sec
      Start 16: pmp4010_protocol_generated_check
16/17 Test #16: pmp4010_protocol_generated_check ...   Passed    0.02 sec
      Start 17: pmp4010_access_generated_check
17/17 Test #17: pmp4010_access_generated_check .....   Passed    0.02 sec

100% tests passed, 0 tests failed out of 17

Label Time Summary:
mock       =   0.80 sec*proc (5 tests)
pmp4010    =   0.80 sec*proc (5 tests)

Total Test time (real) =  12.12 sec

```

stderr（原始文件：`06-asan-ctest.stderr`）：
```text

```

### 07-tsan-build

```sh
cmake --build build-tsan -j8
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:50.366411+00:00 → 2026-10-20T00:50:50.466754+00:00。

stdout（原始文件：`07-tsan-build.stdout`）：
```text
Consolidate compiler generated dependencies of target km3_common
Consolidate compiler generated dependencies of target pmp4010_velocity
[  2%] Built target km3_common
[  7%] Built target pmp4010_velocity
Consolidate compiler generated dependencies of target test_error_code
Consolidate compiler generated dependencies of target test_pmp4010_velocity
Consolidate compiler generated dependencies of target km3_core
Consolidate compiler generated dependencies of target test_pmp4010_verified
[ 11%] Built target test_pmp4010_velocity
[ 14%] Built target test_error_code
Consolidate compiler generated dependencies of target km3_pmp4010_mock
[ 22%] Built target km3_core
[ 27%] Built target test_pmp4010_verified
Consolidate compiler generated dependencies of target km3_link
Consolidate compiler generated dependencies of target km3-video
[ 36%] Built target km3_pmp4010_mock
Consolidate compiler generated dependencies of target test_spsc_ring
Consolidate compiler generated dependencies of target km3_hal
[ 45%] Built target test_spsc_ring
[ 45%] Built target km3_link
[ 45%] Built target km3-video
Consolidate compiler generated dependencies of target km3_proto
Consolidate compiler generated dependencies of target km3_config
Consolidate compiler generated dependencies of target test_clock
[ 51%] Built target km3_hal
Consolidate compiler generated dependencies of target test_pmp4010
[ 57%] Built target km3_config
[ 57%] Built target test_clock
[ 61%] Built target km3_proto
Consolidate compiler generated dependencies of target test_frame
Consolidate compiler generated dependencies of target km3_rt_control
Consolidate compiler generated dependencies of target km3_canopen
[ 64%] Built target test_pmp4010
Consolidate compiler generated dependencies of target test_config
Consolidate compiler generated dependencies of target test_hal
Consolidate compiler generated dependencies of target km3-core
Consolidate compiler generated dependencies of target test_socketcan
[ 69%] Built target km3_canopen
[ 72%] Built target test_frame
[ 75%] Built target test_config
[ 79%] Built target km3_rt_control
[ 82%] Built target km3-core
[ 85%] Built target test_hal
Consolidate compiler generated dependencies of target test_pdo
Consolidate compiler generated dependencies of target km3-rt-controller
Consolidate compiler generated dependencies of target test_rt_control
[ 88%] Built target test_socketcan
Consolidate compiler generated dependencies of target test_sync
[ 91%] Built target test_rt_control
[ 94%] Built target km3-rt-controller
[ 97%] Built target test_pdo
[100%] Built target test_sync

```

stderr（原始文件：`07-tsan-build.stderr`）：
```text

```

### 08-tsan-ctest

```sh
setarch -R ctest --test-dir build-tsan --output-on-failure
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:50:53.395521+00:00 → 2026-10-20T00:51:05.651304+00:00。

stdout（原始文件：`08-tsan-ctest.stdout`）：
```text
Internal ctest changing into directory: /home/zakiu/km3/build-tsan
Test project /home/zakiu/km3/build-tsan
      Start  1: spsc_ring
 1/17 Test  #1: spsc_ring ..........................   Passed    0.16 sec
      Start  2: error_code
 2/17 Test  #2: error_code .........................   Passed    0.01 sec
      Start  3: frame
 3/17 Test  #3: frame ..............................   Passed    0.01 sec
      Start  4: config
 4/17 Test  #4: config .............................   Passed    0.04 sec
      Start  5: hal
 5/17 Test  #5: hal ................................   Passed    0.01 sec
      Start  6: socketcan
 6/17 Test  #6: socketcan ..........................   Passed    0.01 sec
      Start  7: canopen_pdo
 7/17 Test  #7: canopen_pdo ........................   Passed    0.05 sec
      Start  8: canopen_sync
 8/17 Test  #8: canopen_sync .......................   Passed    0.33 sec
      Start  9: sync_config_cli
 9/17 Test  #9: sync_config_cli ....................   Passed   10.61 sec
      Start 10: clock
10/17 Test #10: clock ..............................   Passed    0.08 sec
      Start 11: rt_control
11/17 Test #11: rt_control .........................   Passed    0.01 sec
      Start 12: pmp4010_mock_contract
12/17 Test #12: pmp4010_mock_contract ..............   Passed    0.01 sec
      Start 13: pmp4010_production_od_guard
13/17 Test #13: pmp4010_production_od_guard ........   Passed    0.58 sec
      Start 14: pmp4010_verified_fixture
14/17 Test #14: pmp4010_verified_fixture ...........   Passed    0.30 sec
      Start 15: pmp4010_velocity_shared_vectors
15/17 Test #15: pmp4010_velocity_shared_vectors ....   Passed    0.01 sec
      Start 16: pmp4010_protocol_generated_check
16/17 Test #16: pmp4010_protocol_generated_check ...   Passed    0.02 sec
      Start 17: pmp4010_access_generated_check
17/17 Test #17: pmp4010_access_generated_check .....   Passed    0.02 sec

100% tests passed, 0 tests failed out of 17

Label Time Summary:
mock       =   0.94 sec*proc (5 tests)
pmp4010    =   0.94 sec*proc (5 tests)

Total Test time (real) =  12.25 sec

```

stderr（原始文件：`08-tsan-ctest.stderr`）：
```text

```

### 09-arm64-build

```sh
cmake --build build-arm64 -j8
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:51:08.828462+00:00 → 2026-10-20T00:51:08.952952+00:00。

stdout（原始文件：`09-arm64-build.stdout`）：
```text
[  3%] Built target km3_common
[  7%] Built target pmp4010_velocity
[ 12%] Built target test_pmp4010_velocity
[ 20%] Built target km3_core
[ 29%] Built target test_pmp4010_verified
[ 27%] Built target test_error_code
[ 38%] Built target km3_pmp4010_mock
[ 41%] Built target test_spsc_ring
[ 44%] Built target km3_link
[ 47%] Built target km3-video
[ 50%] Built target km3_config
[ 53%] Built target test_clock
[ 56%] Built target test_pmp4010
[ 60%] Built target km3-core
[ 66%] Built target km3_hal
[ 69%] Built target test_frame
[ 72%] Built target test_config
[ 75%] Built target test_socketcan
[ 80%] Built target km3_rt_control
[ 83%] Built target test_hal
[ 87%] Built target km3_canopen
[ 90%] Built target test_rt_control
[ 93%] Built target km3-rt-controller
[ 96%] Built target test_pdo
[100%] Built target test_sync

```

stderr（原始文件：`09-arm64-build.stderr`）：
```text

```

## 冻结后独立验收

四源码及92个稳定源文件前后核对结果见 `freeze-before-final-checks.json` 和 `freeze-after-final-checks.json`。当前冻结后核对：{"frozen": {"files": 4, "changed": []}, "stable": {"files": 92, "changed": []}}。

源代码 Python 全套通过；手册溯源闸门失败，已报告主会话等待修复。历史失败输出保留，不进行无改动重跑。

### 10-pmp4010-python

```sh
python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py'
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:56:08.864891+00:00 → 2026-10-20T00:56:51.630154+00:00。

stdout（原始文件：`10-pmp4010-python.stdout`）：
```text
BENCH nonblocking: help key + rendered screen 0.005717s; worker still blocked past SDO timeout; quarantine visible
BENCH cancellation: disabled_confirmed=true; completed=false; cleanup ignores transaction cancellation
BENCH enable postflight: out-of-envelope readback causes same-client quick-stop + disable
BENCH enable postflight timeout: disabled_confirmed=false; state_unknown=true; replacement clients=0
BENCH lost write ACK: same quarantined client; disabled_confirmed=false; independent_estop_required=true
BENCH quarantine: timeout latched; rescan/refresh/action cannot recreate client or send another request
BENCH relative: -1464 + 50 = absolute -1414; nonzero actual velocity rejected before write
BENCH scan: four slots; node 1 online; three unassigned slots offline; SDO uploads only; NMT=0
BENCH setup race: start drift after mode write; target 607A never downloaded; cleanup confirmed
BENCH zero: 6098:00=35; controlword edge 0x000F -> 0x001F; 607C writes=0; cleanup [0x0002,0x0000]
DISABLED MOVE Mock: current fresh ACK then 12F/Halt stable5, release2F, final607A freshACK/bit10/actual/demand/zero, cleanup2/0
HALT probe Mock: initial -1470/607A=6062=7800; align once; 106/107/127/137/127 (bit3=0,Halt=1); fresh ACK and demand; 5+5 samples; 2/0 cleanup; no travel
DIAGNOSTIC running603F=3456: exact5 uploads then mode/CW2/status/mode/CW0/status; no extra deviceIO; original historical values preserved
MANUAL submit: bit10=0, final human target -1420 only, fresh ACK0/1/0, CW106/107/127/137/127, no enable/release
DELAYED actual SDO: 0106 ACK then0260/0260/0231; 0107 ACK then0231/0231/0233; fixed607A=-1434; submit completes
DELAYED unexpected0237 then0231: first raw immediately rejected, no read of later normal, cleanup cannot replace root sample
DELAYED pending guard injections: first velocity/position/calibration/profile/607C/settings each prevents next CW and cleans2/0
DELAYED persistent0260: state_timeout bounded; no0107/enable/release; first_failure and cleanup2/0 retained
DELAYED waiting cancel: immediate cancellation at pending snapshot, same-client bounded2/0, no later CW
MANUAL E: CW12F only, +13U accepted inside original start/budget; no zero-drift cleanup, waits for G
ENABLED EDGE ACK read timeout after003F: original client quarantined; no cleanup/refresh/scan/exit requests
ENABLED EDGE behavior: before=-1457, after=-1445, target=-1420, edges=[(47, 63, -1420)], CW=['0x106', '0x107', '0x127', '0x137', '0x127', '0x12f', '0x2f', '0x3f', '0x2f']
ENABLED EDGE actual SDO: ACK samples 0/0→1/1→0/0; moving during high/low handshake; fixed target reached, one edge only
ENABLED EDGE missing high/stuck low ACK: fixed deadline; same-client bounded2/0; proof consumed and node blocked
ENABLED EDGE moving phase: mode/CW/607A/6062/calibration/profile/607C/607D/fault/envelope changes stop2/disable0 before final release
ENABLED TARGET malformed readback width: same-client quarantine and zero following requests
ENABLED TARGET lost download ACK: one attempted target download; same-client quarantine, zero subsequent requests/edges
ENABLED TARGET readback mismatch: same-client bounded2/0; no new edge
ENABLED TARGET hypothesis (not firmware fact): before=-1457, after=-1445, frozen=-1420, enabled writes=[-1420], edges=[(47, 63, -1420)]
MANUAL user sequence CW: 0106 0107 0127 0137 0127 | 012F | 002F 003F 002F | 0002 | 0000 | exit0006
DIAGNOSTIC initial603F=ABCD: exact identity+5 uploads; CW unknown; partial persisted before enable; target lock retained, client communication healthy
DIAGNOSTIC previous complete snapshot then next first6041 timeout: all current raw fields unknown; previous CW kept only as expected context
MANUAL timeout: same client quarantined; scan/refresh/G/exit zero subsequent requests; independent emergency stop required
{"passed": 27, "checks": ["rt", "overview", "startup/RT confirmation/scan have zero controlword writes", "detail", "setting", "setting", "setting", "three settings submitted by Form", "od", "action", "confirm", "A submits final target only", "A stops at submitted, no enable or release", "action", "confirm", "E holds Halt, +13U allowed", "background monitor preserves +13U envelope", "action", "confirm", "G triggers fresh enabled ACK0/1/0", "action", "confirm", "X explicitly software quick stops", "action", "confirm", "D explicitly disables", "exit Shutdown0006 after operator D, exact final controlword sequence"], "controls": [262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 6], "rc": 0, "transport": "standard MockBus only; real socket creation forbidden"}
B explicit backup mock frames=16 SDO uploads; downloads=0; objects=12 identity=4
B out-of-range/late-invalid-plan mock frame_count=0
B dry-run mock frame_count=0; bus_open_count=0; link_query_count=0
C invalid parameter/soft travel/small displacement checks: zero real frames
C fast move: fresh ACK 0->1->0 + target actual position; all sampled target-reached bits=1 accepted
C lost enable ACK: SDO quarantined; stop and disable attempted, unconfirmed; independent E-stop required
C offline default/explicit dry-run: real_frame_count=0 real_bus_open_count=0
C preview matches independent SDO peer: 67/67 complete nominal request frames
PREPARE UI: P -> F5 freeze -> exact token -> F5 background execution; two downloads only
PREPARE MockBus: 5 stable samples >=0.8s; 607A=start,6081=100 only; bit10=0 retained; exit controls=0
PREPARE lost ACK: first download attempted once; quarantine sticky; next write/cleanup/exit/retry=0
PREPARE independent literal SDO peer: complete 319 request frames match MockBus; controlword=0
RESET mutation matrix: 92 SDO boundaries x lost/malformed/cancel; no next request/new client/cleanup
SIMPLE busy x: real delayed SDO worker cancelled; pending cleared; one quickstop2, no target/enable/release replay
SIMPLE delayed SDO full f action: fixed start/target; write ACK/raw snapshots identify actual0106..012F..002F/003F/002F phases
SIMPLE e: frozen current607A/new ACK/6062 then12F Halt, never2F; +13U inside original range accepted
SIMPLE Ready0231/SwitchedOn0233: confirmed internal disable0 then sealed submission; externalEnabled0237 rejected
SIMPLE locked80x24: last-valid telemetry explicitly historical; current state unknown; firstCW0106/raw0237 remains visible
SIMPLE one confirmation: only Operation enabled new edge starts trajectory; original target reached; consumed review never replays
SIMPLE forward: one immutable confirmation; CW106/107/127/137/127/12F/2F/3F/2F; +13U allowed; fixed start/target
MANUAL production default: 1000U/s -> live608F/6091 0.457763671875rpm; explicit100 kept; step0.1deg unchanged
SIMPLE refresh Space: control/status uploads only then cancellation; no remaining snapshot reads before quickstop2
SIMPLE continuous f/b: zero speed permits next direction; draft s/a keep old proof; confirmed2/0 precede new6081/607A
{"passed": 34, "checks": ["rt", "authorize", "connection/RT consent/scan upload only, default node1", "main", "edit", "speed normal numeric Enter commit", "edit", "step normal numeric Enter commit", "confirm", "f confirmed", "f actual feedback reached frozen target", "f Enter real SDO enabled bit4 edge/newACK0/1/0 and progressive feedback reached target, no token/F5", "confirm", "b confirmed", "b actual feedback reached frozen target", "instant stop 'x'", "continuous f then b without manual Space/d; internal2/0 before resubmission", "confirm", "instant stop 'x'", "x in confirmation discards action; Enter cannot revive", "confirm", "d Enter disables", "edit", "draft entered", "instant stop ' '", "help", "instant stop 'x'", "Space in edit and x in help reach actual quick-stop writes", "confirm", "second d", "confirm", "e confirmed", "e explicit frozen current target, fresh ACK, Halt12F no release", "actual SDO delays:0106 ACK then0260/0260/0231;0107 ACK then0231/0231/0233; full f Enter succeeds"], "controls": [262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 2, 0, 2, 2, 0, 262, 263, 295, 311, 295, 303, 6], "rc": 0, "terminal": "80x24", "transport": "standard MockBus only; real socket creation forbidden"}
{"passed": 41, "checks": ["rt", "authorize", "connection/RT consent/scan upload only, default node1", "main", "unset coefficient refuses f", "unset coefficient software zero capture", "edit", "c positive exact mm/rev conversion", "edit", "confirm", "r +1.2 frozen607A reached", "r +1.2 confirms exactly quantified raw target -1034", "z under running reached proof", "z fresh reached sample: zero downloads, same raw physical proof and drive envelope", "edit", "confirm", "j -.2 frozen607A reached", "j -.2 confirms exactly quantified raw target -1106", "edit", "confirm", "p +.1 frozen607A reached", "p +.1 confirms exactly quantified raw target -998", "edit", "confirm", "p -.1 frozen607A reached", "p -.1 confirms exactly quantified raw target -1070", "edit", "confirm", "p 0 frozen607A reached", "p 0 confirms exactly quantified raw target -1034", "edit", "drive boundary refuses numeric review", "instant stop ' '", "out-of-range relative zero motion downloads; Space stops during numeric entry", "edit", "confirm", "instant stop 'x'", "x during position review discards target; Enter cannot replay", "help", "instant stop 'x'", "all five moves preserve enabled target rewrite/new ACK0/1/0; no home/offset/save; exit cleanup6"], "controls": [262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 0, 262, 263, 295, 311, 295, 303, 47, 63, 47, 2, 2, 2, 6], "rc": 0, "terminal": "80x24", "transport": "standard MockBus only; real socket creation forbidden", "mode": "--manual --manual-linear", "coefficient": "360 mock mm/motor revolution; not machine calibration"}
Python inverse samples: 36 signed Int32 write roundtrips + 16 rational rpm roundtrips
Python shared vector manual_default_300rpm: 50000 -> 300 exact
Python shared vector manual_default_inverse: 300 -> 50000 exact
Python shared vector measured_exact_46875_over_2048rpm: 50000 -> 46875/2048 exact
Python shared vector measured_fraction_inverse: 46875/2048 -> 50000 exact
Python shared vector measured_rated_3000rpm: 6553600 -> 3000 exact
Python shared vector measured_rated_inverse: 3000 -> 6553600 exact
Python shared vector zero_direction_0: 0 -> 0 exact
Python shared vector zero_direction_1: 0 -> 0 exact
Python shared vector gear3_forward: 50000 -> 900 exact
Python shared vector gear3_inverse: 900 -> 50000 exact
Python shared vector signed_forward: -50000 -> -900 exact
Python shared vector signed_inverse: -900 -> -50000 exact
Python shared vector negative_denominator_normalized: -50000 -> -300 exact
Python shared vector fractional_write_rejected: 1 -> 500/3 exact
Python shared vector positive_int32_overflow: 2147483648 -> 2147483648 exact
Python shared vector negative_int32_overflow: -2147483649 -> -2147483649 exact
Python shared vector int32_minimum_inverse: -2147483648 -> -2147483648 exact
Python shared vector int32_maximum_inverse: 2147483647 -> 2147483647 exact
Python shared vector wide_numerator: -2147483648 -> -2376844874321125483512594432000 exact
Python shared vector wide_numerator_inverse: -2376844874321125483512594432000 -> -2147483648 exact
Python shared vector wide_denominator: -2147483648 -> -8589934592/1229782937674641135 exact
Python shared vector wide_denominator_inverse: -8589934592/1229782937674641135 -> -2147483648 exact
Python shared vector all_maximum: -2147483648 -> -128849018880 exact
Python shared vector all_maximum_inverse: -128849018880 -> -2147483648 exact
Python shared vector maximum128_cross_cancel: 1 -> 1 exact
Python shared vector maximum128_identity: 340282366920938463463374607431768211455 -> 340282366920938463463374607431768211455 exact
Python shared vector maximum128_negative: -340282366920938463463374607431768211455 -> -340282366920938463463374607431768211455 exact
Python shared vector parse128_overflow: rejected (expected error=6)
Python shared vector output128_overflow: rejected (expected error=6)
Python shared vector zero_calibration_0: rejected (expected error=2)
Python shared vector zero_calibration_1: rejected (expected error=2)
Python shared vector zero_calibration_2: rejected (expected error=2)
Python shared vector zero_calibration_3: rejected (expected error=2)
Python PV/CSV: explicit 60FFh=0 write/readback precedes 6/7/0F; credential consumed once

```

stderr（原始文件：`10-pmp4010-python.stderr`）：
```text
...................................................................................................................................................................................................................................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 435 tests in 42.695s

OK

```

### 11-check-drivers

```sh
bash tools/check-drivers.sh
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:56:55.156398+00:00 → 2026-10-20T00:56:55.184819+00:00。

stdout（原始文件：`11-check-drivers.stdout`）：
```text
==> 驱动解耦检查（device/drivers/，1 个驱动）
  (d) 五件套：检查 1 个驱动
  (a) 链接白名单：检查 1 个驱动的 CMakeLists，白名单 = km3_core km3_hal km3_canopen km3_driver_api
  (b) 驱动互引：检查 1 个驱动之间的两两引用
  (c) 反向完整性：检查 1 个驱动是否都能从 device/CMakeLists.txt 到达
  (e) 物理标识：扫描 device/drivers 全树（含 _template）

结果：1 个驱动，五条断言全部通过

```

stderr（原始文件：`11-check-drivers.stderr`）：
```text

```

### 12-check-deps

```sh
bash tools/check-deps.sh
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:56:58.868358+00:00 → 2026-10-20T00:56:58.885744+00:00。

stdout（原始文件：`12-check-deps.stdout`）：
```text
  install-deps.sh 提供 26 个包
  CMake 共需要 12 个依赖

    Boost            OK  (libboost-all-dev)
    OpenSSL          OK  (libssl-dev)
    PostgreSQL       OK  (libpq-dev)
    Protobuf         OK  (libprotobuf-dev)
    Python3          ??  未在映射表中
    SQLite3          OK  (libsqlite3-dev)
    Threads          OK  (build-essential)
    ZeroMQ           OK  (libzmq3-dev)
    fmt              OK  (libfmt-dev)
    libpqxx          OK  (libpqxx-dev)
    nlohmann_json    OK  (nlohmann-json3-dev)
    spdlog           OK  (libspdlog-dev)

结果：全部已知依赖已覆盖；1 个需人工确认：Python3

```

stderr（原始文件：`12-check-deps.stderr`）：
```text

```

### 13-check-manual-citations

```sh
bash tools/check-manual-citations.sh
```

cwd: `/home/zakiu/km3`；rc=1；UTC 2026-10-20T00:57:02.612260+00:00 → 2026-10-20T00:57:02.726431+00:00。

stdout（原始文件：`13-check-manual-citations.stdout`）：
```text
==> 手册溯源检查
  (1)(2) od.json：1 个驱动、97 个条目、90 条已核对引用
  [违规] device/drivers/pmp4010/tools/pmp4010_manual.py:166 出现对象字典索引字面量但同行（或紧邻上一行）无引用：if key == and value and not ( allow_alarm and value == 0x2312 ) :
  [违规] device/drivers/pmp4010/tools/pmp4010_manual.py:169 出现对象字典索引字面量但同行（或紧邻上一行）无引用：exc . manual_alarm_2312 = ( value == 0x2312 and bool ( sw & 0x80 )
  [违规] device/drivers/pmp4010/tools/pmp4010_manual.py:483 出现对象字典索引字面量但同行（或紧邻上一行）无引用：or v [ ] not in ( 0 , 0x2312 ) ) :
  [违规] device/drivers/pmp4010/tools/pmp4010_manual.py:486 出现对象字典索引字面量但同行（或紧邻上一行）无引用：v [ ] = raw ( 0x200e , 2 , 4 )
  [违规] device/drivers/pmp4010/tools/pmp4010_manual.py:488 出现对象字典索引字面量但同行（或紧邻上一行）无引用：if not sw & 0x80 or v [ ] != 0x2312 or v [ ] != 0x03002312 or v [ ] !=
  (3) 源码索引字面量：检查 59 处，全部同行带引用

结果：发现违规（见上 [违规] 行）

```

stderr（原始文件：`13-check-manual-citations.stderr`）：
```text

```

### 14-gen-od

```sh
python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:57:13.312739+00:00 → 2026-10-20T00:57:13.331580+00:00。

stdout（原始文件：`14-gen-od.stdout`）：
```text
==> 校验 pmp4010 的两份生成物（--check）
  一致   /home/zakiu/km3/device/drivers/pmp4010/driver/od_generated.hpp
  一致   /home/zakiu/km3/tools/hwtui/canopen_consts.py
结果：两份生成物都与 od.json 一致

```

stderr（原始文件：`14-gen-od.stderr`）：
```text

```

### 15-gen-canopen

```sh
python3 -I tools/hwdriver/gen-canopen.py --check
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:57:16.375483+00:00 → 2026-10-20T00:57:16.394923+00:00。

stdout（原始文件：`15-gen-canopen.stdout`）：
```text

```

stderr（原始文件：`15-gen-canopen.stderr`）：
```text

```

### 16-gen-access

```sh
python3 -I tools/hwdriver/gen-pmp4010-access.py --check
```

cwd: `/home/zakiu/km3`；rc=0；UTC 2026-10-20T00:57:19.753035+00:00 → 2026-10-20T00:57:19.774771+00:00。

stdout（原始文件：`16-gen-access.stdout`）：
```text

```

stderr（原始文件：`16-gen-access.stderr`）：
```text

```

