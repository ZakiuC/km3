#!/usr/bin/env python3
import datetime,hashlib,json,pathlib,subprocess,sys
from zoneinfo import ZoneInfo
ROOT=pathlib.Path('/home/zakiu/km3')
OUT=pathlib.Path(__file__).resolve().parent

def stamp():return datetime.datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
def hashes():
    files=list((ROOT/'device/drivers/pmp4010/tools').glob('*.py'))
    files+=list((ROOT/'tools/hwtui').glob('*.py'))+list((ROOT/'tools/tests').glob('*.py'))
    files+=[ROOT/'device/drivers/pmp4010/driver/od.json',ROOT/'device/drivers/pmp4010/driver/pmp4010_velocity.cpp',ROOT/'device/drivers/pmp4010/driver/pmp4010_velocity.hpp']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
def run(label,argv,cwd=ROOT):
    assert not (OUT/(label+'.json')).exists(),'existing evidence: '+label
    rec=dict(argv=argv,cwd=str(cwd),start=stamp(),source_sha256_before=hashes())
    p=subprocess.run(argv,cwd=cwd,capture_output=True)
    (OUT/(label+'.stdout')).write_bytes(p.stdout);(OUT/(label+'.stderr')).write_bytes(p.stderr)
    rec.update(rc=p.returncode,end=stamp(),source_sha256_after=hashes())
    (OUT/(label+'.json')).write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n')
    print(label,'rc='+str(p.returncode),flush=True);sys.stdout.buffer.write(p.stdout);sys.stderr.buffer.write(p.stderr)
    if p.returncode:raise SystemExit(p.returncode)
    assert rec['source_sha256_before']==rec['source_sha256_after'],'source changed during '+label
    return p.stdout
if __name__=='__main__':run(sys.argv[1],sys.argv[2:])
