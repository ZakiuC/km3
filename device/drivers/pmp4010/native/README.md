# PMP4010 精确速度换算原生库

选择方案 A：速度公式仅由 C++ 实现，Python 通过 `ctypes` 调用 ABI 1。
本目标只含数学运算，不链接 CAN、HAL、OD，也不带 OD 旁路宏。
独立构建无需完整设备端依赖，要求 CMake 3.20、C++17，以及支持
`unsigned __int128` 的编译器（本项目的 GCC x86_64/aarch64 工具链支持）。

```sh
cmake -S device/drivers/pmp4010/native -B /tmp/pmp4010-velocity-native -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/pmp4010-velocity-native -j2
PMP4010_VELOCITY_LIBRARY=/tmp/pmp4010-velocity-native/libpmp4010_velocity.so python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py'
```

完整 `device` 构建也生成构建根目录的 `libpmp4010_velocity.so`。纯库可单独
构建 ARM64；在源码根目录运行：

```sh
cmake -S device/drivers/pmp4010/native -B /tmp/pmp4010-velocity-arm64 -DCMAKE_TOOLCHAIN_FILE="$PWD/device/cmake/aarch64-linux-gnu.cmake" -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/pmp4010-velocity-arm64 -j2
```

这是构建命令，不会访问板端。需要的本地暂存安装命令：

```sh
cmake --install /tmp/pmp4010-velocity-native --prefix /tmp/pmp4010-velocity-install
```

安装产生 `lib/libpmp4010_velocity.so*` 和
`include/km3/pmp4010/pmp4010_velocity_c.h`。Python 的
`PMP4010_VELOCITY_LIBRARY` 应指向适配当前 CPU 架构的库文件；找不到库或 ABI
不匹配时明确报错，不回退到 Python 公式。当前不修改部署脚本。

ABI 定义见 `../driver/pmp4010_velocity_c.h`。方向 0 为用户单位/s → 电机 rpm，
方向 1 是代数反演。手册 E06 §5.2 **只给正向关系**，反向不是手册原文。
四项标定必须是正 Uint32；速度可为负，也可为零。输入是十进制整数分子、
分母，支持符号、拒绝空白/小数点/指数/非有限字符串与零分母。每个输入及约分后
输出的分子幅值、分母均不得大于 `340282366920938463463374607431768211455`
（2^128−1）。先交叉约分再检查乘积；超界返回 6，不舍入、不截断。
该边界覆盖全部 Int32 用户速度与四项正 Uint32 标定的正向转换和精确反演。

`velocity_to_unit` / `unit_to_velocity` 返回精确分数；设备写值应使用
`velocity_to_int32`。C ABI 的 `pmp4010_velocity_require_int32` 将非整数拒绝为 5，
Int32 越界拒绝为 6。错误码 1=参数非法、2=标定非法、3=输出缓冲不足、4=内部错误。
所有失败均保持输出参数原值。

C++ 的 `SetTargetVelocity`、`SetVelocity`、`WriteObject(60FF)`、`SdoWrite(60FF)`
共用一次实时四项标定读取、607F 用户单位/s 与 6080 rpm 双限速及 60FF 写后读回。
`SetTargetVelocityRpm` 使用同一次实时标定快照完成反演与限速。允许 disabled
时预置值，但 setter 不发送使能或启动指令。模式 3/9 的每次使能必须先显式
写入目标并确认读回；使能前重新核对标定、限值、模式与目标，再写同目标并读回。
凭据绑定本次打开会话、节点、模式、四项标定及目标，使用一次后失效；停止、
禁能、复位、模式或 selector 写入、事务失败与重连均清除。模式 2/7 仍拒绝，
因为其使能终值不同。
新展开的标定和限值子对象只允许通过驱动读取，不扩大通用参数写权限。

共享的唯一数学向量是 `../tests/velocity_vectors.json`，C++ 测试构建时仅将其
适配为头文件，Python 测试直接读取同一 JSON。测试适配器不含换算公式。
