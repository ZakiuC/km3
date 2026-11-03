# 手动界面重设计：本地冻结交付

实际生产 `SimpleApp` 已完成中文主屏、独立输入/冻结动作确认、可翻阅帮助/诊断、固定快停/安全退出。新增纯呈现模块 `pmp4010_simple_view.py`，共享TUI仅增加默认字节兼容的 `render_frame` 钩子。正常数值使用终端默认前景加粗，NO_COLOR/TERM=dumb回到共享纯文本渲染。

生产修改：`pmp4010_simple.py`、`pmp4010_simple_view.py`、`tools/hwtui/tui.py`。测试修改：`_test_simple.py`、`_test_simple_pty.py`、`tools/hwtui/test_tui.py`。

`SimpleController` 类段before/after逐字节相同，SHA256 `9c78e08ea5228541881bacdc404490bd92d29742fde63f23ff0729cf2c498fe6`。原hwtui测试AST完整保留（包括61字节断言），仅新增默认钩子兼容测试。

最终命令与结果（完整命令、原日志和起止hash见 `freeze.json`）：

- PMP工具Python：`python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p "_test_*.py"`，rc=0，472 tests / 50.775s / OK。
- hwtui Python：`python3 -I -m unittest discover -s tools/hwtui -p "test_*.py"`，rc=0，218 tests / OK。
- 生产SimpleApp + 标准MockBus真实PTY：`_test_simple_pty.py --run-visual .../pty-80x24`，rc=0，35项检查；有色80x24、120x32/64x20/80x24 resize、输入/确认、故障、诊断/帮助分页、停止与退出。
- 生产SimpleApp + 标准MockBus真实PTY：`_test_simple_pty.py --run-visual-small .../pty-64x20-no-color`，rc=0，35项检查；无色64x20启动、resize、真实SDO读超时隔离、零重试、保护页停留。
- 两套PTY均断言完整termios恢复、cursor恢复、应用SGR策略、无目标/轮廓/回零/保存下载；原f/b/e动作PTY仍由完整472套件覆盖。

早期旧布局断言失败、一次小屏footer断言失败及父线程请求颜色调整时中断的原日志均保留。`freeze-failed-before-final.json` 是历史失败记录，最终唯一交付清单为 `freeze.json`。

`implementation.patch` 对照本轮前真实dirty/untracked副本生成，不能用git基线代替。`previews-final/`是生产Grid+MockBus；其source-hashes与最终freeze一致。`pty-*/`含实际终端转义流、屏幕JSON/文字、SDO trace及summary。

未SSH、未硬件操作、未部署、未修改正式driver/OD/示例C++/架构文档、未commit/push。实物机械停止/抱闸/运动验收不在本次本地结论内。低于64x20明确降级提示并不接收操作确认，Space/x/q始终优先。

本worker拥有的临时日志/目录已清理，证据目录有意保留；无遗留测试子进程。打包部署交由delivery worker。
