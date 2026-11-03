"""One-time approved read-only operation; never rerun after an SDO failure."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
ROOT=Path('/tmp/km3-pp-preflight-20261017')
PAYLOAD='/tmp/km3-pmp4010-reload-20261017-gVa5HIb5/payload'
STAGE=(ROOT/'board-stage-create.stdout').read_text().strip()
assert STAGE.startswith('/tmp/km3-pp-preflight-20261017-') and len(STAGE.split('/'))==3
SSH=['ssh','-o','StrictHostKeyChecking=yes','-o','BatchMode=yes','-o','ConnectTimeout=8','root@192.168.0.188']
SCP=['scp','-o','StrictHostKeyChecking=yes','-o','BatchMode=yes','-o','ConnectTimeout=8']

def run(label,argv):
    cmd=[sys.executable,'-I','-B',str(ROOT/'record.py'),label]+argv
    subprocess.run(cmd,check=True)

def remote(label,argv):
    run(label,SSH+[shlex.join(argv)])

run('board-script-upload',SCP+[str(ROOT/'preflight.py'),'root@192.168.0.188:'+STAGE+'/preflight.py'])
remote('board-pre-read-sha256',['sha256sum',STAGE+'/preflight.py',PAYLOAD+'/payload-manifest.json',PAYLOAD+'/drivers/pmp4010/pmp4010.py',PAYLOAD+'/drivers/pmp4010/od.json'])
lines=(ROOT/'board-pre-read-sha256.stdout').read_text().splitlines()
actual={line.split()[1]:line.split()[0] for line in lines}
expected={STAGE+'/preflight.py':hashlib.sha256((ROOT/'preflight.py').read_bytes()).hexdigest(),
          PAYLOAD+'/payload-manifest.json':'df7588af7102192aa81f0621288613c5ca2bc3ac3fe72d3c4c6eacd1ca3390ac',
          PAYLOAD+'/drivers/pmp4010/pmp4010.py':'2fca0b103544c29f78a79b9e349ec841db0e39abf8f39d9d910e57e47b1e0a1a',
          PAYLOAD+'/drivers/pmp4010/od.json':'5bf6c7bbd73c1c6ef3855fb9ef104d8b6d72ecec8a598eeff16f71682934e58f'}
assert actual==expected,(actual,expected)
remote('board-payload-no-can-smoke',['python3','-I','-B',PAYLOAD+'/load_smoke.py'])
remote('board-preflight-offline',['python3','-I','-B',STAGE+'/preflight.py','--source-root',PAYLOAD])
# The next and only active operation is the reviewed 62-object upload sequence.
# Command/raw stdout/stderr/exit code remain on the host even when rc != 0.
remote('board-live-readonly',['python3','-I','-B',STAGE+'/preflight.py','--source-root',PAYLOAD,'--live-read-only','--i-know-rt-is-running','--timeout','0.25'])
print(json.dumps(dict(stage=STAGE,result='read-only-preflight-complete')))
