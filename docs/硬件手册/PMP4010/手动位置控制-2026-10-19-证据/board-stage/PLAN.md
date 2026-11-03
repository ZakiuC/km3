# 隔离部署准备

准备完成后已按父会话精确授权执行；最终结果见 final-summary.json。复制来源及 SHA 见 preparation.json。

收到父会话精确归档路径、完整 SHA256、唯一新 /var/tmp 目录及冻结 API 后，先调整并本地编译脚本，确认归档 SHA 和清单。只使用新隔离目录，不写正式 current、配置或服务。

单次执行顺序：before-passive → stage_final.py --apply（上传一次、清单验证、18 imports / ABI1 / 33+33+14、manual/simple imports、parser/controller 构造、CLI help、sh -n）→ after-passive → 比较 11 项被动快照 → 证据 SHA 清单。失败即停止，不自动 SSH 重试；保留原始 argv/rc/stdout/stderr。

纯加载预期：--manual-linear 未给 mm_per_motor_rev 时未知；显式 Fraction(4) 使用用户提供的 4 mm/转，通用未配置分支仍为 None；默认 velocity=1000、step_mm=0.1；旧 --manual 保留 angle 1°。仅 parser/controller 构造，禁止 scan、load_device_json、CAN/socket 写入和 App.run。禁止真实 TUI、SDO、运动和驱动实时状态查询。用户自行测量机械。

脚本已按冻结 API 更新；归档只上传一次，未启动真实手动程序。
