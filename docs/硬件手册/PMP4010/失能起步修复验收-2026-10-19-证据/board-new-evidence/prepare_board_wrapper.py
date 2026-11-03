from pathlib import Path
import ast,hashlib,json,tarfile
B=Path(__file__).parent
archive=Path('/tmp/km3-pp-disabled-start-package-20261019/pmp4010-board-payload.tar.gz')
sha=hashlib.sha256(archive.read_bytes()).hexdigest();assert sha=='a1bfb7b491ec1ff0509992db6ecae3b68c80384d4db59d068cffa47960f31f6c'
remote=json.loads((B/'stage-probe-package.stdout').read_text())['payload_root']
local=B/'local-probe-payload';local.mkdir(exist_ok=False)
with tarfile.open(archive,'r:gz') as t:
 manifest=t.extractfile('payload-manifest.json').read();manifestsha=hashlib.sha256(manifest).hexdigest()
 entries=json.loads(manifest)['files']
 for m in t.getmembers():
  assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts
  assert m.isfile() or m.issym()
  if m.isfile():
   p=local/m.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(t.extractfile(m).read())
 for m in t.getmembers():
  if m.issym():
   assert Path(m.linkname).name==m.linkname
   (local/m.name).symlink_to(m.linkname)
s=Path('/tmp/km3-pp-motion-20261017/board_app.py').read_text()
s=s.replace('/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/payload',remote).replace('3dfc3a5c10c13d4c3f49691f259a9a7097cc480f9cb6b604e34f0a554a97b2b5',manifestsha).replace('5bf6c7bbd73c1c6ef3855fb9ef104d8b6d72ecec8a598eeff16f71682934e58f','1cc28b19cf94349d83497af7c252a5fa68702af433df7fd7afd431b6cdfa6a6e')
s=s.replace("OUTPUT=PAYLOAD.parent/'pp-session-1'","OUTPUT=PAYLOAD.parent/'probe-session-1'")
s=s.replace("AUTHORIZATION_CONTEXT='用户在本会话确认现场安全并授权一次小角度验证；仅本次PP1约0.1°，既有7项缺口保留；另已批准失能准备607A对齐和6081=100。此记录不替代UI确认。'", "AUTHORIZATION_CONTEXT='本轮仅B起步核证：在Halt保持下对当前位置取新ACK并核6062；不解除Halt、不位移，结束2→0，q沿用exit-only Shutdown6。实际执行还须主线程批准；RT/OD/软限/预算/冻结token均由原Form键入。'")
s=s.replace("manifest=root/'payload-manifest.json'", "manifest=root/'payload-manifest.json'\n    if root==PAYLOAD:\n        assert hashlib.sha256((PAYLOAD.parent/'frozen-payload.tar.gz').read_bytes()).hexdigest()=='a1bfb7b491ec1ff0509992db6ecae3b68c80384d4db59d068cffa47960f31f6c','archive mismatch'")
s=s.replace('move_timeout=8','move_timeout=180').replace("'--move-timeout','8'","'--move-timeout','180'")
(B/'board_app.py').write_text(s)
s=Path('/tmp/km3-pp-motion-20261017/board_pty_runner.py').read_text().replace("ROOT=Path('/home/zakiu/km3')","ROOT=Path(__file__).parent/'pty-source'").replace('/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/board_app.py',str(Path(remote).parent/'board_app.py'))
(B/'board_pty_runner.py').write_text(s)
for n in ('board_app.py','board_pty_runner.py'):ast.parse((B/n).read_text(),filename=n)
print(json.dumps(dict(remote_payload=remote,manifest_sha256=manifestsha,wrapper_sha256=hashlib.sha256((B/'board_app.py').read_bytes()).hexdigest(),runner_sha256=hashlib.sha256((B/'board_pty_runner.py').read_bytes()).hexdigest(),offline_root=str(local),real_CAN_opens=0)))
