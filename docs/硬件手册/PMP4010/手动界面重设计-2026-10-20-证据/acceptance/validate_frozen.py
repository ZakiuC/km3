#!/usr/bin/env python3
"""Verify immutable source freeze and referenced test records without running hardware."""
import hashlib,json,pathlib,sys
ROOT=pathlib.Path('/home/zakiu/km3'); E=pathlib.Path(__file__).resolve().parent.parent
freeze=json.loads(pathlib.Path(sys.argv[1]).read_text())
for name,digest in freeze['source_hashes'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
results=[]
for arg in sys.argv[2:]:
    p=pathlib.Path(arg);rec=json.loads(p.read_text());assert rec['rc']==0,str(p)
    before=rec['source_sha256_before'];after=rec['source_sha256_after'];assert before==after,str(p)
    for name,digest in freeze['source_hashes'].items():
        if name in before:assert before[name]==digest,str(p)+': '+name
    results.append({'record':str(p),'argv':rec['argv'],'rc':rec['rc'],'source_hashes_stable':True})
assert freeze['local_acceptance']['status']=='passed'
print(json.dumps({'frozen_files_checked':len(freeze['source_hashes']),'records_checked':results,'hardware_acceptance':False},ensure_ascii=False,indent=2))
