# Python 手动直线速度 mm/s 本地实现证据

仅修改本目录 before/ 保存的四个源码/测试文件；before 是本次编辑前的脏工作树副本，不代表 git HEAD。对应 *.diff 为本次前后差异。

- linear 的 s 使用正有限数/分数 mm/s，先精确换算 rpm，再调用现有 C++ 唯一实现的 position.velocity_to_unit；内部 U/s、6081 和 CiA402 没有更改。
- 未输入 mm/s 请求时显示“设置 ... mm/s（... U/s）”。保留默认1000 U/s和显式 --manual-velocity；不提高默认物理速度。
- 请求/量化实际速度并列显示；实际反馈依2006:12先变rpm再变mm/s，保留方向。缺比例/标定或未知反馈单位明确不可换算；禁止设置速度。
- c换算仅改变软件比例、清旧mm/s请求并取消审阅；不改变next_velocity/manual_velocity，不下载速度。requested_mm_s参与审阅封印及执行前校验。
- 其余动作、范围、使能、快停、故障、隔离与受限复位守卫保留。角度模式继续rpm。

本地独立数字：4mm/转、608F=131072/1、6091=1/1时，默认1000U/s=0.0305176mm/s；1mm/s=15rpm=32768U/s（mock607F显式设100000，设备本身若上限更低仍拒绝）；0.1mm/s量化3276U/s=0.0999756mm/s。非1齿轮比3:1时，1mm/s量化10922U/s。

simple-initial.log保留初次测试失败：新增固定1mm/s用例超过原mock607F=10000导致3 errors；调整这些测试自己的mock上限为100000后通过，生产限值检查未更改。

验证命令、实测退出码/数量及冻结hash见source-freeze-input.json。linear-pty-final/保留真实80x24 curses画面、原始PTY transcript、motion.jsonl和MockBus trace，零真实socket/CAN。PTY沿用360mm/转 mock测程以保留既有五段运动覆盖，s=1mm/s量化364U/s，显示实际0.999756mm/s；不是机器标定。4mm/转精确数值由_test_simple.py的独立固定断言验证。

本任务未SSH、未访问板端、未发真实CAN、未改正式driver/OD、未改C++示例、未commit/push。所有证据有意保留，无临时进程应驻留。板端部署由既有部署worker接手，硬件运动验收不属于本地结论。
