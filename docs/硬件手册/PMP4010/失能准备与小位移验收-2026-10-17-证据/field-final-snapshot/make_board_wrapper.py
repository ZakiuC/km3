from pathlib import Path
r=Path('/tmp/km3-pp-motion-20261017')
mock=(r/'mock_app.py').read_text()
taps=mock[mock.index('    original_read='):mock.index('    rc=1\n')]
patches=mock[mock.index("            stack.enter_context(mock.patch.object(tool._canopen.SdoClient,'read'"):mock.index('            # Profiles remain from telemetry;')]
# unittest.mock is only an attribute-restoration helper; no test fixtures are used.
header='''#!/usr/bin/env python3
"""Fixed-payload PmpApp launcher with observational taps only.

Default verifies package/import provenance offline and exits without CAN.
--run-tui starts the original app at its RT acknowledgement page. This flag
is not authorization. All reviews, settings, OD/RT consent and tokens are keys.
"""
import argparse
from contextlib import ExitStack
import datetime
from functools import wraps
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest import mock

sys.dont_write_bytecode=True
PAYLOAD=Path('/tmp/km3-pp-prepare-load-20261017-1YfFxQ5O/payload')
EXPECTED_MANIFEST='3dfc3a5c10c13d4c3f49691f259a9a7097cc480f9cb6b604e34f0a554a97b2b5'
EXPECTED_CONFIG='96a15d8d28f760ab9c5b8f43f6b5ddccc667c77f89413fcd9c3201aa9ddbb205'
OUTPUT=PAYLOAD.parent/'pp-session-1'
AUTHORIZATION_CONTEXT='用户在本会话确认现场安全并授权一次小角度验证；仅本次PP1约0.1°，既有7项缺口保留；另已批准失能准备607A对齐和6081=100。此记录不替代UI确认。'


def verify_payload(root):
    root=root.resolve(strict=True)
    manifest=root/'payload-manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==EXPECTED_MANIFEST,'manifest mismatch'
    entries=json.loads(manifest.read_text())['files']
    for entry in entries:
        rel=Path(entry['path'])
        assert not rel.is_absolute() and '..' not in rel.parts
        path=root/rel
        assert root in path.resolve(strict=True).parents
        if 'symlink' in entry:
            assert path.is_symlink() and os.readlink(path)==entry['symlink']
        else:
            assert path.stat().st_size==entry['size']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],str(path)
    sys.path[:0]=[str(root/'drivers/pmp4010'),str(root/'hwtui')]
    import pmp4010 as tool
    assert tool.require_std_layer('固定包测试台'), 'standard layer unavailable'
    import pmp4010_bench as bench
    import pmp4010_tui as tui
    from tui import Field
    import pmp4010_position as position
    import pmp4010_params as params
    import pmp4010_velocity as velocity
    import canopen_consts,canopen_wire
    modules=(tool,bench,tui,position,params,velocity,tool._canopen,tool._canbus,canopen_consts,canopen_wire,sys.modules['tui'])
    sources=[]
    for module in modules:
        path=Path(module.__file__).resolve(strict=True)
        assert root in path.parents,'import escaped payload: '+str(path)
        sources.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    od=Path(tool.od_json_path()).resolve(strict=True)
    assert root in od.parents
    assert hashlib.sha256(od.read_bytes()).hexdigest()=='5bf6c7bbd73c1c6ef3855fb9ef104d8b6d72ecec8a598eeff16f71682934e58f'
    return tool,bench,tui,Field,position,sources


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-tui',action='store_true')
    parser.add_argument('--offline-source-root',type=Path)
    args=parser.parse_args()
    if args.run_tui and args.offline_source_root:
        parser.error('live source root is fixed; offline override cannot run TUI')
    root=args.offline_source_root or PAYLOAD
    tool,bench,tui,Field,position,sources=verify_payload(root)
    if not args.run_tui:
        print(json.dumps(dict(result='PASS',mode='offline_verify_only',manifest_sha256=EXPECTED_MANIFEST,sources=sources,actual_CAN_opens=0),ensure_ascii=False,indent=2))
        return 0
    assert root==PAYLOAD and root.resolve(strict=True)==PAYLOAD
    cfgpath=Path('/etc/km3/device.json')
    assert hashlib.sha256(cfgpath.read_bytes()).hexdigest()==EXPECTED_CONFIG,'configuration changed'
    cfg,path=tool.load_device_json()
    item,_=tool.find_can_bus(cfg,tool.LOGICAL_BUS)
    assert path==str(cfgpath) and cfg.get('device',{}).get('id')=='km3-test-001'
    assert socket.gethostname()=='RK3588-Tronlong'
    assert Path('/proc/device-tree/model').read_bytes().rstrip(b'\\0')==b'TL3588-EVM'
    assert item['iface']=='can1' and item['bitrate']==1000000
    library=position.require_velocity_library()
    libpath=Path(library._name).resolve(strict=True)
    assert PAYLOAD in libpath.parents and library.pmp4010_velocity_abi_version()==1
    assert hashlib.sha256(libpath.read_bytes()).hexdigest()=='59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
    out=OUTPUT
    out.mkdir(mode=0o700,exist_ok=False)
    os.chdir(out)
    log=(out/'app-audit.jsonl').open('x')
    lock=threading.Lock()
    def emit(event,**data):
        with lock:
            log.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic=time.monotonic(),event=event,**data),ensure_ascii=False,default=str)+'\\n')
            log.flush()
    emit('board_session',hardware_transport=True,payload=str(PAYLOAD),manifest_sha256=EXPECTED_MANIFEST,source_list=sources,authorization_context=AUTHORIZATION_CONTEXT,rt_stopped_claim=False,interface='can1',node_range='1',timeout=.25,state_timeout=2,move_timeout=8)
    for source in sources:
        emit('source',**source)
'''
footer='''    rc=1
    try:
        with ExitStack() as stack:
'''+patches+'''            # No consent/telemetry/review mutation. Parser defaults require
            # keyboard RT/OD approval and F2 bounds/budget before any action.
            config=tool.build_parser().parse_args(['--node','1','--node-range','1','--timeout','0.25','--state-timeout','2','--move-timeout','8','--rows','36','--cols','160'])
            app=tui.PmpApp(tool,config)
            assert app.current_view=='rt' and not app.controller.rt_ack and not app.controller.od_reason
            assert app.pending is None and not app.controller.enabled_sessions
            assert all(value is None for value in app.controller.settings.values())
            if not app._curses_usable():
                raise RuntimeError('interactive curses PTY required; no fallback or CAN opened')
            rc=app.run()
        return rc
    finally:
        emit('board_app_exit',rc=rc)
        log.close()


if __name__=='__main__':
    sys.exit(main())
'''
(r/'board_app.py').write_text(header+taps+footer)
print('board_app.py written; not launched')
