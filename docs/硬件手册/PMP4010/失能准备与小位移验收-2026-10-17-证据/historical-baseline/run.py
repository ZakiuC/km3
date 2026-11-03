import datetime, json, os, pathlib, subprocess, sys
base=pathlib.Path('/tmp/km3-pmp4010-a-20261017-evidence')
phase, name, command=sys.argv[1:]
out=base/phase
out.mkdir(exist_ok=True)
env=dict(os.environ, TMPDIR=str(base/'tmp'))
meta={'command':command, 'argv':['bash','-c',command], 'cwd':'/home/zakiu/km3','env_overrides':{'TMPDIR':env['TMPDIR']},'start_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runtime_model':'unknown','runtime_effort':'unknown','last_token_usage_input_tokens':'unknown'}
(out/(name+'.command.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
with (out/(name+'.stdout.log')).open('wb') as stdout, (out/(name+'.stderr.log')).open('wb') as stderr:
    result=subprocess.run(['bash','-c',command],cwd=meta['cwd'],env=env,stdout=stdout,stderr=stderr)
meta.update(exitcode=result.returncode,end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
(out/(name+'.result.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
(out/(name+'.exitcode')).write_text(str(result.returncode)+'\n')
print(name, 'exitcode='+str(result.returncode))
print((out/(name+'.stdout.log')).read_text(errors='replace')[-1600:])
print((out/(name+'.stderr.log')).read_text(errors='replace')[-1600:])
sys.exit(result.returncode)
