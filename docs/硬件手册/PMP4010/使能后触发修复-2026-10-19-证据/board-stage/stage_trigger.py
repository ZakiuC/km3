"""Local driver. Default plans only; --apply needs explicit parent authorization.

Does not launch the supplied manual launcher. The only remote shell invocation
is `sh -n` syntax checking; no package app, scan or CAN transport is started.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

B = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',required=True,type=Path)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--stage',help='Default isolated directory uses full archive SHA')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    raw = args.archive.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == args.sha256
    stage = args.stage or '/var/tmp/km3-pmp4010-trigger-20261019-'+args.sha256
    plan = dict(archive=str(args.archive),archive_sha256=args.sha256,stage=stage,
                steps=['archive_manifest_verified_exclusive_stage','AArch64_pureload_math_simple_manual_import_CLI_help','launcher_sh_n_only'],
                manual_launcher_executed=False,CAN_operations=0)
    if not args.apply:
        print(json.dumps(dict(mode='local_plan_only',actual_ssh_calls=0,**plan),indent=2))
        return
    ssh = ['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
           '-o','ConnectionAttempts=1','-o','ConnectTimeout=5','root@192.168.0.188']
    def remote(label, script, script_args=(), input_bytes=b''):
        code = script.read_text()
        argv = ssh + [shlex.join(['python3','-I','-B','-c',code,*script_args])]
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            p = subprocess.run(argv,input=input_bytes,capture_output=True,timeout=55)
            rc, stdout, stderr = p.returncode,p.stdout,p.stderr
        except subprocess.TimeoutExpired as exc:
            rc,stdout,stderr = 124,exc.stdout or b'',(exc.stderr or b'')+b'\nLocal SSH timeout; no automatic retry.\n'
        (B/(label+'.stdout')).write_bytes(stdout)
        (B/(label+'.stderr')).write_bytes(stderr)
        (B/(label+'.rc')).write_text(str(rc)+'\n')
        (B/(label+'.command.json')).write_text(json.dumps(dict(argv=argv,start_utc=start,
            end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=rc,
            stdin_size=len(input_bytes),stdin_sha256=hashlib.sha256(input_bytes).hexdigest(),
            script_sha256=hashlib.sha256(code.encode()).hexdigest()),indent=2)+'\n')
        print(json.dumps(dict(label=label,rc=rc)),flush=True)
        if rc:
            raise RuntimeError(label+' failed; stage retained, no automatic retry: '+stderr.decode(errors='replace'))
        return stdout
    staged=json.loads(remote('stage',B/'stage.remote.py',(args.sha256,stage),raw))
    assert staged['archive_sha256']==args.sha256 and staged['payload_root']==stage+'/payload'
    output=remote('pureload',B/'pureload.remote.py',(stage+'/payload',)).decode()
    summaries=[]
    for line in output.splitlines():
        if line.startswith('{'):
            try: summaries.append(json.loads(line))
            except json.JSONDecodeError: pass
    assert any(s.get('native_vectors')==33 and s.get('python_vectors')==33 and s.get('native_int32_vectors')==14 and s.get('forbidden_io_attempts')==0 for s in summaries)
    assert any(s.get('manual_import') is True and s.get('simple_import') is True and s.get('cli_help') is True and s.get('forbidden_IO_write_or_run_attempts')==0 for s in summaries)
    syntax=B/'launcher-syntax.remote.py'
    syntax.write_text("import json,pathlib,subprocess,sys\np=pathlib.Path(sys.argv[1]);assert p.is_file()\nx=subprocess.run(['sh','-n',str(p)],capture_output=True,text=True,timeout=5)\nprint(json.dumps(dict(argv=['sh','-n',str(p)],rc=x.returncode,stdout=x.stdout,stderr=x.stderr,launcher_executed=False,CAN_opens=0,CAN_sends=0)))\nsys.exit(x.returncode)\n")
    remote('launcher-syntax',syntax,(stage+'/payload/run-manual.sh',))
    (B/'stage-summary.json').write_text(json.dumps(dict(result='PASS',**plan,
        archive_uploads=1,manual_import=True,simple_import=True,native_vectors=33,python_vectors=33,
        native_int32_vectors=14,forbidden_attempts=0,launcher_syntax=True),indent=2)+'\n')

if __name__=='__main__':
    main()
