import datetime,hashlib,json,pathlib,subprocess,sys
b=pathlib.Path(__file__).parent
label=sys.argv[1];argv=sys.argv[2:]
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
p=subprocess.run(argv,capture_output=True,timeout=25)
(b/(label+'.stdout')).write_bytes(p.stdout);(b/(label+'.stderr')).write_bytes(p.stderr)
(b/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,utc_start=start,utc_end=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=p.returncode),indent=2)+'\n')
print(json.dumps(dict(label=label,rc=p.returncode,stdout_bytes=len(p.stdout),stderr=p.stderr.decode(errors='replace'))))
sys.exit(p.returncode)
