"""Local MockBus evidence only. Never opens a CAN socket or network client."""
import concurrent.futures
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
TOOLS = ROOT / 'device/drivers/pmp4010/tools'
BASE = ROOT / 'docs/硬件手册/PMP4010/报警内容显示-2026-10-19-证据/package/payload/drivers/pmp4010'

def run(pattern):
    command = [sys.executable, '-I', '-m', 'unittest', 'discover', '-s', str(TOOLS.relative_to(ROOT)), '-p', pattern]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    name = pattern.removesuffix('.py')
    (OUT / (name+'.stdout')).write_text(result.stdout)
    (OUT / (name+'.stderr')).write_text(result.stderr)
    return dict(command=command, returncode=result.returncode,
                stdout=name+'.stdout', stderr=name+'.stderr')

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(run, ['_test_manual.py','_test_simple.py']))
(OUT/'focused-checks.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
if any(row['returncode'] for row in results):
    print(json.dumps(results, ensure_ascii=False)); sys.exit(1)

hashes = {}
for name in ('pmp4010_manual.py','pmp4010_simple.py','_test_manual.py','_test_simple.py'):
    path = TOOLS/name
    hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if (BASE/name).exists():
        diff = ''.join(difflib.unified_diff((BASE/name).read_text().splitlines(True),path.read_text().splitlines(True),
            fromfile='7355-payload/'+name,tofile='current/'+name))
        (OUT/(name+'.diff')).write_text(diff)
(OUT/'source-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')

sys.path.insert(0,str(TOOLS))
from _test_simple import ResetTests
from pmp4010_simple import SimpleApp
for mode in ('success','lost-high-ack','bad-final-controlword'):
    case = ResetTests()
    case.setUp()
    try:
        review = case.review()
        if mode=='success':
            app=SimpleApp(case.ctl.tool,case.args)
            app.controller=case.ctl
            app.rows,app.cols=24,80
            app.action='reset'; app.pending=review; app.current_view='confirm'
            (OUT/'mock-confirmation-80x24.txt').write_text('\n'.join(app.build().to_lines())+'\n')
        elif mode=='lost-high-ack':case.node.fail_download=2
        else:case.node.behavior='cw'
        try:
            case.ctl.execute_reset(review,case.out)
        except BaseException as exc:
            if mode=='success':raise
            error=type(exc).__name__+': '+str(exc)
        else:
            error=None
        log=next(Path(case.tmp.name).glob('*reset-*.jsonl'))
        shutil.copyfile(log,OUT/('mock-'+mode+'-reset.jsonl'))
        trace = dict(mode=mode, error=error, local_mock_only=True,
            downloads=case.node.downloads, client_quarantined=case.ctl.manual_sessions[1].client.quarantined,
            action_locked=bool(case.ctl.blocked), source_client_count=case.ctl.tool._std_open_active.call_count,
            wire=[dict(id=f.arbitration_id,data=f.data.hex()) for f in case.bus.sent])
        (OUT/('mock-'+mode+'-trace.json')).write_text(json.dumps(trace,indent=2)+'\n')
    finally:
        case.doCleanups()
print(json.dumps(results,ensure_ascii=False))
print(json.dumps(hashes,indent=2))
