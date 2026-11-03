# PMP4010 抱闸手动听声测试

本包只在本地开发、Mock/PTY 和冷加载检查。未连接真实 CAN 或板端，未部署。ARM 板端加载和机械抱闸效果待人类操作确认；当前物理堵转未解决。

在已获准现场操作的独立目录解压本包，进入目录后启动：

```sh
chmod +x ./run-brake.sh
./run-brake.sh
```

启动器固定节点 1，沿用原设备配置解析：can-2 映射原 can1；不覆盖或修改设备配置。每次运行独立记录到 `logs/brake-XXXXXXXX/motion.jsonl`。帮助命令为 `./run-brake.sh --help`；启动器不接收运动或配置参数。未使用交互终端时直接拒绝，不连接 CAN。

按 `1` 或 `e` 后再按 Enter，请求使能当前位置保持以松闸；按 `2` 或 `d` 立即请求失能以抱闸。按 Space 或 `x` 停止，按 `q` 退出。实际机械抱闸开合需听声确认；使能会接通保持力矩，不能保证轴绝不调整。操作时保持独立急停可用。

不配置 DO、PWM、VDI、电流或 Save，不自动运动、复位或循环。不会为了听声生成位移目标；当前位置保持仍可能带来调整，软件请求成功也不能证明机械抱闸已动作。

旧手动工具和其 README 原样保留。本入口使用 `pmp4010_brake.py` 和 `run-brake.sh`，不切换 current 或服务。

宿主离线冷加载（只替换原生换算库，不执行 ARM 库）：

```sh
python3 -I -B ./load_smoke.py --library /home/zakiu/km3/build-host/libpmp4010_velocity.so --check-missing-library
```

加载器禁止 socket、子进程等真实 I/O，检查 20 个模块、ABI 与换算向量。冷加载、Mock/PTY 不等于 ARM 或硬件验收。
