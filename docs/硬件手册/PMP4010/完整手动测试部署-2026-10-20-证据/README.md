# 完整手动测程工具已部署（上海日期 2026-10-20）

板子 `root@192.168.0.188` 上执行：

```sh
/opt/km3/hwtools/manual-test-20261020-e137c603/run-manual.sh
```

默认节点1、直线模式、4 mm/电机轴转，每次新建独立日志目录。完整手动入口包含软件零点、相对/绝对位置、转速、步长、正反点动、使能/失能、2312受限报警复位与快停。程序启动后的实际操作由用户执行。

冻结原包 SHA256：`e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237`（235068 bytes）。清单 SHA256：`8cf6fa0d6efdfb3831ff7e5397fd67314aa3ea433969e07d51478296634caa51`。启动器 SHA256：`56641a72621da4fbc831e047437fe1a668dbf9de60380d0334a2234a1f40d1c1`。

已在 aarch64 / TL3588-EVM / Python 3.10.12 实际运行：

- `python3 -I -B /opt/km3/hwtools/manual-test-20261020-e137c603/load_smoke.py --check-missing-library`：rc=0，20 imports，包内ARM库 ABI=1，native=33、python=33、int32=14，禁用I/O尝试=0，缺库预期拒绝。
- `sh -n /opt/km3/hwtools/manual-test-20261020-e137c603/run-manual.sh`：rc=0。
- `python3 -I -B /opt/km3/hwtools/manual-test-20261020-e137c603/drivers/pmp4010/pmp4010.py --help`：rc=0；解析帮助后退出，未启动TUI。

上传前后 `/etc/km3/device.json`、`/etc/km3/can.env` SHA256与`current`链接一致；配置静态can-2→can1、1000000 bps。26个普通文件与2个链接再次核验一致，无额外运行文件。新目录由临时目录原子改名生成，自有上传临时目录已清理。

未运行实际TUI、扫描、CAN、使能/复位/运动、服务操作，物理验收未执行。此次仅完成板端文件部署及无CAN ARM加载验证。

每次SSH/SCP的完整argv、输入脚本SHA、UTC原始时间、实际rc在对应`.command.json`；原始输出在`.stdout`和`.stderr`。第一次只读前检因嵌套字节转义产生SyntaxError（rc=1），修正后rc=0，无板端修改。一次本地归档验证脚本因缩进失败（rc=1）且后继upload.py不存在（rc=2），修正后归档校验通过再上传；此失败没有SSH或板端修改。
