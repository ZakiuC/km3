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
    if event == 'call' and frame.f_code.co_name == 'run' and Path(frame.f_code.co_filename).name in ('pmp4010_manual.py','pmp4010_simple.py'):
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
