"""Local preparation helpers. Importing this file performs no device I/O."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import threading

ROOT = Path('/var/tmp/km3-pmp4010-diag-20261019-0218ab4207eb/payload')
MANIFEST_SHA = 'a41b88269819e718e1486876be8d99502a773a401ef084343b57d597fb818b71'


def emit(event, **data):
    print(json.dumps(dict(event=event, **data),ensure_ascii=False,default=str),flush=True)


class Output:
    def __init__(self):
        self.cancel_event = threading.Event()
    def _line(self, text, *args, **kwargs):
        emit('信息',text=text)
    info = plain = title = cmd = ok = bad = warn = _line
    def event(self, name, data):
        emit(name, **data)
    def check_cancel(self):
        if self.cancel_event.is_set():
            raise KeyboardInterrupt()


def load_pinned():
    if ROOT.resolve(strict=True) != ROOT:
        raise ValueError('payload目录变化或为符号链接')
    raw=(ROOT/'payload-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA:
        raise ValueError('批准的manifest已变化')
    for item in json.loads(raw)['files']:
        path=ROOT/item['path']
        if not path.resolve(strict=True).is_relative_to(ROOT):
            raise ValueError('manifest成员越过payload目录')
        if 'symlink' in item:
            if not path.is_symlink() or path.readlink().as_posix()!=item['symlink']:
                raise ValueError('符号链接变化：'+str(path))
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('成员hash变化：'+str(path))
    sys.path[:0]=[str(ROOT/'drivers/pmp4010'),str(ROOT/'hwtui')]
    spec=importlib.util.spec_from_file_location('negative_once_pmp4010',ROOT/'drivers/pmp4010/pmp4010.py')
    tool=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=tool
    spec.loader.exec_module(tool)
    simple=importlib.import_module('pmp4010_simple')
    for name in ('pmp4010_simple','pmp4010_manual','pmp4010_bench','pmp4010_position','canopen','canbus'):
        if not Path(sys.modules[name].__file__).resolve().is_relative_to(ROOT):
            raise ValueError('模块来源越过批准包：'+name)
    emit('固定包已核对',root=str(ROOT),manifest_sha256=MANIFEST_SHA)
    return tool,simple.SimpleController


def make_controller(tool, controller_type, log_base):
    args=tool.build_parser().parse_args(['--manual','--manual-linear','--manual-mm-per-rev','4',
        '--node','1','--node-range','1','--motion-log',str(log_base)])
    ctl=controller_type(tool,args)
    # These are the original UI's explicit session consent APIs. Live use is
    # gated by each wrapper's flag and the parent/user's reviewed authorization.
    ctl.rt_ack=True
    return ctl
