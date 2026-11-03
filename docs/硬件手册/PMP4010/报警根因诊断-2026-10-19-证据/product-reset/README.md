本目录是本地产品修复证据，所有收发均来自标准层 MockBus，未访问板端、SSH、网络或真实 CAN，未部署、未提交。目录日期延续用户任务日期；JSONL 时间戳由执行环境实际 UTC 时钟产生。

`focused-checks.json` 记录精确命令与退出码；对应 stdout/stderr 保留完整聚焦测试输出。`capture_evidence.py` 可从仓库任意位置运行，重建这些本地证据。完整 PMP 工具验收由父会话独立执行。

`source-sha256.json` 冻结四个修改文件。两个 `.diff` 与固定 7355 原包源码比较；`baseline-7355-source.json` 保存原 archive 与基线源 SHA，并确认源码逐字节匹配 archive 内成员。原包未修改。

新增复位测试覆盖三次 6040h 下载及 ACK 后等待、92 个实际 SDO 边界各注入丢应答/非法报文/取消（276 子例）、其他动作锁/参数/607Ch/PP1/行程/日志守卫、原客户端、不隐式授权运动、坐标保持、旧 Go 证明失效、下一次故障写新日志。真实 DelayedBus 测试证明按 t 等待在途读取完成，不取消它；80×24 渲染保留全部 OD 项和 Enter/Esc 的本次复位限定授权。

`mock-success-*`、`mock-lost-high-ack-*`、`mock-bad-final-controlword-*` 是代表性本地原始 SDO JSONL 与完整发送帧。后两项保留失败，无补写；最终 0 有 ACK 但读回 0080 时明确 `final_zero_readback_confirmed=false`、`bit7_may_remain_set=true`。已知报警/日志失败只锁定动作，不伪造 SDO 通信隔离。

退出回归保留同 session/client 的既有成功失能证据与 UTC 时间，标明 `source=prior_cleanup`、`shutdown_attempted=false`，零追加帧；任何后续写尝试、通信隔离、新失败或 needs_stop 都使此证明失效。它既不把原动作 Timeout 改成成功，也不证明本次退出进行了新实读，更不证明机械停止/抱闸。

开发中两次失败与修正记录在 `development-attempts.json`；当时完整输出仅在会话工具记录，未在本目录伪造补录。最终聚焦运行完整输出另存。

引用检查收口：`source-sha256-final.json` 是当前最终冻结，替代此前的 `source-sha256.json`（历史文件保留不改）。仅 manual.py 增补E06 §9与10.2表10-6相邻引用注释，并纠正“仅上传”注释的作用范围；未更改OD可信度或访问属性。`manual-citations-comments-only.diff` 与 `manual-citations-final-verification.json` 证明前后AST SHA和非注释token完全一致。仅运行一次 `bash tools/check-manual-citations.sh`，完整输出另存 `manual-citations-final.stdout/.stderr`；原失败与大套件验收结果保留，未重跑大套件。
