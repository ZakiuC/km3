import subprocess,json,sys,pathlib,datetime
out=pathlib.Path(__file__).parent
name=sys.argv[1]; argv=sys.argv[2:]
(out/(name+'.command.json')).write_text(json.dumps(dict(argv=argv,cwd='/home/zakiu/km3',utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),ensure_ascii=False,indent=2)+'\n')
r=subprocess.run(argv,cwd='/home/zakiu/km3',capture_output=True)
(out/(name+'.stdout')).write_bytes(r.stdout)
(out/(name+'.stderr')).write_bytes(r.stderr)
(out/(name+'.rc')).write_text(str(r.returncode)+'\n')
print(r.stdout.decode(errors='replace'),end=''); print(r.stderr.decode(errors='replace'),end='',file=sys.stderr); print('recorded rc='+str(r.returncode)); sys.exit(r.returncode)
