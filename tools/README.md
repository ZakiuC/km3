# tools — 主机侧脚本

| 脚本 | 用途 |
| --- | --- |
| `check-deps.sh` | 校验 `install-deps.sh` 所列依赖与两个 CMake 工程的 `find_package` 一致（CI 调用） |
| `check-docs.sh` | 校验文档引用、仓库内路径引用、目录树、CAN 写法歧义（CI 调用） |
| `check-drivers.sh` | 硬件驱动解耦断言：五件套齐全、链接白名单、驱动互引、能否从 `device/CMakeLists.txt` 到达（CI 调用） |
| `check-manual-citations.sh` | 手册溯源闸门：引用目标须真实存在、`od.json` 每条目带 `status`/`cite`、源码里的索引字面量同行须带引用（CI 调用） |
| `install-deps.sh` | 安装 C++ 工具链与库（需 sudo；分组安装，单组失败不影响其余） |
| `android-setup.sh` | 打印 Android SDK / JDK 安装命令（需先装 JDK 17） |
| `provision_device.sh` | 设备端置备：用户/目录/数据盘挂载/udev/单元（默认 dry-run，需 `--apply`） |
| `deploy_device.sh` | 设备端部署：版本目录 + 符号链接原子切换 + 失败回滚 |
| `deploy_server.sh` | 服务器端部署：rsync + systemctl + `/readyz` 健康检查 |
| `deploy_web.sh` | 管理端部署：静态产物 rsync 到版本目录 + 符号链接切换（无服务重启） |
| `deploy_hwtools.sh` | 硬件驱动工具源码交付：tar 经 ssh 送到 `/opt/km3/hwtools/<版本>/` + 符号链接原子切换 + 失败回滚（**需 root**，默认 dry-run） |
| `sign-firmware.py` | **离线**固件签名（Ed25519），产出 manifest.json |

**本表只索引 `tools/` 直下一层的脚本。** 子目录里的脚本有各自的入口，不列在这里：
`tools/hwdriver/` 是驱动骨架与对象字典生成器（入口见 `device/drivers/_template/README.md`），
`tools/hwtui/` 是板载 TUI 的公共库（被各型号驱动的 `tools/<型号>.py` 复用）。
把它们混进本表会让"tools/ 下有哪些**主机侧**脚本"这件事不可读——
这张表的读者是要在主机上跑脚本的人，而子目录里的东西是给人抄的库。

这条边界是**机检**的：`check-docs.sh` 第 5 项只 `os.listdir("tools")` 一层，
不递归，因此子目录脚本未列入本表不会让 CI 变红；反过来，顶层脚本漏列必红。
两处的口径必须一致，改任何一处都要同时改另一处。

**分工**：`provision_device.sh` 负责"这台机器具备运行条件"（用户、目录、挂载、单元、udev），
只需在机器首次上线或重装后执行一次；`deploy_device.sh` 负责"把新版本放上去"，
每次发布都跑。两者不可互相替代——部署脚本会 `systemctl restart`，
而单元文件由置备脚本安装，所以**新机器必须先置备再部署**。

部署脚本均支持 `--dry-run`（`deploy_hwtools.sh` 默认即 dry-run，需 `--apply`）。
`deploy_hwtools.sh` 是其中唯一的例外——它**需要 root**：目标是 `/opt/km3/hwtools/`，
而 `/opt` 属 root 且 `km3` 用户 `CapEff=0`，所以它经 `ssh root@<主机>` 执行。
但它投的是**源码**：不装服务、不重启进程、不进 `SERVICES_CORE`，
也不改 `device/deploy/binaries.txt` 那份枚举（见 `硬件驱动开发路径.md` 末节）。签名工具的私钥**不得进入生产路径**；
服务器只分发固件，不持私钥（见 `OTA升级方案.md §六`）。

管理端是静态站点，因此 `deploy_web.sh` **不重启任何服务**，回滚即切回上一个
版本目录。它要求 nginx 已按 `管理端Web程序架构.md` §八 配置：
静态根指向 `current`、SPA 回退到 `index.html`、`index.html` 不缓存、
`/api/` 反代到本机服务端（**同源是 Cookie 会话与实时通道的前提**）。
