import sys,json,pathlib,hashlib
base=pathlib.Path('docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/post-power-board')
p=base/'verify_move_pty.py'
forbidden=[]
def audit(event,args):
    if event=='open' and ((args[1] and any(c in args[1] for c in 'wax+')) or (isinstance(args[2],int) and args[2]&3)):
        forbidden.append(event);raise AssertionError('write forbidden')
    if event in ('socket.__new__','socket.connect','subprocess.Popen'):
        forbidden.append(event);raise AssertionError('external IO forbidden')
sys.addaudithook(audit)
ns={'__file__':str(p)}
exec(compile(p.read_text().split('summary=dict(',1)[0],str(p)+':read-only-prefix','exec'),ns)
current=[]
for name in ('pmp4010_bench.py','pmp4010_position.py','pmp4010_tui.py'):
    rel=pathlib.Path('device/drivers/pmp4010/tools')/name
    digest=hashlib.sha256(rel.read_bytes()).hexdigest()
    assert digest==hashlib.sha256((base/'pty-source'/rel).read_bytes()).hexdigest()
    current.append({'file':str(rel),'sha256':digest})
for scenario in ('success','halt-drift','final-no-ack'):
    rows=[json.loads(x) for x in (base/('pty-move-'+scenario+'-v2')/'motion.jsonl').read_text().splitlines()]
    for phase in ('起步稳定采样','Halt保持核证采样','Halt使能零漂移采样'):
        samples=[r for r in rows if r['event']=='disabled_start_sample' and r['phase'].startswith(phase)]
        if scenario=='halt-drift' and phase=='Halt使能零漂移采样':assert not samples
        else:assert len(samples)==5
assert not forbidden
print(json.dumps({'result':'PASS','scenario_count':len(ns['results']),'source_files_checked':len(ns['manifest']),'current_business_snapshot_equal':current,'forbidden_IO_attempts':len(forbidden),'verifier_output_writes_removed':True,'hardware_acceptance':False},ensure_ascii=False,indent=2))
