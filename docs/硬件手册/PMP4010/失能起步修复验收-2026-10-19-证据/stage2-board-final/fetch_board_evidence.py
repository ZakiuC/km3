from pathlib import Path
import contextlib,importlib.util,io,json,shlex,sys
b=Path(__file__).parent
spec=importlib.util.spec_from_file_location('remote_record',b/'remote_record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
remote=Path(json.loads((b/'load-summary.json').read_text())['stage']['base'])/'probe-session-1'
label=sys.argv[1]
code='import json,pathlib; root=pathlib.Path('+repr(str(remote))+'); names=["app-audit.jsonl"]+[p.name for p in root.glob("pmp4010-motion-*.jsonl")]; print(json.dumps({n:(root/n).read_text() for n in names},ensure_ascii=False))'
with contextlib.redirect_stdout(io.StringIO()):p=r.run('board-'+label+'-fetch','python3 -I -B -c '+shlex.quote(code))
assert p.returncode==0
files=json.loads(p.stdout)
out=b/('board-'+label+'-evidence');out.mkdir(exist_ok=False)
for n,data in files.items():(out/n).write_text(data)
rows=[json.loads(x) for x in files['app-audit.jsonl'].splitlines()]
for x in rows:
 if x['event']=='job_event' and x['name'] in ('result','bench_result','probe_observed','exception','probe_control_readback','after_cleanup','after_cleanup_unavailable'):print(json.dumps(x,ensure_ascii=False))
print(json.dumps({'files':list(files),'fetch_rc':p.returncode,'controls':[int.from_bytes(bytes.fromhex(x['raw_hex']),'little') for x in rows if x['event']=='sdo_download_begin' and x['index']==0x6040],'downloads':len([x for x in rows if x['event']=='sdo_download_begin']),'SDO_exceptions':len([x for x in rows if x['event'] in ('sdo_download_exception','sdo_upload_exception')])}))
