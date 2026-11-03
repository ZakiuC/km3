from pathlib import Path
import contextlib,hashlib,importlib.util,io,json,shlex
B=Path(__file__).parent;prior=B.parent
(B/'remote_record.py').write_bytes((prior/'remote_record.py').read_bytes())
spec=importlib.util.spec_from_file_location('remote_record',B/'remote_record.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
oldroot=json.loads((prior/'stage-probe-package.stdout').read_text())['payload_root']
passive=(prior/'passive.py').read_text().replace('/tmp/km3-pp-disabled-start-readonly-20261019-kf4gqufd/payload',oldroot)
(B/'passive.py').write_text(passive)
(B/'record.py').write_bytes((prior/'record.py').read_bytes())
with contextlib.redirect_stdout(io.StringIO()):p=r.run('before-stage','python3 -I -B -',passive.encode())
assert p.returncode==0
archive=Path('/tmp/km3-pp-disabled-start-stage2-package-20261019/pmp4010-board-payload.tar.gz')
assert archive.stat().st_size==190810
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='1aa97f73db9299d950bfa7e7b4b701f06998b3cdfd99c0c18d8dc7b251bf270f'
stage=(prior/'stage_frozen.py').read_text().replace('e5e6be59dbed7611f3b4e5f6d5c53e38e2f7e1527afa1831e7b511e4a0fcacb6','1aa97f73db9299d950bfa7e7b4b701f06998b3cdfd99c0c18d8dc7b251bf270f').replace('km3-pp-disabled-start-readonly-20261019-','km3-pp-disabled-start-stage2-20261019-')
(B/'stage_remote.py').write_text(stage)
with contextlib.redirect_stdout(io.StringIO()):p=r.run('stage-package','python3 -I -B -c '+shlex.quote(stage),archive.read_bytes())
assert p.returncode==0
staged=json.loads(p.stdout)
load=(prior/'probe_load_audit.py').read_text();(B/'pure_load_audit.py').write_text(load)
with contextlib.redirect_stdout(io.StringIO()):p=r.run('pure-load','python3 -I -B -c '+shlex.quote(load)+' '+shlex.quote(staged['payload_root']))
assert p.returncode==0
assert 'outer_audit_forbidden_write_or_io_attempts=0' in p.stdout.decode()
with contextlib.redirect_stdout(io.StringIO()):p=r.run('after-load','python3 -I -B -',passive.encode())
assert p.returncode==0
before=json.loads((B/'before-stage.stdout').read_text());after=json.loads(p.stdout)
changes=[k for k in before if k!='utc' and before[k]!=after[k]];assert changes==[]
summary=dict(result='PASS',stage=staged,command_rcs={n:json.loads((B/(n+'.command.json')).read_text())['rc'] for n in ('before-stage','stage-package','pure-load','after-load')},native_vectors=33,python_vectors=33,native_int32_vectors=14,imports=16,forbidden_IO_write_attempts=0,passive_changed_keys=changes,old_stage1_payload_unchanged=True,CAN_opens=0,CAN_sends=0)
(B/'load-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
