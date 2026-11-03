# PMP4010 stage2 未operationenabled起步核证临时包

阶段：`switched_on_unenabled_probe_only`。冻结清单SHA256：`3e492ef6a6b36aa94789d78500a2992aee0ff52b2702acc7e377ffb484408b47`。

B仅发送106/107/127/137/127到SWITCHED_ON并保持bit3=0、Halt=1，然后2/0 cleanup。禁止010F/012F/013F fallback，禁止release Halt，不打开完整PP位移分支，不加位置容差或settling。任何失败停止，不自动重试。协议严格失能、位置未变、速度为零按独立结果字段报告。

本包独立于stage1；stage1真实010F后发生+12U微位移，核证失败。stage2本地Mock/纯加载不能证明固件行为或起步硬件通过。

白名单内容：7共享hwtui模块、6型号工具模块、OD/共享向量、ARM数学库及2链接、纯加载自检和本说明。精确源码及payload哈希见包外source-manifest.json、full-source-sha256.json、包内payload-manifest.json。

本地host纯加载仅验证16imports、native33/Python33/Int32 14数学向量、缺库拒绝与禁止IO计数；不执行ARM或打开CAN。

经父线程授权和协调的板端隔离临时目录纯加载命令：

```sh
python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library
```

不传 --library 强制同包库。本包没有自动安装、服务更改或current切换。
