import datetime
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path('/tmp/km3-pp-preflight-20261017')
label = sys.argv[1]
argv = sys.argv[2:]
(ROOT/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),ensure_ascii=False,indent=2))
with (ROOT/(label+'.stdout')).open('xb') as out, (ROOT/(label+'.stderr')).open('xb') as err:
    result = subprocess.run(argv, stdout=out, stderr=err)
(ROOT/(label+'.rc')).write_text(str(result.returncode)+'\n')
print(json.dumps(dict(label=label,rc=result.returncode,stdout=str(ROOT/(label+'.stdout')),stderr=str(ROOT/(label+'.stderr')))))
sys.exit(result.returncode)
