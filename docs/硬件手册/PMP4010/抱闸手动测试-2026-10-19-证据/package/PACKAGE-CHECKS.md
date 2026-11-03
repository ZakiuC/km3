# 最终离线抱闸包构建检查

归档：`/home/zakiu/km3/docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/package/pmp4010-brake-offline-payload.tar.gz`

SHA256：`e137c603d57c584134f14f006bcae482d3b999ce1d2dda8dd25b19005a80b237`；235068 字节。

26 个普通文件、2 个符号链接；旧清单全部路径保留，仅 loader 增加 brake 导入，新加模块/启动器/中文说明。manual/simple 与 ARM 库哈希保持冻结值，测试源码未入包。原始候选与其日志保存在 candidate-before-diagnostic-refresh/，旧 ca634a 包未变。

两次冷加载（原 payload 与 tar 解压回读）各 20 模块、ABI 1、原生 33/ Python 33/整数 14 向量，forbidden_io_attempts=0。宿主仅使用现有 --library 原生库替换，未执行 ARM。逐项哈希、符号链接与清单回读相同。

代码专项 22 项通过；最终全套与 PTY 结果由验收 worker 另行记录。板端加载、机械抱闸效果仍待用户确认。agent 未执行任何 SSH、板端复制、真实 CAN 或启动操作。用户命令见 USER-COMMANDS.md。仓库静态配置 can-2→can1、1 Mbps，启动器固定节点 1；未读取板端实时配置。

| 检查 | 实际返回码 | 预期返回码 | 原始日志 |
| --- | --- | --- | --- |
| source-head | 0 | 0 | source-head.stdout.log / .stderr.log |
| launcher-shell-syntax | 0 | 0 | launcher-shell-syntax.stdout.log / .stderr.log |
| launcher-help | 0 | 0 | launcher-help.stdout.log / .stderr.log |
| launcher-no-tty | 2 | 2 | launcher-no-tty.stdout.log / .stderr.log |
| launcher-parameter-refused | 2 | 2 | launcher-parameter-refused.stdout.log / .stderr.log |
| host-load-smoke | 0 | 0 | host-load-smoke.stdout.log / .stderr.log |
| roundtrip-smoke | 0 | 0 | roundtrip-smoke.stdout.log / .stderr.log |
| archive-list | 0 | 0 | archive-list.stdout.log / .stderr.log |

完整 argv、cwd 与返回码见 command-index.json；builder 调用及原始输出见 builder-invocation.json、builder-run.stdout.log、builder-run.stderr.log。
