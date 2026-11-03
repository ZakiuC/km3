# PMP4010 手动入口

隔离包只面向节点1。实际接口由板上现有 /etc/km3/device.json 的 can-2 解析；本包不修改配置、不切 current、不启动服务。

先纯加载（不打开总线）：

```sh
python3 -I -B /ABSOLUTE/PACKAGE/load_smoke.py --check-missing-library
```

用户本人运行（把路径替换为实际解包目录）：

```sh
ssh -t root@192.168.0.188 '/ABSOLUTE/PACKAGE/run-manual.sh'
```

可加 --manual-velocity 100、--rows 40、--cols 150 等显示/超时参数；启动脚本拒绝传入RT/OD或动作确认参数。默认速度100 U/s，实际rpm及设备上限在界面审阅。

启动、扫描不会自动使能。依界面完成人工RT确认、OD理由、软行程及位移预算；A/R提交目标后，E保持Halt使能，G单独解除Halt，X软件Quick stop，D失能，q退出。每个写动作保留两段确认；真实急停、故障和通信异常保护保留。取消自动起步和零漂移检查。Quick stop不等同失能。

日志位于本隔离包 logs/manual-XXXXXXXX/；不代填目标、RT/OD理由或动态确认文本。本包仅完成本地主机模拟/PTY与静态检查；板端纯加载和真实人工操作尚待执行。
