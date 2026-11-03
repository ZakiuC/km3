"""仅供根审后的独立现场任务通过stdin运行；本文件未执行。"""
import hashlib
from pathlib import Path
import runpy
import sys
sys.dont_write_bytecode = True
PROPOSAL = Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/motion-compare-proposal')
EXPECTED = {'pinned_payload.py': 'd8d99ccffdf1b1cb3fc02669af2627243f9801b4fe8861cc39f18692af954a62', 'motion_compare_once.py': 'e530b81f231d3ccccca5df741ccc0f79ec410b9cf5569e517d14428ca7151202'}
if PROPOSAL.resolve(strict=True) != PROPOSAL:
    raise ValueError('诊断脚本目录变化或符号链接')
for name, expected in EXPECTED.items():
    path = PROPOSAL/name
    if path.resolve(strict=True).parent != PROPOSAL or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('已审阅运行文件变化：'+name)
sys.argv = ['motion_compare_once.py', '--execute-once',
            '--od-session-ack', '接受本会话OD缺口和独立停机责任',
            '--motion-confirm', '确认仅一次相对-10mm_100rpm_低加速度对照']
runpy.run_path(str(PROPOSAL/'motion_compare_once.py'), run_name='__main__')
