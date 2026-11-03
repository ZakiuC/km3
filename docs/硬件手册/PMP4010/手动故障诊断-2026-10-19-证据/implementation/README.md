# 手动故障诊断实现与本地证据

范围仅 `pmp4010_manual.py`、`pmp4010_simple.py`、`_test_manual.py`、`_test_simple.py`。没有硬件、SSH、部署、运动验收或提交。既有 dirty 工作树保留。

`_snapshot` 仅保存本轮已经返回的字段，异常通过属性带出非完整快照，不继续读取603F或6040。603F异常包含原始hex，603F是最后错误低16bit，不能单独推断当前Fault或根因。UI保留最后有效采样的历史标签；当轮未知CW、未知603F均明确显示未知。

snapshot serial区分前一成功采样与本轮失败采样。本轮partial中的None也是证据；不会拿前轮raw6041、位置、速度、故障码补成当前实读。已锁定的同轮phase first_failure保持优先；阶段控制字标明非本轮6040实读，另存expected_controlword与observed_controlword。

原有owned/quarantined清理条件和控制调用保持；先完成清理尝试，再独占创建 `--motion-log` 同目录的 `BASE-diagnostics-RUN-node-N.jsonl`。日志保存首次失败和清理结果，字段包括stop_attempted、skip_reason、disabled_confirmed（三态）、cleanup_error、mechanical_stop_confirmed=false。日志写入失败只报告证据缺失，不会新增SDO或通信隔离。

`run_local_checks.py` 保存两套受影响测试的精确argv、rc、完整stdout/stderr与四文件sha256；结果见verification.json。`capture_samples.py` 仅运行MockBus夹具，保存初始603F、running保护603F以及80×24界面。样例中的临时motion_log_base来自MockBus，临时目录已由fixture清理。

新增8个manual和3个simple回归：初始603F落盘；running保护603F精确请求/原清理顺序；首6041故障不追读603F；隔离优先skip且零新增发送；日志写入在清理后且失败不抹原故障；清理异常与原始603F分别保存；成功snapshot精确原读序；前一成功snapshot后下一首6041失败的实读来源；未知CW的初始80×24；后台历史值80×24；未知603F的80×24。

调试失败与重跑保留：

- 早期新增逐帧断言漏计原有stop/disable前各一次2002:01模式守卫，修正测试期望，生产读序未改。该次命令为 `python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_manual.py' -v`，rc=1，59例，failures=1、errors=1。错误还包含下述15ms问题；这次终端输出未完整落盘，不能把后续raw当成此轮原始输出。
- 正式attempt1/2的manual原始输出均为rc=1、59例、errors=1；attempt3为rc=1、60例、errors=1。原始输出及对应verification-attemptN.json保留。失败均在既有moving_handshake_all_protected_guards_cleanup的0x608f子场景，正常回复被15ms主机期限拒绝。attempt1/2位于6081；attempt3位于2006:12。
- `manual-gc-diagnostic.raw.txt` 中同段generation-2 GC耗时18.382ms，超过15ms。该wrapper的完整命令为终端heredoc，未另存argv文件；raw包含退出码1。GC未禁用。
- `compare_fixture_gc.py` 对同一既有case的归档baseline/current各跑一次，双方rc=0，GC约6.992ms/6.823ms；argv与原始输出见gc-comparison.json及两个raw。该单case对比未复现超时，不能称为baseline已复现失败。
- 为避免该多场景值保护测试受主机GC打断，仅该case把模拟SDO预算设100ms；专测timeout/隔离的fixture预算、生产参数和所有控制写均保持。最终结果由verification.json报告。

最早一次baseline比较脚本因未安装共享工具import路径而ModuleNotFoundError（tui），修正import前置后得到上述有效对比；不能把这次导入失败算作baseline行为失败。

生产文件baseline来自父会话指定的已交付payload，成员名、原SHA和缺失测试文件记录在baseline-source.json。两个生产文件的当前diff位于pmp4010_manual.py.diff、pmp4010_simple.py.diff。测试没有归档副本，提供新增测试清单与最终hash，不声称存在可验证的测试baseline diff。
