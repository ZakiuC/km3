from pathlib import Path
import subprocess,json,hashlib,tarfile
OUT=Path('/tmp/km3-pp-disabled-start-package-20261019');ROOT=Path('/home/zakiu/km3')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def record(name,args,expected=0):
    p=subprocess.run(args,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    original_name=name; attempt=1
    while (OUT/'records'/(name+'.command.json')).exists():
        attempt+=1; name=original_name+'-attempt'+str(attempt)
    for suffix,data in [('command.json',(json.dumps({'argv':args,'cwd':str(ROOT)},ensure_ascii=False,indent=2)+'\n').encode()),('stdout.log',p.stdout),('stderr.log',p.stderr),('exitcode',(str(p.returncode)+'\n').encode())]:
        (OUT/'records'/(name+'.'+suffix)).write_bytes(data)
    if p.returncode!=expected:raise RuntimeError((name,p.returncode,p.stderr.decode()))
    return p
roundtrip=OUT/'roundtrip';roundtrip.mkdir(exist_ok=True)
with tarfile.open(OUT/'pmp4010-board-payload.tar.gz') as tf:
    names=tf.getnames();assert len(names)==21,len(names)
    for m in tf.getmembers():
        assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
        assert m.isfile() or m.issym()
        if m.issym(): assert '/' not in m.linkname and '..' not in m.linkname
record('archive-extract',['tar','-xzf',str(OUT/'pmp4010-board-payload.tar.gz'),'-C',str(roundtrip)])
record('archive-host-smoke',['python3','-I','-B',str(roundtrip/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so'),'--check-missing-library'])
# Focused negative check: fail integrity before loading or import after corruption.
file=roundtrip/'drivers/pmp4010/velocity_vectors.json'; original=file.read_bytes();file.write_bytes(original+b' ')
try:
    p=record('archive-tamper-refused',['python3','-I','-B',str(roundtrip/'load_smoke.py'),'--library',str(ROOT/'build-host/libpmp4010_velocity.so')],1)
    assert b'payload SHA256 mismatch' in p.stderr and b'import=' not in p.stdout
finally:file.write_bytes(original)
summary=json.loads((OUT/'summary.json').read_text());summary['checks'].update({'archive_roundtrip_host_smoke_exitcode':0,'tampered_payload_exitcode':1,'tamper_refused_before_import_or_library_load':True,'archive_safe_relative_members':21})
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
commands=[]
for c in sorted((OUT/'records').glob('*.command.json')):
    stem=c.name[:-len('.command.json')];args=json.loads(c.read_text())
    commands.append({'name':stem,**args,'exitcode':int((OUT/'records'/(stem+'.exitcode')).read_text()),'stdout':'records/'+stem+'.stdout.log','stderr':'records/'+stem+'.stderr.log'})
(OUT/'command-index.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2)+'\n')
lines=['# PMP4010 本地板端加载待交付包','',f"归档：`{summary['archive']}`",f"归档大小：{summary['archive_bytes']} bytes；payload普通文件{summary['payload_regular_files']}项（{summary['payload_regular_bytes']} bytes），符号链接2项。",f"归档SHA256：`{summary['archive_sha256']}`",f"ARM so SHA256：`{summary['arm_library_sha256']}`",'', '仅写当前/tmp目录；未连接板端、未执行ARM产物、未启动服务或打开总线。工作区未修改。', '', '内容清单见 payload/payload-manifest.json；含白名单工具及2个库符号链接。源HEAD与dirty清单见source-manifest.json，完整包目录列表见records/archive-list.stdout.log。', '', '验证命令及原始输出/退出码逐项见command-index.json与records/。host逻辑检查及归档解压后重跑各rc0：16模块导入、native33/Python33向量、14 Int32向量、缺库明确拒绝、禁止IO尝试0。故意破坏解压副本向量文件后检查rc1，完整性检查在导入/加载前拒绝，随后恢复副本。', '', 'ELF静态检查和后续兼容性要求见elf-assessment.json与payload/README-板端加载.md。板端真实依赖解析、ARM加载和现场运动全部仍待后续授权/执行。', '', '板端加载命令（显式替换目录，不切current）：', '', '```sh','python3 -I -B /ABSOLUTE/EXTRACTED/PACKAGE/load_smoke.py --check-missing-library','```','', 'runtime模型/effort/input元数据不可获取，均unknown；路由请求与input估计记录在source-manifest.json。']
(OUT/'交付说明.md').write_text('\n'.join(lines)+'\n')
files=[]
for path in sorted(OUT.iterdir()):
    if path.is_file() and path.name!='artifact-index.json':files.append({'path':path.name,'size':path.stat().st_size,'sha256':sha(path)})
for path in sorted((OUT/'records').iterdir()):
    if path.is_file():files.append({'path':str(path.relative_to(OUT)),'size':path.stat().st_size,'sha256':sha(path)})
(OUT/'artifact-index.json').write_text(json.dumps({'files':files},ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,indent=2));print('recorded_commands='+str(len(commands)))
