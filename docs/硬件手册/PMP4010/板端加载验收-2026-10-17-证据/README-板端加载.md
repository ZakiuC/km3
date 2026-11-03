# PMP4010 本地待审阅加载包

包内相对布局遵循现有工具交付布局：`hwtui/` 与 `drivers/pmp4010/` 同级，纯数学 ARM64 so 与 PMP4010 Python 脚本同目录。没有 device.json、systemd 单元或自动安装/切换脚本。只包含7个hwtui运行模块、6个PMP4010运行模块、原OD文件、共享数学向量和原生库。未包含测试/PTY运行器、其他驱动或整个仓库。

本包使用源 HEAD `7541b67edad414ef9b12999dd615f5ea228ed101` 上的未提交工作区，不能把 HEAD 单独当作完整源码版本；精确文件SHA和dirty记录在包外 source-manifest.json 与 records/ 中。ARM库来自现有验收产物 build-arm64/libpmp4010_velocity.so.1.0.0。

本地只以 x86_64 host 对应库验证 smoke 逻辑，未执行 ARM 产物。板端库解析、Python导入和向量执行仍待真实板端检查；数学检查不证明CAN通信或真实运动。

## 板端纯软件检查（后续经父线程授权上传后执行）

操作者显式选定解压目录。此包不会选择版本目录，也不会切换 current。将下例 `/ABSOLUTE/EXTRACTED/PACKAGE` 替换为显式选择的实际绝对目录：

```sh
python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library
```

不传 `--library` 时，脚本明确忽略继承的 PMP4010_VELOCITY_LIBRARY，强制加载包内库。输出其实际路径、SHA、ABI、Python版本和架构，逐项验证manifest、16种模块导入、33条原生向量、33条Python向量、14条原生Int32向量。包含默认50000→300rpm、现场50000→46875/2048rpm、6553600→3000rpm及反演，均为离线常量，并非重新测得的设备标定。

脚本不调用工具 main、总线发现、TUI、配置读取或服务操作。Python审计钩子在创建socket或启动子进程前拒绝，任何尝试均使检查失败。`--check-missing-library` 仅把当前进程环境变量指向一个不存在文件来验证明确失败，不删除文件。`-I -B` 防环境路径注入并禁写pycache，脚本本身也禁写pycache。

## 上传前后续只读兼容性探测清单

以下仅是待执行清单，本地没有连接板端：

1. 确认设备身份与目标IP、架构 `uname -m` 为 aarch64；检查 `getconf LONG_BIT` 与 `python3 --version`。脚本要求Python>=3.9，既有记录为3.10.12，须以当前实测为准。
2. 只读检查目标文件系统空间/权限、拟定目录是否已存在、当前链接的 `readlink` 结果。禁止擅自覆盖已有版本或切换链接。
3. `getconf GNU_LIBC_VERSION`；读取板端 `ldconfig -p`（不带写缓存选项）定位 libstdc++.so.6、libgcc_s.so.1、libc.so.6、ld-linux-aarch64.so.1。以readelf --version-info或strings确认GLIBC_2.17、GLIBCXX_3.4.21、CXXABI_1.3/CXXABI_1.3.9、GCC_3.0可用。若工具缺失，报告缺失，不能把本地ELF分析当作板端解析成功。
4. 上传/解压须由父线程另行授权及协调；校验包与库SHA、完整manifest，执行上方纯软件smoke并保留完整命令、原始输出与退出码。
5. 真实CAN只读探测、任何参数写入、使能和真实运动使用独立现场授权及既有守卫。此加载包及数学检查不授予这些权限。

ELF静态检查：AArch64、SONAME libpmp4010_velocity.so.1；依赖恰为上述4个标准运行库，未发现ASan/TSan/UBSan等runtime名称或符号，不声明CAN/HAL/OD动态依赖。标准ABI要求已列出，板端是否满足仍待只读探测。
