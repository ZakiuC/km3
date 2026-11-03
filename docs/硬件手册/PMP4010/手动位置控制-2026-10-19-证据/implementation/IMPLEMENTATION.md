# 手动位置控制实现与局部验证（2026-10-19）

已实现单屏软件坐标与相对/绝对目标，复用原始绝对607A提交、Halt使能、使能后同一冻结607A重写/读回及002F/003F/002F新ACK事务。修改文件为pmp4010.py、pmp4010_manual.py、pmp4010_simple.py、_test_simple.py、_test_simple_pty.py；_test_manual.py、_test_manual_pty.py未修改但一起冻结。

## 使用与单位

真实标定已由用户提供为每电机轴转4mm；交付launcher应传：

```
python3 -I -B device/drivers/pmp4010/tools/pmp4010.py --manual --manual-linear --manual-mm-per-rev 4
```

通用程序系数缺省为None，不猜测，启动构造不开总线。`--manual-linear`仅可与`--manual`使用，`--manual-mm-per-rev`仅可与`--manual-linear`使用，接受正有限数/分数。未设置系数时禁止f/b/r/j/p/e动作，允许只读、软件零点、快停、失能与退出。c设置换算仍要求d失能并确认、无活动proof。

线性模式r/j输入正负相对mm，p输入相对本会话软件零点的绝对mm；位置输入Enter生成冻结审阅，再Enter执行一次，Esc取消。f/b点动，a步长（初始0.1mm，按实时系数量化），s电机轴rpm，e显式Halt保持使能，z新鲜采样设软件零点，Space/x在任何页面请求快停，d显式审阅/确认失能，q有界安全退出。系数、软件零点、模式、实时标定、原始起止目标与设置均绑定审阅；设置变化使旧审阅失效。

z纯软件坐标捕获，不写607C、不回零、不Save、不发运动/参数下载；有合法本会话物理proof且速度采样为0时可设零，完整保留原proof的start/target/settings/calibration与持续guard；无proof要求Switch on disabled，外部enable拒绝。这里的速度采样与软件零点不等于证明机械静止。软件零点改变不重置物理origin、原始目标或限位。

mm/U = motor_degrees_per_user_unit / 360 * mm_per_motor_revolution。测试标定608F=131072/1、6091=1/1及用户4mm/转时，1U=1/32768mm，0.1mm向零量化3276U（0.0999755859375mm）；正负输入对称。目标必须先将带符号offset向零量化，再加整数原始零点，避免负数origin偏移。

显式linear模式不设主机1度或origin会话行程上限，仍取实读驱动607D、607A Int32及G换算后6063 Int32的交集。预算为合法端点间距high-low，允许合法Int32端点之间距离超过Int32正上限。普通manual仍为电机轴角度与1度/固定origin包络，其r范围键兼容，相对位移新增j、绝对p、零点z；普通position/bench规则未改。OD、607C非零、模式、速度/加减速度、故障/跟随误差、隔离不重试及退出清理保持。

## 局部验证

完整原argv/cwd/rc及stdout/stderr索引为[command-index.json](command-index.json)，最终7文件SHA256为[source-final-sha256.json](source-final-sha256.json)，与source-before-sha256完全一致。运行脚本为run-local.py。

|命令|实测|
|---|---|
|python3 -I -B -m unittest discover -s device/drivers/pmp4010/tools -p _test_simple.py|31 tests，rc0|
|python3 -I -B -m unittest discover -s device/drivers/pmp4010/tools -p _test_manual.py|52 tests，rc0|
|python3 -I -B device/drivers/pmp4010/tools/_test_simple_pty.py --run OUTPUT|34观察点，rc0|
|python3 -I -B device/drivers/pmp4010/tools/_test_simple_pty.py --run-linear OUTPUT|41观察点，rc0|
|python3 -I -B device/drivers/pmp4010/tools/_test_manual_pty.py --run OUTPUT|27观察点，rc0|
|python3 -I -B device/drivers/pmp4010/tools/pmp4010.py --help|rc0|
|git diff --check -- 七文件|rc0|

PTY观察点不是独立unit tests。全部PTY为生产公开CLI、真实curses按键、标准MockBus SDO，socket创建被fixture禁止。linear PTY的360mm/转为加速模拟轨迹用例，非机器实际标定；实际4mm精确数值由Simple单元用例覆盖。线性PTY五次relative/absolute均核对冻结607A、enabled重写和新ACK触发、最终模拟位置，并核对运行proof下z零下载、proof不变、越界0动作、编辑/审阅快停与退出。

前一轮全部rc0结果保留于pre-finish-pass（最终两处授权文案修正前），未覆盖失败。最终一轮无失败。没有执行全量七门、C++重编、SSH、板端、真实CAN、部署或物理运动；这些属于父会话和独立验收工作。

临时目录/tmp/pmp4010-linear-implementation-first及/tmp/pmp4010-angle-implementation-first已清理；正式证据全部保留于本目录。未回滚其他dirty改动，未commit/push。
