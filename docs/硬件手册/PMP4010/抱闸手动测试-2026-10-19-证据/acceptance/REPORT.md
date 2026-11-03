# 抱闸手动听声工具本地验收

本目录仅执行本地 Python 单元测试、静态闸门、生成器核对和真实 curses 伪终端的标准 MockBus 验证。未访问 SSH、真实 CAN、硬件、部署或提交；未重新构建或重复测试未变动的 C++。本次基线完整 Python 435 项，加新抱闸22项，最终为457项；共享 hwtui217项、tools8项。

预期行为：启动仅连接/读取，经独立会话授权后，1/e 审阅且 Enter 才提交 Halt 保持；2/d 立即失能；运动按键无效；q 沿用共享 Shutdown（6040h=0006，bit2=0）并确认电气失能。软件反馈不能证明机械抱闸或实际负载安全。

初始冻结版本：完整 PMP4010 454/454（45.394s），共享 hwtui 217/217，tools 8/8，均 rc=0。每次运行前后四文件 SHA256 与 implementation/source-sha256-final.json 对应的初始清单一致，原始清单也嵌于每条 raw/*.json，避免后续冻结更新混淆。

初次80×24 PTY已实际走完两次保持、立即失能和退出，但验收脚本错误要求 q 最后 CW=0000；实际继承清理 CW=0006，子进程 rc=0、总线关闭、termios 恢复、无运动或 Go。该验收 oracle 失败 rc=1 保留在 raw/brake-pty.* 与 raw/pty-v1-failed/；随后仅修正证据脚本，增加退出失能反馈及无效键零写入的独立断言。

最终冻结版本完整 PMP4010 457/457（45.693s，rc=0），包含新增3项故障显示测试；前后四文件哈希均与最终冻结清单相同。最终真实 80×24 PTY rc=0，9张逐列解码屏，termios 完整恢复、MockBus关闭、两次独立审阅/Enter保持、2立即失能，q退出反馈确认电气失能；8个无效运动/设置按键无新增帧，未出现002F/003F Go、使能态位置触发沿或运动样本。子进程审计钩子禁止真实socket及subprocess。详见 raw/pty-final/summary.json、trace.json、screens.txt、terminal.raw。最终包与说明已完成更新；文档最后闸门原样命令为 `bash tools/check-docs.sh`，实际退出码及完整输出以随后产生的 [raw/check-docs.json](raw/check-docs.json)、[stdout](raw/check-docs.stdout)、[stderr](raw/check-docs.stderr) 为准。该闸门在本报告与包最终更新后执行，避免以预写文字代替实际结果。

| 证据标签 | 原样命令 | 实际 rc | 耗时(s) |
| --- | --- | --- | --- |
| brake-pty-final | `python3 -I 'docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/acceptance/verify_brake_pty.py'` | 0 | 3.457 |
| brake-pty | `python3 -I 'docs/硬件手册/PMP4010/抱闸手动测试-2026-10-19-证据/acceptance/verify_brake_pty.py'` | 1 | 3.477 |
| check-deps | `bash tools/check-deps.sh` | 0 | 0.018 |
| check-drivers | `bash tools/check-drivers.sh` | 0 | 0.034 |
| check-manual-citations | `bash tools/check-manual-citations.sh` | 0 | 0.126 |
| gen-canopen | `python3 -I tools/hwdriver/gen-canopen.py --check` | 0 | 0.022 |
| gen-od | `python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check` | 0 | 0.026 |
| gen-pmp4010-access | `python3 -I tools/hwdriver/gen-pmp4010-access.py --check` | 0 | 0.023 |
| hwtui | `python3 -I -m unittest discover -s tools/hwtui -p 'test_*.py' -v` | 0 | 0.423 |
| pmp4010-final | `python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py' -v` | 0 | 45.766 |
| pmp4010 | `python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py' -v` | 0 | 45.468 |
| tools | `python3 -I -m unittest discover -s tools/tests -p 'test_*.py' -v` | 0 | 0.356 |

每条记录的 raw/<标签>.json 包含 UTC 起止时间、原样 argv、实际 rc、前后源码 SHA256 和冻结清单；同名 .stdout/.stderr 保留完整原始输出。run.py 仅执行传入的字面 argv，未通过变量组装闸门命令。

依赖闸门 rc=0，但保留原始提示：Python3 未在映射表中，1 个需人工确认；本结果不将该提示改写为全部依赖已确认。

C++ 本次未重复测试；引用此前同轮已完成的原始验收：[Host CTest](../../报警根因诊断-2026-10-19-证据/product-acceptance/04-host-ctest.stdout)、[ASan CTest](../../报警根因诊断-2026-10-19-证据/product-acceptance/06-asan-ctest.stdout)、[TSan CTest](../../报警根因诊断-2026-10-19-证据/product-acceptance/08-tsan-ctest.stdout)，三者各17/17。原始命令、rc和时间在对应 .command.json/.result.json/.rc 中；不把引用结果表述为本次重新执行。

三项前置闸门与三项生成器核对均在初始新抱闸源码冻结（9f66f210/4d114741）时逐一执行，rc=0；随后仅新抱闸TUI显示及3项对应单元测试更新为最终 a7fdc134/938a14db，既有manual/simple两文件始终未变。最终457项完整测试和真实PTY则严格核对最终四文件冻结；每条结果保留自身运行时哈希边界，未把旧清单冒充最终清单。
