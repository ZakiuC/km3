# PMP4010 手动入口：手动默认速度修复（2026-10-19）

隔离包只面向节点1。实际接口由板上现有 /etc/km3/device.json 的 can-2 解析；本包不修改配置、不切 current、不启动服务。

先纯加载（不打开总线）：

```sh
python3 -I -B /ABSOLUTE/PACKAGE/load_smoke.py --check-missing-library
```

用户本人运行（把路径替换为实际解包目录）：

```sh
ssh -t root@192.168.0.188 '/ABSOLUTE/PACKAGE/run-manual.sh'
```

可加 --manual-velocity 100、--rows 40、--cols 150 等显示/超时参数；启动脚本拒绝传入RT/OD或动作确认参数。默认速度1000 U/s，实际rpm及设备上限在界面审阅。

本轮将手动入口默认速度从100改为1000 U/s，显式100仍可使用。保留此前使能后目标重写：go 在 enabled Halt12F/ACK0/零速完整核对后，对同一冻结607A重写并读回一次，再执行既有2F→3F→2F与新ACK0→1→0。固件dirty flag假设尚未证实。

启动、扫描不会自动使能。依界面完成人工RT确认、OD理由、软行程及位移预算；s设置速度、a设置步长；f/b 后按 Enter 确认正/反点动；e 后按 Enter 确认保持当前位置使能；Space/x 在任何页面停止；d失能、q退出。RT/OD每会话确认一次；不代填运动确认。1度预算、607C、故障和通信隔离等保护保留。Quick stop不等同失能。

日志位于本隔离包 logs/manual-XXXXXXXX/；不代填目标、RT/OD理由或动态确认文本。本包仅完成本地主机模拟/PTY与静态检查；板端纯加载和真实人工操作尚待执行。
