# PMP4010 失能 PP 准备包

本包冻结独立 prepare_pp：严格失能且静止的 PP1 状态下，允许 bit10=0；607A 对齐实读位置并读回，6081 降至100并读回。原运动 bit10 守卫和两段确认保持。

7个 hwtui 运行模块、6个 PMP4010 运行模块、原OD、共享向量与 ARM64 纯数学库使用既有布局。精确源码与 payload SHA256 在包外 source-manifest.json、full-source-sha256.json 及包内 payload-manifest.json。HEAD 不能单独代表未提交源码。

本地只用 host 库覆盖运行纯软件 smoke；没有执行 ARM 产物或连接硬件。检查包括16模块导入、33 native+33 Python+14 Int32向量、缺库拒绝及禁止IO计数0。数学检查不证明真实CAN或运动。

在父线程协调的板端隔离目录解压后运行：

```sh
python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library
```

不传 --library 时强制加载同包库；本包没有自动安装、服务更改或 current 切换。既有现场守卫与授权边界继续适用。
