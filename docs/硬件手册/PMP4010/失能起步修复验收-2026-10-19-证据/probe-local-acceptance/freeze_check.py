from pathlib import Path
import hashlib,json,sys
out=Path('/tmp/km3-pp-disabled-start-20261019-acceptance')
before=json.loads((out/'probe-freeze-before.json').read_text())
rows=[dict(x, after=hashlib.sha256((Path('/home/zakiu/km3')/x['path']).read_bytes()).hexdigest()) for x in before['files']]
result={'stage':'probe_no_travel','manifest_sha256':before['manifest_sha256'],'files':rows,'matched':all(x['after']==x['expected'] for x in rows)}
(out/(sys.argv[1]+'.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'matched':result['matched'],'files':len(rows)}));assert result['matched']
