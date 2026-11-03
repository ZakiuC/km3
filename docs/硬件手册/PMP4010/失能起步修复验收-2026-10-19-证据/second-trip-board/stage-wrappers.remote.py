import hashlib,json,pathlib,sys
base=pathlib.Path(sys.argv[1]);items=json.load(sys.stdin)
assert base.is_dir()
for name,item in items.items():
 assert name in ('board_app.py','board_pty_runner.py')
 data=item['data'].encode();assert hashlib.sha256(data).hexdigest()==item['sha256']
 with (base/name).open('xb') as f:f.write(data)
 assert hashlib.sha256((base/name).read_bytes()).hexdigest()==item['sha256']
print(json.dumps({'uploaded':{k:v['sha256'] for k,v in items.items()},'CAN_opens':0,'executed':False}))
