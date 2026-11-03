"""Replay real read-only values locally; never creates a bus or executes."""
import importlib.util
import json
from pathlib import Path
import sys
import time
sys.dont_write_bytecode=True
ROOT=Path('/tmp/km3-pp-preflight-20261017')
spec=importlib.util.spec_from_file_location('preflight',ROOT/'preflight.py')
pre=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)
tool,bench,position=pre.load(Path('/tmp/km3-pmp4010-board-20261017-csvguard-final-package/payload'))
rows=[json.loads(s) for s in (ROOT/'board-live-readonly.stdout').read_text().splitlines()]
values=next(r['values'] for r in rows if r['event']=='snapshot')
result=next(r for r in rows if r['event']=='preflight_result')
args=tool.build_parser().parse_args(['--node','1','--node-range','1','--soft-min','-1465','--soft-max','-1429','--max-displacement','36'])
controller=bench.BenchController(tool,args)
controller.rt_ack=True
controller.od_reason='用户在本会话确认现场安全并授权一次小角度验证；仅本次PP1约0.1°，既有7项缺口保留'
controller.context=('can1',1000000)
controller.od,controller.objects=pre.objects(tool,position)
controller.specs=position.specifications(controller.od)
controller._put(dict(node=1,online=True,lost=False,quarantined=False,values=values,
                     calibration=result['calibration'],updated=time.monotonic(),name='NiMotion Servo',firmware='V3.3'))
try:
    controller.review('relative',1,36,pre.Output())
except ValueError as exc:
    assert str(exc)=='电机未确认静止：target reached 未置位',str(exc)
    pre.emit('review_expected_rejection',message=str(exc),source='local_replay_of_recorded_device_values',
             live_can_opens=0,live_can_sends=0,execute_calls=0,
             target=-1429,delta=36,soft_min=-1465,soft_max=-1429,max_displacement=36,
             parameter_values_are_for_local_replay_only=True)
else:
    raise AssertionError('unchanged BenchController unexpectedly accepted bit10=0')
