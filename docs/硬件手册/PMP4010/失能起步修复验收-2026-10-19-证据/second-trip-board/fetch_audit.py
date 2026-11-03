import datetime,json,pathlib,subprocess,sys
b=pathlib.Path(__file__).resolve().parent;label=sys.argv[1]
remote='/var/tmp/km3-pp-disabled-start-stage3-recovery-20261019-b2/move-session-1/app-audit.jsonl'
argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188','cat '+remote]
p=subprocess.run(argv,capture_output=True,timeout=20)
(b/(label+'.jsonl')).write_bytes(p.stdout);(b/(label+'.stderr')).write_bytes(p.stderr)
(b/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,rc=p.returncode,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()),indent=2)+'\n');assert p.returncode==0
rows=[json.loads(x) for x in p.stdout.decode().splitlines()];states=[r['state'] for r in rows if r['event']=='ui_state']
print(json.dumps(dict(downloads=sum(r['event']=='sdo_download_begin' for r in rows),state=states[-1]),ensure_ascii=False,indent=2))
