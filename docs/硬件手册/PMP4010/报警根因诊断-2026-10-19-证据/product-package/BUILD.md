# 离线候选包构建

当前 builder 已准备；必须先取得主会话确认的源码冻结哈希和本地验收记录。不能用旧包测试数量代替当前验收。原冻结包与原运动/复位证据保持不变。

在本目录创建 `freeze-input.json`，格式如下；哈希与 evidence 必须填入真实值：

```json
{
  "source_hashes": {
    "device/drivers/pmp4010/tools/pmp4010_manual.py": "冻结后的 SHA256",
    "device/drivers/pmp4010/tools/pmp4010_simple.py": "冻结后的 SHA256"
  },
  "local_acceptance": {
    "status": "passed",
    "evidence": "本轮验收原始记录路径与命令结果"
  }
}
```

可追加 `_test_manual.py`、`_test_simple.py`、`_test_bench.py` 的冻结哈希。测试文件只做冻结校验，不进入交付包。

从 `/home/zakiu/km3` 执行以下完整命令：

```sh
python3 -I -B docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/product-package/build_package.py --freeze-input docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/product-package/freeze-input.json
```

输出 `pmp4010-manual-reset-offline-payload.tar.gz`、`archive-manifest.json`、`source-manifest.json`、`summary.json` 和每项检查的 stdout/stderr/returncode。已有 payload/roundtrip 时拒绝覆盖。失败的中间产物必须保留并由主会话明确决定后续处理。

基线仅来自 `报警内容显示-2026-10-19-证据/package/payload` 已校验清单：旧归档 SHA256 为 `7355389782042fdc3baa7d1ddc114cc2e11df14fa304293f8b1683037bde1d72`，旧载荷清单 SHA256 为 `5072e207253957356b28f03d989efa3b798efa85b240e196060d6eb40495e40b`。

新包仍为 23 个普通文件和 2 个符号链接（含载荷清单）。只替换 manual/simple 源码、中文 README 与本轮清单，启动器和 load_smoke.py 原样保留。AArch64 换算库 SHA256 必须为 `59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404`。

检查范围：启动脚本 sh -n、--help、现有加载器配 --library 指向 build-host 原生库的 19 模块/ABI/33+33 换算向量/14 整数向量检查、禁止 socket/子进程审计、归档解压后逐文件哈希与符号链接检查。宿主冷加载不是 AArch64 执行或硬件验收；板端加载、复位与运动均待完成，当前物理堵转未解决。构建过程不连接 CAN、不执行 SSH、不部署、不更改服务，也不使用 QEMU。
