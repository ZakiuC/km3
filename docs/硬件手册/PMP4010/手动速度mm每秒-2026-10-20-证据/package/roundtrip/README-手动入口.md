# PMP4010 自实现手动测试：直线速度 mm/s

run-manual.sh默认node1、manual-linear、4mm/电机轴转；配置can-2解析到can1。连接与会话授权按Enter，无需手填OD理由。
线性模式s设置mm/s，主屏实际/下一速度、审阅、帮助均为mm/s；角度模式仍rpm。
换算为 mm/s×60÷每电机轴转毫米，再调用唯一C++ rpm→U/s函数；底层单位仍U/s。
默认仍1000U/s，不提高原速度；--manual-velocity继续明确接受U/s兼容值。
c修改mm/电机轴转不会自动改变设备U/s，清除旧请求并使旧审阅失效。
软件零点z、相对r/j、绝对p、步长a、正反f/b、保持使能e、失能d、2312受限复位t均保留。
输入参数Enter审阅，再Enter执行；Space/x停止、q安全退出。真实通信、限位、标定、607C、状态与隔离保护不变。
日志保存在包内logs/manual-XXXXXXXX/。本包不配置CAN、不自动使能/复位、不更改正式OD状态。
本轮本地Python与PTY为mock验证；板端只做禁止I/O加载及帮助，不代表实际CAN、固件、负载、抱闸或运动验收。
宿主离线加载：python3 -I -B ./load_smoke.py --library /home/zakiu/km3/build-host/libpmp4010_velocity.so --check-missing-library
该检查阻断socket/进程I/O，验证20模块、ABI和33native/33Python/14int32向量；宿主不得加载包内AArch64库。
实际板端更新状态与稳定命令见同轮验收与部署报告；保留run-brake.sh及原抱闸说明。
