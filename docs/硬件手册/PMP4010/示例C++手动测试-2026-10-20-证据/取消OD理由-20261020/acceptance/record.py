#!/usr/bin/env python3
"""Record an explicit command with immutable output, source hashes and actual status."""
import datetime, hashlib, json, pathlib, subprocess, sys
from zoneinfo import ZoneInfo
ROOT=pathlib.Path('/home/zakiu/km3')
OUT=pathlib.Path(__file__).resolve().parent
BASE=OUT.parent.parent

def stamp(): return datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
def hashes():
    names=json.loads((BASE/'package/SOURCE_SHA256.json').read_text())
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names}
def run(label,command,cwd=ROOT):
    if (OUT/(label+'.json')).exists(): raise RuntimeError('Refusing to replace evidence '+label)
    record=dict(argv=command,cwd=str(cwd),started=stamp(),source_sha256_before=hashes())
    try:
        p=subprocess.run(command,cwd=cwd,capture_output=True)
        rc,stdout,stderr=p.returncode,p.stdout,p.stderr
    except OSError as e: rc,stdout,stderr=127,b'',str(e).encode()
    (OUT/(label+'.stdout.log')).write_bytes(stdout)
    (OUT/(label+'.stderr.log')).write_bytes(stderr)
    record.update(returncode=rc,finished=stamp(),source_sha256_after=hashes())
    (OUT/(label+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print(label,'rc='+str(rc),flush=True)
    sys.stdout.buffer.write(stdout); sys.stderr.buffer.write(stderr)
    if rc: raise SystemExit(rc if rc>0 else 128-rc)
    if record['source_sha256_before']!=record['source_sha256_after']: raise RuntimeError('Source changed during '+label)
    return stdout
if __name__=='__main__': run(sys.argv[1],sys.argv[2:])
