from pathlib import Path
import json
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, '/home/zakiu/km3/device/drivers/pmp4010/tools')
from _test_prepare import PrepareTests
out = Path(__file__).parent
for label, fail in [('nominal', False), ('lost-first-ack', True)]:
    test = PrepareTests('test_bit10_zero_success_exact_two_writes_and_no_exit_control')
    test.setUp()
    try:
        review = test.prepared()
        if fail:
            test.node.drop_write_ack.add((test.ctl.specs['target_position']['index_value'], 0))
        error = None
        try:
            test.ctl.execute(review, test.out)
        except Exception as exc:
            error = dict(type=type(exc).__name__, message=str(exc))
        (out / (label + '.jsonl')).write_bytes(Path(test.args.motion_log).read_bytes())
        count = len(test.bus.sent)
        test.ctl.close()
        trace = dict(source='isolated MockBus only', error=error,
                     real_can_open=0, enabled_sessions=len(test.ctl.enabled_sessions),
                     exit_frames=len(test.bus.sent)-count,
                     requests=[dict(id=f.arbitration_id,data=f.data.hex()) for f in test.bus.sent])
        (out / (label + '.wire.json')).write_text(json.dumps(trace, ensure_ascii=False, indent=2)+'\n')
        print(label, 'requests=', len(test.bus.sent), 'error=', error, 'exit_frames=',trace['exit_frames'])
    finally:
        test.doCleanups()
