# tools — 主机侧脚本

| 脚本 | 用途 |
| --- | --- |
| `check-deps.sh` | 校验本表所列依赖与两个 CMake 工程的 `find_package` 一致（CI 调用） |
| `check-docs.sh` | 校验文档引用、仓库内路径引用、目录树、CAN 写法歧义（CI 调用） |
| `install-deps.sh` | 安装 C++ 工具链与库（需 sudo；分组安装，单组失败不影响其余） |
| `android-setup.sh` | 打印 Android SDK / JDK 安装命令（需先装 JDK 17） |
| `provision_device.sh` | 设备端置备：用户/目录/数据盘挂载/udev/单元（默认 dry-run，需 `--apply`） |
| `deploy_device.sh` | 设备端部署：版本目录 + 符号链接原子切换 + 失败回滚 |
| `deploy_server.sh` | 服务器端部署：rsync + systemctl + `/readyz` 健康检查 |
| `deploy_web.sh` | 管理端部署：静态产物 rsync 到版本目录 + 符号链接切换（无服务重启） |
| `sign-firmware.py` | **离线**固件签名（Ed25519），产出 manifest.json |

**分工**：`provision_device.sh` 负责"这台机器具备运行条件"（用户、目录、挂载、单元、udev），
只需在机器首次上线或重装后执行一次；`deploy_device.sh` 负责"把新版本放上去"，
每次发布都跑。两者不可互相替代——部署脚本会 `systemctl restart`，
而单元文件由置备脚本安装，所以**新机器必须先置备再部署**。

部署脚本均支持 `--dry-run`。签名工具的私钥**不得进入生产路径**；
服务器只分发固件，不持私钥（见 `OTA升级方案.md §六`）。

管理端是静态站点，因此 `deploy_web.sh` **不重启任何服务**，回滚即切回上一个
版本目录。它要求 nginx 已按 `管理端Web程序架构.md` §八 配置：
静态根指向 `current`、SPA 回退到 `index.html`、`index.html` 不缓存、
`/api/` 反代到本机服务端（**同源是 Cookie 会话与实时通道的前提**）。
