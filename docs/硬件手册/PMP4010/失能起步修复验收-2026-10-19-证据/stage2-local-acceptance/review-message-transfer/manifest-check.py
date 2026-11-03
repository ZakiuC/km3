import json, hashlib
from pathlib import Path
root=Path('/tmp/km3-pp-disabled-start-20261019-impl')
new=root/'switched-on-stage2/freeze/source-sha256.json'
old=root/'probe-freeze/source-sha256.json'
a=json.loads(new.read_text())['files']; b=json.loads(old.read_text())['files']
assert hashlib.sha256(new.read_bytes()).hexdigest()=='3e492ef6a6b36aa94789d78500a2992aee0ff52b2702acc7e377ffb484408b47'
assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in a.items())
print(json.dumps({'manifest_sha256':hashlib.sha256(new.read_bytes()).hexdigest(),'source_match':True,'changed_since_stage1':[p for p,h in a.items() if b.get(p)!=h],'unchanged_source_count':sum(b.get(p)==h for p,h in a.items())},ensure_ascii=False))
