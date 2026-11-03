"""7355 固定包加载器；导入和 cold-load 均不打开 CAN。"""
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

ROOT = Path('/var/tmp/km3-pmp4010-alarm-20261019-735538978204/payload')
MANIFEST_SHA = '5072e207253957356b28f03d989efa3b798efa85b240e196060d6eb40495e40b'
LIB_SHA = '59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
BOOT = 'dc67bdcd-fa50-4927-8f9b-7207359c70af'
CONFIG_SHA = '96a15d8d28f760ab9c5b8f43f6b5ddccc667c77f89413fcd9c3201aa9ddbb205'
ENV_SHA = '9253d719d847eed56ea7ef768985c0215faf0f78dfc98838152c734a0feff407'
MODULES = ('pmp4010', 'pmp4010_simple', 'pmp4010_manual', 'pmp4010_bench',
           'pmp4010_position', 'pmp4010_params', 'pmp4010_tui', 'pmp4010_faults',
           'pmp4010_velocity', 'canopen', 'canbus', 'canopen_wire', 'canopen_consts')


def emit(event, **data):
    print(json.dumps(dict(event=event, utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          monotonic_ns=time.monotonic_ns(), **data), ensure_ascii=False,
                     default=str), flush=True)


class Output:
    def __init__(self):
        self.cancel_event = threading.Event()
        self.hook = None

    def _line(self, text, *args, **kwargs):
        emit('信息', text=text)
    info = plain = title = cmd = ok = bad = warn = _line

    def event(self, name, data):
        emit(name, **data)
        if self.hook is not None:
            self.hook(name, data)  # Deliberately propagate into original cleanup.

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise KeyboardInterrupt()


def load_pinned(root=ROOT):
    root = Path(root)
    if root.resolve(strict=True) != root:
        raise ValueError('payload目录变化或为符号链接')
    raw = (root / 'payload-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA:
        raise ValueError('固定manifest变化')
    members = {}
    for item in json.loads(raw)['files']:
        path = root / item['path']
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('固定成员越界：' + item['path'])
        if 'symlink' in item:
            if not path.is_symlink() or path.readlink().as_posix() != item['symlink']:
                raise ValueError('固定符号链接变化：' + item['path'])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('固定成员变化：' + item['path'])
        else:
            members[path.resolve()] = item['sha256']
    library = root / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'
    if hashlib.sha256(library.read_bytes()).hexdigest() != LIB_SHA:
        raise ValueError('固定ARM库变化')
    for name in MODULES:
        if name in sys.modules:
            raise ValueError('要求冷启动加载，模块已存在：' + name)
    sys.path[:0] = [str(root / 'drivers/pmp4010'), str(root / 'hwtui')]
    tool = importlib.import_module('pmp4010')
    if not tool.std_layer_available():
        raise ValueError('固定标准层加载失败：' + str(tool._HWTUI_ERROR))
    for name in MODULES:
        importlib.import_module(name)
    origins = {}
    for name in MODULES:
        path = Path(sys.modules[name].__file__).resolve(strict=True)
        if path not in members:
            raise ValueError('模块来源越过固定包：' + name)
        origins[name] = dict(path=str(path), sha256=members[path])
    if tool._canopen is not sys.modules['canopen'] or tool._canbus is not sys.modules['canbus']:
        raise ValueError('标准层模块身份变化')
    emit('固定包已冷加载', root=str(root), manifest_sha256=MANIFEST_SHA,
         ARM_library_sha256=LIB_SHA, origins=origins, CAN_opens=0)
    return tool, sys.modules['pmp4010_simple'].SimpleController


def os_preflight():
    if socket.gethostname() != 'RK3588-Tronlong':
        raise ValueError('主机身份变化')
    if Path('/proc/device-tree/model').read_bytes().rstrip(b'\0') != b'TL3588-EVM':
        raise ValueError('主板型号变化')
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != BOOT:
        raise ValueError('boot_id变化')
    for path, expected in (('/etc/km3/device.json', CONFIG_SHA), ('/etc/km3/can.env', ENV_SHA)):
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError('配置hash变化：' + path)
    if json.loads(Path('/etc/km3/device.json').read_text())['device']['id'] != 'km3-test-001':
        raise ValueError('设备id变化')
    if Path('/opt/km3/hwtools/current').resolve() != Path('/opt/km3/hwtools/releases/0.1.5+zh-5e31e29'):
        raise ValueError('current变化')
    for path in Path('/proc/net/can').glob('rcvlist*'):
        for line in path.read_text().splitlines():
            if line.strip() and not ('receive list' in line or 'no entry' in line):
                raise ValueError('CAN接收表被占用：' + line)
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            command = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except FileNotFoundError:
            continue
        if any(word in command for word in ('pmp4010', 'pty_runner', 'mock_app', 'km3-pp',
                                            'drive_probe', 'board_app', 'candump', 'cansend', 'cangen', 'canplayer',
                                            'motion_compare_once')):
            raise ValueError('诊断进程占用：' + proc.name + ' ' + command)
    # Freeze the approved passive snapshot, including the active RT service.
    # Active service is not evidence of CAN ownership: its exact known fds and
    # all CAN receive lists are separately checked. Never stop/restart it here.
    expected_services = {
        'km3-core': ('2134', '10563138', '10563360'),
        'km3-rt-controller': ('600', '3294993', '3295172'),
        'km3-video': ('2135', '10565127', '10565346')}
    services = {}
    for service, (pid, started, entered) in expected_services.items():
        result = subprocess.run(['systemctl', 'show', service,
            '--property=MainPID,ActiveState,SubState,ExecMainStartTimestampMonotonic,ActiveEnterTimestampMonotonic'],
            check=True, capture_output=True, text=True, timeout=2)
        state = dict(line.split('=',1) for line in result.stdout.splitlines() if line)
        expected = dict(MainPID=pid, ActiveState='active', SubState='running',
                        ExecMainStartTimestampMonotonic=started, ActiveEnterTimestampMonotonic=entered)
        if state != expected:
            raise ValueError('服务与已批准被动快照不同：' + service)
        services[service] = state
    rt_fds = {fd.name: os.readlink(fd) for fd in Path('/proc/600/fd').iterdir()}
    if rt_fds != {'0':'/dev/null', '1':'socket:[3640]', '2':'socket:[3640]'}:
        raise ValueError('RT进程fd与已批准无CAN快照不同')
    result = subprocess.run(['ip', '-details', '-statistics', '-json', 'link', 'show', 'can1'],
                            check=True, capture_output=True, text=True, timeout=2)
    link, = json.loads(result.stdout)
    if not (link['ifname'] == 'can1' and link['mtu'] == 16 and 'UP' in link['flags']
            and link['linkinfo']['info_data']['state'] == 'ERROR-ACTIVE'
            and link['linkinfo']['info_data']['bittiming']['bitrate'] == 1000000
            and all(v == 0 for v in link['linkinfo']['info_xstats'].values())
            and all(link['stats64'][direction][key] == 0
                    for direction in ('rx', 'tx') for key in ('errors', 'dropped'))):
        raise ValueError('can1状态/速率/错误计数变化')
    emit('系统前置核对完成', boot_id=BOOT, can_link=link, services=services, rt_fds=rt_fds)


def make_controller(tool, controller_type, log_base):
    args = tool.build_parser().parse_args([
        '--manual', '--manual-linear', '--manual-mm-per-rev', '4',
        '--node', '1', '--node-range', '1', '--timeout', '0.25',
        '--motion-log', str(log_base)])
    controller = controller_type(tool, args)
    controller.rt_ack = True
    return controller
