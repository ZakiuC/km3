# PTY截图发布竞态修复

独立验收首轮全量400 tests，rc1，唯一ERROR位于线性PTY首次confirm截图归档：FileNotFoundError，confirm-80x24.txt不存在。生产PP事务无此错误。原model stderr/相关文件副本及SHA保留于first-acceptance-*与first-acceptance-failure-provenance.json，父验收目录原文件未改。

只修改_test_simple_pty.py。ObservedApp原先先原子发布state.json，再写截图；父进程看到confirm就能先执行截图归档，从而抢在截图创建之前。现将同一poll采样的截图完整写入临时文件、原子替换视图截图后，才完整写入临时state、原子替换state.json；state可见即截图已完整提交。截图名绑定采样data[view]，没有用sleep掩盖竞态。

新增SnapshotPublicationTests在state.json原子rename的同一回调中立即检查截图存在、完整并对应当前target；同一confirm视图连续两个target还检查不会看到陈旧截图。保留旧发布顺序的提取版源码before-fix-reproducer.py，该边界回归稳定rc1（old-order.command.json及日志）；修复后同回归rc0。该失败为明确旧顺序重现，未改判通过。

新command-index.json保留局部完整argv/cwd/rc/stdout/stderr：publication-contract 1 test rc0；同模块unit 3 tests rc0（含真实公开angle及linear CLI MockBus PTY）；独立linear两次各41观察点rc0；独立angle34观察点rc0。观察点不是独立unit tests。没有继续全量矩阵、硬件、SSH或部署。父要求聚焦时这一次局部脚本已结束，此后未追加测试。

source-before-sha256.json与source-final-sha256.json一致，列7文件；相对上一implementation/source-final-sha256.json仅_test_simple_pty.py改变，其余6文件完全一致。新的最终冻结以本目录source-final-sha256.json为准。已直接通知父、acceptance及board，业务源码保持冻结。

无临时目录残留（TemporaryDirectory清理）；有意保留旧失败、修复前提取版源码、最终PTY原始终端和截图证据。未回滚其他dirty文件，未提交。
