"""Read/import/math/help only; no manual run, scan, transport, or device writes."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import platform
import runpy
import sys

sys.dont_write_bytecode = True
root = Path(sys.argv[1]).resolve(strict=True)
assert platform.machine() == 'aarch64', 'board native math must execute on AArch64'
attempts = []
default_check_active = False

def audit(event, args):
    forbidden = event in ('socket.__new__','socket.connect','socket.bind','subprocess.Popen',
        'os.system','os.fork','os.forkpty','os.posix_spawn','os.exec','pty.spawn',
        'os.remove','os.rename','os.mkdir','os.rmdir','os.symlink','os.link',
        'os.truncate','os.chmod','os.chown')
    if event == 'open':
        mode, flags = args[1:3]
        forbidden = (isinstance(mode,str) and any(x in mode for x in 'wax+')) or (
            isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
    if forbidden:
        attempts.append(event)
        raise RuntimeError('manual pureload audit rejected '+event)

def reject_manual_run(frame, event, arg):
    if event == 'call' and default_check_active:
        filename = Path(frame.f_code.co_filename).name
        name = frame.f_code.co_name
        if ((filename in ('pmp4010_bench.py','pmp4010_manual.py','pmp4010_simple.py') and name not in ('__init__','_empty','<listcomp>','<dictcomp>','<genexpr>') and not (filename == 'pmp4010_simple.py' and name in ('positive','signed'))) or
                (filename == 'pmp4010.py' and name in ('load_device_json','_std_open_active'))):
            attempts.append('default_check_controller_io:'+filename+':'+name)
            raise RuntimeError('default check forbids controller operations')
    if event == 'call' and frame.f_code.co_name == 'run' and Path(frame.f_code.co_filename).name in ('pmp4010_manual.py','pmp4010_simple.py','pmp4010_tui.py'):
        attempts.append('manual_app_run')
        raise RuntimeError('simple/manual pureload forbids app.run')
    return reject_manual_run

sys.addaudithook(audit)
sys.settrace(reject_manual_run)
sys.argv = [str(root/'load_smoke.py'),'--check-missing-library']
runpy.run_path(sys.argv[0],run_name='__main__')
sys.path[:0] = [str(root/'drivers/pmp4010'),str(root/'hwtui'),str(root)]
os.environ.pop('PMP4010_VELOCITY_LIBRARY',None)
manual = importlib.import_module('pmp4010_manual')
module_path = Path(manual.__file__).resolve(strict=True)
assert module_path.is_relative_to(root), 'manual import escaped package'
print(json.dumps(dict(manual_import='PASS',path=str(module_path),
                     sha256=hashlib.sha256(module_path.read_bytes()).hexdigest())))
simple = importlib.import_module('pmp4010_simple')
simple_path = Path(simple.__file__).resolve(strict=True)
assert simple_path.is_relative_to(root), 'simple import escaped package'
print(json.dumps(dict(simple_import='PASS',path=str(simple_path),
                     sha256=hashlib.sha256(simple_path.read_bytes()).hexdigest())))
faults = importlib.import_module('pmp4010_faults')
assert Path(faults.__file__).resolve(strict=True).is_relative_to(root)
fault_cases = {0x2312: '0x2312（电机堵转）', 0: '0x0000（无错误码）', None: '未知', 0xABCD: '0xABCD（未知报警）'}
for code, expected in fault_cases.items():
    assert faults.format_fault(code) == expected
print(json.dumps(dict(faults_display_check='PASS',code_2312=faults.format_fault(0x2312),zero=faults.format_fault(0),none=faults.format_fault(None),unknown=faults.format_fault(0xABCD),real_alarm_read=False),ensure_ascii=False))
# Constructors inspected: Bench -> Manual -> Simple initializes memory only.
# Run the actual packaged parser and constructors under the same audit guard.
from fractions import Fraction
tool = importlib.import_module('pmp4010')
assert Path(tool.__file__).resolve(strict=True).is_relative_to(root)
default_check_active = True
args_default = tool.build_parser().parse_args(['--manual'])
controller_default = simple.SimpleController(tool, args_default)
assert args_default.manual_velocity is None
assert not controller_default.linear
assert controller_default.manual_velocity == controller_default.next_velocity == 1000
assert controller_default.step_degrees == Fraction('0.1')
assert controller_default.range_degrees == Fraction(1)
assert controller_default.mm_per_motor_revolution is None
args_unknown = tool.build_parser().parse_args(['--manual','--manual-linear'])
controller_unknown = simple.SimpleController(tool, args_unknown)
assert args_unknown.manual_mm_per_rev is None
assert controller_unknown.linear
assert controller_unknown.mm_per_motor_revolution is None
assert controller_unknown.software_zero is None
assert controller_unknown.step_mm == Fraction('0.1')
assert controller_unknown.manual_velocity == controller_unknown.next_velocity == 1000
args_configured = tool.build_parser().parse_args(['--manual','--manual-linear','--manual-mm-per-rev','4'])
controller_configured = simple.SimpleController(tool, args_configured)
assert controller_configured.linear
assert isinstance(controller_configured.mm_per_motor_revolution, Fraction)
assert controller_configured.mm_per_motor_revolution == Fraction(4)
assert controller_configured.software_zero is None
assert controller_configured.step_mm == Fraction('0.1')
assert controller_configured.manual_velocity == controller_configured.next_velocity == 1000
args_override = tool.build_parser().parse_args(['--manual','--manual-velocity','100'])
controller_override = simple.SimpleController(tool, args_override)
assert controller_override.manual_velocity == controller_override.next_velocity == 100
assert controller_override.step_degrees == Fraction('0.1')
for controller in (controller_default,controller_unknown,controller_configured,controller_override):
    assert controller.bus is None and controller.context is None
    assert not controller.manual_sessions and not controller.refresh_clients and not controller.scan_clients
    assert not controller.proofs and not controller.rt_ack and controller.od_reason is None
default_check_active = False
print(json.dumps(dict(default_parameter_check='PASS',default_parser_manual_velocity=args_default.manual_velocity,
    manual_velocity=controller_default.manual_velocity,next_velocity=controller_default.next_velocity,
    legacy_range_degrees=str(controller_default.range_degrees),step_degrees=str(controller_default.step_degrees),
    unknown_linear_mm_per_motor_revolution=controller_unknown.mm_per_motor_revolution,
    configured_linear_mm_per_motor_revolution=str(controller_configured.mm_per_motor_revolution),
    linear_step_mm=str(controller_configured.step_mm),linear_default_velocity=controller_configured.manual_velocity,
    explicit_override_manual_velocity=controller_override.manual_velocity,
    explicit_override_next_velocity=controller_override.next_velocity,controller_bus_opened=False,
    controller_scan_called=False,load_device_json_called=False,forbidden_attempts=len(attempts))))
sys.argv = [str(root/'drivers/pmp4010/pmp4010.py'),'--help']
help_output = io.StringIO()
with contextlib.redirect_stdout(help_output):
    try:
        runpy.run_path(sys.argv[0],run_name='__main__')
    except SystemExit as exc:
        assert exc.code in (0,None), 'CLI --help did not exit successfully'
    else:
        raise RuntimeError('CLI --help unexpectedly returned without parser exit')
text = help_output.getvalue()
assert text.strip(), 'CLI --help output empty'
print('CLI_HELP_BEGIN\n'+text+'CLI_HELP_END')
assert not attempts, 'forbidden operations attempted'
print(json.dumps(dict(result='PASS',manual_import=True,simple_import=True,cli_help=True,
                     forbidden_IO_write_or_run_attempts=len(attempts),
                     real_manual_app_started=False,CAN_opens=0,CAN_sends=0)))
