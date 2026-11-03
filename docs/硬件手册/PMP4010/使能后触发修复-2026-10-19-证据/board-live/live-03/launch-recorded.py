"""Record one authorized SSH read/review session; stdin stays with that process."""
import datetime
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys

base = Path(__file__).resolve().parent
remote_argv = [
    'python3', '-I', '-B',
    '/var/tmp/km3-pmp4010-accept-20261019-54c5cb09/controlled-forward.runner.py',
    '--mode', 'live',
    '--payload', '/var/tmp/km3-pmp4010-trigger-20261019-c20f27992759/payload',
    '--output', '/var/tmp/km3-pmp4010-accept-20261019-54c5cb09/run-01',
    '--rt-reason', '沿用用户已确认到场接线急停的小角度现场授权；保留RT进程，接受本次同client测试的RT占用风险',
    '--od-reason', '沿用用户已确认的小角度现场授权；仅本次PP1正向量化0.1度、100U/s；接受原OD未核对项及独立停机责任',
]
argv = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=5',
        'root@192.168.0.188', shlex.join(remote_argv)]
record = dict(argv=argv, remote_argv=remote_argv,
              started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              stage_authorization='upload and CAN upload-only scan/review; no EXECUTE authorized',
              status='running')
(base/'live.command.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
with (base/'live.stdout').open('xb') as out, (base/'live.stderr').open('xb') as err:
    p = subprocess.Popen(argv, stdin=sys.stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (base/'local-ssh.pid').write_text(str(p.pid)+'\n')
    selector = selectors.DefaultSelector()
    selector.register(p.stdout, selectors.EVENT_READ, (out, sys.stdout.buffer))
    selector.register(p.stderr, selectors.EVENT_READ, (err, sys.stderr.buffer))
    while selector.get_map():
        for key, _ in selector.select(timeout=1):
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                selector.unregister(key.fileobj)
                continue
            logfile, display = key.data
            logfile.write(data)
            logfile.flush()
            try:
                display.write(data)
                display.flush()
            except OSError:
                pass
    rc = p.wait()
record.update(status='finished', rc=rc,
              finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
(base/'live.rc').write_text(str(rc)+'\n')
(base/'live.command.json').write_text(json.dumps(record, ensure_ascii=False, indent=2)+'\n')
raise SystemExit(rc)
