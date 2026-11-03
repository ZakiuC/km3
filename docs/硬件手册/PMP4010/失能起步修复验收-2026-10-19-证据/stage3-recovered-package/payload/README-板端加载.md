# PMP4010 stage3 断电后重建临时包

阶段：`disabled_start_small_move_stage3_recovered`。本轮14项冻结清单SHA256：`be1bd12c248b12553229b2e39a02d2d6f98591e053c2321d956fed2d2e570846`。

保留stage2 B未Operation enabled当前设定点预装核证分支；stage3小动作先预装当前起点，再012F保持Halt使能并严格检查位置不变与零速，随后按新目标握手与守卫执行。具体流程、边界与预算由源码和TUI冻结审阅显示；本包本地纯加载不证明固件行为或实际位移通过。

阶段一010F使能后+12U微位移失败原始证据保留。阶段二未使能preload已现场成功，不能替代stage3真实motion验收。断电前stage3临时manifest、归档与本地原输出丢失；本包为源SHA核对后的全新重建，不能声称找回旧包。

白名单：7个hwtui模块、6个型号工具模块、OD、共享向量、ARM库及2链接、纯加载自检与本说明。精确hash见payload-manifest.json和包外source-manifest.json。本地host自检显式使用host库；ARM库仅静态检查。

经父会话协调的板端隔离目录纯加载命令：

```sh
python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library
```

未传--library时加载同包ARM库。此包不自动安装、不改服务或current。
