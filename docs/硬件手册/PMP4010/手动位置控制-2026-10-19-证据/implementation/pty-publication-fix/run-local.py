import datetime
import hashlib
import json
from pathlib import Path
import subprocess

root=Path('/home/zakiu/km3')
out=Path(__file__).resolve().parent
old=json.loads((out.parent/'source-final-sha256.json').read_text())
def hashes():
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in old}
before=hashes()
changed=[p for p in old if old[p]!=before[p]]
assert changed==['device/drivers/pmp4010/tools/_test_simple_pty.py'],changed
(out/'source-before-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
commands=[
    ('publication-contract',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','SnapshotPublicationTests']),
    ('pty-module-unit',['python3','-I','-B','-m','unittest','discover','-s','device/drivers/pmp4010/tools','-p','_test_simple_pty.py']),
    ('public-linear-1',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-linear',str(out/'public-linear-1')]),
    ('public-linear-2',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run-linear',str(out/'public-linear-2')]),
    ('public-angle',['python3','-I','-B','device/drivers/pmp4010/tools/_test_simple_pty.py','--run',str(out/'public-angle')]),
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
summary=dict(changed_source_files=changed,source_unchanged_during_checks=before==after,
    all_new_checks_rc0=all(r['rc']==0 for r in index),commands=len(index),
    original_acceptance_failure='../../local-acceptance/attempt-1-failed/model.stderr.log',
    reproduced_old_order_rc=json.loads((out/'old-order.command.json').read_text())['rc'])
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary),flush=True)
raise SystemExit(0 if before==after and summary['all_new_checks_rc0'] else 1)
