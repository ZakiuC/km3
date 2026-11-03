import json,pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1]);assert p.is_file()
x=subprocess.run(['sh','-n',str(p)],capture_output=True,text=True,timeout=5)
print(json.dumps(dict(argv=['sh','-n',str(p)],rc=x.returncode,stdout=x.stdout,stderr=x.stderr,launcher_executed=False,CAN_opens=0,CAN_sends=0)))
sys.exit(x.returncode)
