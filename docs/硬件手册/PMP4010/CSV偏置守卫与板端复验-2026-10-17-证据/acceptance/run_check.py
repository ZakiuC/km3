import datetime,json,pathlib,subprocess,sys,time
root=pathlib.Path(__file__).parent
name=sys.argv[1]; command=sys.argv[2:]
prefix=root/name
with prefix.with_suffix('.command.json').open('x') as f:
 json.dump({'argv':command,'cwd':'/home/zakiu/km3','started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},f,ensure_ascii=False,indent=2)
start=time.monotonic()
with prefix.with_suffix('.stdout.log').open('xb') as out, prefix.with_suffix('.stderr.log').open('xb') as err:
 result=subprocess.run(command,cwd='/home/zakiu/km3',stdout=out,stderr=err)
record={'name':name,'argv':command,'returncode':result.returncode,'elapsed_seconds':time.monotonic()-start,'stdout':str(prefix.with_suffix('.stdout.log')),'stderr':str(prefix.with_suffix('.stderr.log'))}
with prefix.with_suffix('.result.json').open('x') as f: json.dump(record,f,ensure_ascii=False,indent=2)
print(json.dumps(record,ensure_ascii=False))
print('STDOUT:\n'+prefix.with_suffix('.stdout.log').read_text(errors='replace'))
print('STDERR:\n'+prefix.with_suffix('.stderr.log').read_text(errors='replace'))
sys.exit(result.returncode)
