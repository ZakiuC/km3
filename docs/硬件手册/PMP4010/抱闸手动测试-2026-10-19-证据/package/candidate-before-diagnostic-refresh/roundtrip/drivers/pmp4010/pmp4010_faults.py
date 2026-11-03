"""Display-only 603Fh descriptions from E06 manual section 9.1, table 9-1.

603Fh is the last error's low 16 bits, not a current Fault indicator or a
physical-cause diagnosis. Do not infer configured reactions or read more OD
objects here. Match the full supplied value: masking could mislabel bad data.
"""

FAULT_DESCRIPTIONS = {
    0x2300: '电机过流',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x2311: '电机过载',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x2312: '电机堵转',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x3210: '电源过电压',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x3220: '电源欠电压',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x3211: '过泄压值故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x3285: '输出相故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x4210: '温度过高报警',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x4220: '温度过低报警',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5080: '驱动器故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5540: 'Flash 操作故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5541: 'Flash 初始化故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5542: 'Flash 校验错误警告',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5543: 'Flash 用户区无参数',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5544: '掉电位置存储异常',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x5545: '掉电保存数据未存储上',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x6000: '硬件初始化故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x6320: '参数设置错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x6321: '注册故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7301: '编码器电池低压故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7302: '编码器电池欠压报警',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7303: '编码器通信故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7304: '编码器多圈计数错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7305: 'Z 脉冲故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7306: '编码器故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7307: '编码器警告',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7309: '编码器电池失效故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7310: '超速',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7501: '通信故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7502: 'CAN 被动错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x7503: 'CAN 接收溢出',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8610: '原点回归超时',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8611: '位置超差',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8613: '软件限位错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8614: '限位开关错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8700: '同步故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8615: '曲线规划计算错误',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8616: '目标位置溢出',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0x8617: '曲线规划参数过小',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF01: '电机参数识别故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF02: '不能执行的命令',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF05: 'STO 1 关闭失败',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF06: 'STO 1 使能失败',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF07: 'STO 2 关闭失败',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF08: 'STO 2 使能失败',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF09: 'STO 输入异常故障',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF0A: 'STO 使能状态',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF0B: '非安全状态',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
    0xFF0C: '编码器校准超时',  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:9.1 表9-1
}


def fault_description(code):
    if code is None:
        return '未知'
    if code == 0:
        return '无错误码'
    return FAULT_DESCRIPTIONS.get(code, '未知报警')


def format_fault(code):
    return '未知' if code is None else '0x{:04X}（{}）'.format(code, fault_description(code))


def fault_fields(code):
    """Add display metadata without changing the original numeric sample."""
    return dict(fault_code=code,
                fault_code_hex=None if code is None else '0x{:04X}'.format(code),
                fault_description=fault_description(code))
