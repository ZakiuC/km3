from pathlib import Path
import datetime,hashlib,json,subprocess
ROOT=Path('/home/zakiu/km3');BASE=ROOT/'docs/硬件手册/PMP4010';EV=BASE/'状态样本修复-2026-10-19-证据';OUT=EV/'local-acceptance'
argv=['bash','tools/check-docs.sh'];p=subprocess.run(argv,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
row={'argv':argv,'cwd':str(ROOT),'returncode':p.returncode,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
for kind,data in [('stdout',p.stdout),('stderr',p.stderr)]:
 name='docs-after-report.'+kind+'.log';(OUT/name).write_bytes(data);row[kind]=name;row[kind+'_sha256']=hashlib.sha256(data).hexdigest()
(OUT/'docs-after-report.command.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
entries=[]
for directory in [OUT,EV/'package']:
 for path in sorted(directory.rglob('*')):
  if not path.is_file() or path.is_symlink() or path==OUT/'artifact-index.json':continue
  entries.append({'path':str(path.relative_to(BASE)),'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(OUT/'artifact-index.json').write_text(json.dumps({'scope':'local acceptance and package evidence only; excludes this index and report to prevent self hashes; includes preserved candidate rounds','files':entries},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'docs_after_report_rc':p.returncode,'indexed_files':len(entries)},ensure_ascii=False))
raise SystemExit(p.returncode)
