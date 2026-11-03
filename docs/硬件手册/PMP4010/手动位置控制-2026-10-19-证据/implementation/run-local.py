"""Local implementation verification: no hardware, SSH, deployment or full gates."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

root=Path('/home/zakiu/km3')
out=Path(__file__).resolve().parent
paths=[root/'device/drivers/pmp4010/tools'/name for name in (
    'pmp4010_simple.py','pmp4010_manual.py','pmp4010.py','_test_simple.py',
    '_test_simple_pty.py','_test_manual.py','_test_manual_pty.py')]
def hashes():
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before=hashes()
(out/'source-before-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
commands=[
    ('simple-unit',['python3','-I','-B','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_simple.py']),
    ('manual-unit',['python3','-I','-B','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_manual.py']),
    ('public-angle-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run',str(out/'public-angle-pty')]),
    ('public-linear-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-linear',str(out/'public-linear-pty')]),
    ('staged-manual-pty',['python3','-I','-B','device/drivers/pmp4010/tools/_test_manual_pty.py','--run',str(out/'staged-manual-pty')]),
    ('help',['python3','-I','-B','device/drivers/pmp4010/tools/pmp4010.py','--help']),
    ('diff-check',['git','diff','--check','--']+[str(p.relative_to(root)) for p in paths]),
]
index=[]
for name,argv in commands:
    row=dict(name=name,argv=argv,cwd=str(root),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
             stdout=name+'.stdout.log',stderr=name+'.stderr.log')
    with (out/row['stdout']).open('wb') as stdout,(out/row['stderr']).open('wb') as stderr:
        result=subprocess.run(argv,cwd=root,stdout=stdout,stderr=stderr)
    row.update(rc=result.returncode,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    index.append(row)
    (out/(name+'.command.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    (out/'command-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n')
    print(name,result.returncode,flush=True)
after=hashes()
(out/'source-final-sha256.json').write_text(json.dumps(after,indent=2)+'\n')
summary=dict(source_unchanged=before==after,commands=len(index),all_rc0=all(r['rc']==0 for r in index),
    source_files=len(after),limits='Local unit and real curses MockBus PTY only; no hardware/SSH/deployment/full gate runs')
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary),flush=True)
raise SystemExit(0 if summary['source_unchanged'] and summary['all_rc0'] else 1)
