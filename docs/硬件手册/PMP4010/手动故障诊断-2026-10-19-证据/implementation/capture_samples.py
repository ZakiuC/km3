"""Save MockBus diagnostics and one 80x24 screen; never opens a real bus."""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO/'device/drivers/pmp4010/tools'))
from _test_manual import ManualTests
from _test_simple import SimpleTests
from _test_bench_support import requests
from pmp4010_simple import SimpleApp

for scenario in ('initial', 'running', 'screen'):
    case = SimpleTests() if scenario == 'screen' else ManualTests()
    case.setUp()
    try:
        if scenario == 'running':
            case.submit()
            case.step('enable')
            case.step('go')
            case.bus.sent.clear()
            case.node.set_value(0x603f, 0x3456)
            case.ctl.refresh(case.out)
        else:
            case.node.set_value(0x603f, 0xabcd)
            case.ctl.rt_ack = True
            case.ctl.scan(case.out)
        if scenario == 'screen':
            app = SimpleApp(case.ctl.tool, case.args)
            app.controller = case.ctl
            app.rows, app.cols = 24, 80
            app.current_view = 'main'
            app.poll()
            (HERE/'sample-80x24.txt').write_text('\n'.join(app.build().to_lines())+'\n', encoding='utf-8')
        else:
            (HERE/('sample-'+scenario+'.jsonl')).write_bytes(Path(case.ctl.diagnostic_paths[1]).read_bytes())
            (HERE/('sample-'+scenario+'-requests.json')).write_text(json.dumps(requests(case.bus),indent=2)+'\n',encoding='utf-8')
    finally:
        case.doCleanups()
