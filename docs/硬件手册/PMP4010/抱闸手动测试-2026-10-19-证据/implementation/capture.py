#!/usr/bin/env python3
"""Offline CLI/render/hash evidence only; every controller uses MockBus."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p/'device/drivers/pmp4010/tools/pmp4010_brake.py').is_file())
OUT = Path(__file__).resolve().parent
TOOLS = ROOT/'device/drivers/pmp4010/tools'
sys.path.insert(0, str(TOOLS))
from _test_brake import BrakeTests


def command(name, argv, expected):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    (OUT/(name+'.stdout')).write_text(result.stdout)
    (OUT/(name+'.stderr')).write_text(result.stderr)
    (OUT/(name+'.json')).write_text(json.dumps(dict(command=argv, cwd=str(ROOT), returncode=result.returncode), indent=2))
    assert result.returncode == expected, (name, result.returncode, result.stderr)
    print('{}: rc={}'.format(name, result.returncode))


command('help', ['python3','-I',str(TOOLS/'pmp4010_brake.py'),'--help'], 0)
command('no-tty', ['python3','-I',str(TOOLS/'pmp4010_brake.py')], 2)
command('import-no-can', ['python3','-I','-c',
    "import socket,sys; socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('CAN forbidden')); sys.path.insert(0,"+repr(str(TOOLS))+"); import pmp4010_brake; print('import OK; socket opens=0')"], 0)

case = BrakeTests('test_80x24_screen_distinguishes_electrical_request_and_mechanics')
case.setUp()
try:
    app = case.app('rt')
    def screen(name):
        (OUT/(name+'-80x24.txt')).write_text('\n'.join(app.build().to_lines())+'\n')
    screen('startup')
    case.ready()
    for name in ('authorize','main','help'):
        app.current_view = name
        screen(name)
    app.pending = case.ctl.review_action('hold', case.out)
    app.action, app.current_view = 'hold', 'confirm'
    screen('hold-confirm')
    case.ctl.execute_action(app.pending, case.out)
    app.current_view = 'main'
    screen('enabled-request')
    case.ctl.safety_action('disable', case.out)
    screen('disabled-request')
    case.node.set_value(0x603f, 0x2312)
    case.ctl.refresh(case.out)
    screen('first-failure-2312')
    case.ctl.close()
    (OUT/'mock-requests.json').write_text(json.dumps(dict(
        requests=__import__('_test_bench_support').requests(case.bus),
        controls=case.controls(), mechanical_brake_confirmed=False,
        no_real_can=True, no_network=True), indent=2))
finally:
    case.doCleanups()

files = [TOOLS/name for name in ('pmp4010_brake.py','_test_brake.py','pmp4010_manual.py','pmp4010_simple.py')]
(OUT/'source-sha256-final.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}, indent=2)+'\n')
print('eight 80x24 screens including first failure + actual MockBus requests + four source SHA256 saved')
