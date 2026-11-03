# PMP4010 失能起步 probe_no_travel 临时包

阶段：`probe_no_travel` / `halt_probe_only_no_travel`。冻结清单 SHA256：`a9750a6952ee851f502bd0bd59b7eedd2542d35c1f423503f1a75acc17c6e5ff`。

本包用于保持 Halt 的起步核证：alignment、带 Halt enable、fresh ACK、6062/607A/current 联合证据；不得 release Halt。核证固件行为之后才能安排最终小动作。本包不代表真实起步或真实运动通过。

白名单内容为7个共享 hwtui 模块、6个型号工具模块、od.json、共享数学向量和 ARM64 数学库及2个库链接。精确源码与产物 SHA256 见包外 source-manifest.json、full-source-sha256.json 和包内 payload-manifest.json；HEAD 不能单独代表未提交源码。

本地只用 host 库覆盖纯软件加载自检，没有执行 ARM 产物或访问硬件。自检包括16模块导入、33 native+33 Python+14 Int32向量、缺库拒绝及禁止IO计数0；数学检查不证明真实CAN或运动。

板端在父线程授权和协调的隔离临时目录解压后，可执行纯加载自检：

```sh
python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library
```

不传 --library 时强制加载同包库。本包没有自动安装、服务更改或 current 切换。
