import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,'/home/zakiu/km3/device/drivers/pmp4010/tools')
from _test_simple import SimpleTests
from _test_manual import DelayedManualNode
from _test_bench_support import DelayedBus,tool
from pmp4010_simple import SimpleController,SimpleApp
output=Path(sys.argv[1])
output.mkdir(parents=True,exist_ok=True)
test=SimpleTests('test_locked_UI_marks_values_historical_and_first_bad_raw')
test.setUp()
try:
    test.node=DelayedManualNode()
    test.node.transition_sequences[0x106]=[0x237,0x231]
    test.bus=DelayedBus([test.node])
    tool._std_open_active.return_value=test.bus
    test.ctl=SimpleController(tool,test.args)
    test.addCleanup(test.ctl.close)
    test.ready()
    try:
        test.action('forward')
        raise AssertionError('unexpected state was accepted')
    except ValueError as exc:
        print(str(exc))
    app=SimpleApp(tool,test.args)
    app.controller=test.ctl
    app.rows,app.cols=24,80
    app.current_view='main'
    app.poll()
    (output/'locked-80x24.txt').write_text('\n'.join(app.build().to_lines()))
    for path in Path(test.tmp.name).glob('*.jsonl'):
        shutil.copy2(path,output/path.name)
    diagnostic=test.ctl.failure_diagnostics[1]
    assert diagnostic['raw_statusword']==0x237
    assert test.controls()==[0x106,2,0]
    print(json.dumps(dict(first_failure=diagnostic,controls=test.controls(),quarantine_reason=test.ctl.row(1)['quarantine_reason']),ensure_ascii=False))
finally:
    test.doCleanups()
