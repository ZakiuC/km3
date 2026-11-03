import sys
sys.argv = ["-", '/var/tmp/km3-pmp4010-alarm-20261019-735538978204/payload/logs/relative-minus10-100rpm-a131072-once-20261019']
"""被动OS快照；不打开CAN，不控制进程，只收集指定本次日志目录。"""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def snapshot(log_root):
    def run(argv):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            return dict(argv=argv, rc=p.returncode, stdout=p.stdout, stderr=p.stderr)
        except Exception as exc:
            return dict(argv=argv, error=str(exc))

    def read(path):
        try:
            return path.read_text()
        except (OSError, UnicodeError) as exc:
            return dict(error=str(exc))

    def sha(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return dict(error=str(exc))

    services = {s: run(['systemctl', 'show', s,
        '--property=MainPID,ActiveState,SubState,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic'])
        for s in ('km3-core', 'km3-rt-controller', 'km3-video')}
    processes = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            cmd = (p/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            continue
        if not any(s in cmd for s in ('pmp4010', 'pty_runner', 'mock_app', 'km3-pp',
                'drive_probe', 'board_app', 'candump', 'cansend', 'cangen', 'canplayer', 'km3-rt-controller')):
            continue
        fds = {}
        try:
            for f in (p/'fd').iterdir():
                try:
                    fds[f.name] = os.readlink(f)
                except OSError as exc:
                    fds[f.name] = dict(error=str(exc))
        except OSError as exc:
            fds = dict(error=str(exc))
        processes.append(dict(pid=int(p.name), cmdline=cmd, fds=fds))
    cfg = Path('/etc/km3/device.json')
    try:
        device_id = json.loads(cfg.read_text()).get('device', {}).get('id')
    except Exception as exc:
        device_id = dict(error=str(exc))
    try:
        model = Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode()
    except OSError as exc:
        model = dict(error=str(exc))
    root = Path(log_root)
    expected = Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/payload/logs/relative-minus10-100rpm-a131072-once-20261019')
    if root != expected or root.is_symlink():
        raise ValueError('仅允许固定本次日志目录，禁止符号链接')
    logs, total = [], 0
    if root.exists():
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(directory)/d).is_symlink())
            for name in sorted(files):
                path = Path(directory)/name
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                item = dict(path=str(path), size=size)
                if size > 4194304 or total + size > 12582912:
                    item['collection_error'] = 'bounded_collection_limit'
                else:
                    raw = path.read_bytes()
                    total += len(raw)
                    item.update(sha256=hashlib.sha256(raw).hexdigest(), content_base64=base64.b64encode(raw).decode())
                logs.append(item)
    return dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), hostname=socket.gethostname(),
        model=model, boot_id=read(Path('/proc/sys/kernel/random/boot_id')), device_id=device_id,
        config_sha=sha(cfg), can_env_sha=sha(Path('/etc/km3/can.env')),
        current=os.path.realpath('/opt/km3/hwtools/current'), services=services,
        related_processes_excluding_snapshot_pid=sorted(processes, key=lambda p:p['pid']), snapshot_pid=os.getpid(),
        can_rcvlists={p.name:read(p) for p in Path('/proc/net/can').glob('rcvlist*')},
        can_link=run(['ip', '-details', '-statistics', '-json', 'link', 'show', 'can1']),
        log_root=str(root), log_root_exists=root.exists(), logs=logs, log_bytes_collected=total,
        remote_file_writes=0, CAN_socket_opens=0, CAN_sends=0, process_control_operations=0)


if __name__ == '__main__':
    print(json.dumps(snapshot(sys.argv[1]), ensure_ascii=False, indent=2))
