#!/usr/bin/env python3
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
PAYLOAD=Path('/tmp/km3-pp-disabled-start-stage3-recovery-20261019-0213/payload')
EXPECTED_MANIFEST='280884d0e1cf519675a590aa053dc16419d0266555683f61d93fcef6bcce0c5c'
EXPECTED_CONFIG='96a15d8d28f760ab9c5b8f43f6b5ddccc667c77f89413fcd9c3201aa9ddbb205'
OUTPUT=PAYLOAD.parent/'move-session-1'
AUTHORIZATION_CONTEXT='断电后新会话：原RT/F2软限起点±36U及budget36/OD均键入；Ready先原D两段确认到strictdisabled，再R+36两段确认。每次motion必须全新106/107/127/137/127预装当前目标及独立freshACK/6062、12F Halt使能五次严格零漂移、2F清Halt仍原点零速ACK0、final607A/readback、3F新finalACK及2F新ACK0/非Haltbit10/目标实际位置/6062/零速，最后2/0；全部15秒同client，失败隔离不重试，q另记Shutdown6。D/R冻结页分别待主线程批准；断电前seal与target proof全部无效。'


def verify_payload(root):
    root=root.resolve(strict=True)
    manifest=root/'payload-manifest.json'
    if root==PAYLOAD:
        assert hashlib.sha256((PAYLOAD.parent/'frozen-payload.tar.gz').read_bytes()).hexdigest()=='b70396b228cb594793b44ad6d6f4dfc65a0167fd0efd90325d8feec276f69c5e','archive mismatch'
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
    assert hashlib.sha256(od.read_bytes()).hexdigest()=='1cc28b19cf94349d83497af7c252a5fa68702af433df7fd7afd431b6cdfa6a6e'
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
    assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='bacd8411-f822-4f19-b7cb-8bcbf755b54a','boot changed; new passive acceptance required'
    rt_cmdline=Path('/proc/558/cmdline').read_bytes().replace(b'\0',b' ').decode()
    assert rt_cmdline=='/opt/km3/current/km3-rt-controller --config /etc/km3/device.json --require-config ','RT process changed'
    assert set(p.name for p in Path('/proc/558/fd').iterdir())=={'0','1','2'},'RT descriptors changed'
    for p in Path('/proc/net/can').glob('rcvlist*'):
        assert all(not line.strip() or line.startswith("receive list") or 'no entry' in line for line in p.read_text().splitlines()),'CAN receiver present'
    cfgpath=Path('/etc/km3/device.json')
    assert hashlib.sha256(cfgpath.read_bytes()).hexdigest()==EXPECTED_CONFIG,'configuration changed'
    cfg,path=tool.load_device_json()
    item,_=tool.find_can_bus(cfg,tool.LOGICAL_BUS)
    assert path==str(cfgpath) and cfg.get('device',{}).get('id')=='km3-test-001'
    assert socket.gethostname()=='RK3588-Tronlong'
    assert Path('/proc/device-tree/model').read_bytes().rstrip(b'\0')==b'TL3588-EVM'
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
            log.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic=time.monotonic(),event=event,**data),ensure_ascii=False,default=str)+'\n')
            log.flush()
    emit('board_session',hardware_transport=True,payload=str(PAYLOAD),manifest_sha256=EXPECTED_MANIFEST,source_list=sources,authorization_context=AUTHORIZATION_CONTEXT,rt_stopped_claim=False,interface='can1',node_range='1',timeout=.25,state_timeout=2,move_timeout=15)
    for source in sources:
        emit('source',**source)
    original_read=tool._canopen.SdoClient.read
    original_write=tool._canopen.SdoClient.write
    @wraps(original_read)
    def traced_read(self,index,subindex=0):
        emit('sdo_upload_begin',client_id=id(self),node=self.node_id,index=index,sub=subindex)
        try:
            value=original_read(self,index,subindex)
        except BaseException as exc:
            emit('sdo_upload_exception',client_id=id(self),index=index,sub=subindex,type=type(exc).__name__,message=str(exc),quarantined=self.quarantined)
            raise
        emit('sdo_upload_done',client_id=id(self),index=index,sub=subindex,raw_hex=value.hex())
        return value
    @wraps(original_write)
    def traced_write(self,index,subindex,value):
        emit('sdo_download_begin',client_id=id(self),node=self.node_id,index=index,sub=subindex,raw_hex=bytes(value).hex())
        try:
            result=original_write(self,index,subindex,value)
        except BaseException as exc:
            emit('sdo_download_exception',client_id=id(self),index=index,sub=subindex,type=type(exc).__name__,message=str(exc),quarantined=self.quarantined)
            raise
        emit('sdo_download_done',client_id=id(self),index=index,sub=subindex,raw_hex=bytes(value).hex())
        return result
    original_event=tui.JobOutput.event
    @wraps(original_event)
    def observed_event(self,name,data):
        emit('job_event',name=name,data=data)
        return original_event(self,name,data)
    original_line=tui.JobOutput._line
    @wraps(original_line)
    def observed_line(self,text,level='info'):
        emit('job_line',level=level,text=str(text))
        return original_line(self,text,level)

    def ui_state(app):
        field=getattr(app,'input_field',None)
        pending=getattr(app,'pending',None)
        row=app.selected_row
        return dict(view=app.current_view,action=app.action,busy=app.busy,
                    field_value=getattr(field,'value',None),field_draft=getattr(field,'text',None),
                    field_editing=getattr(field,'editing',None),
                    pending={k:pending.get(k) for k in ('action','node','target','start','token','lines','writes')} if pending else None,
                    row_values=row.get('values'),row_updated=row.get('updated'),row_lost=row.get('lost'),
                    row_quarantined=row.get('quarantined'),recovery=app.recovery,
                    enabled_session_nodes=sorted(app.controller.enabled_sessions),
                    blocked=[str(k) for k in app.controller.blocked])
    def observe_method(name):
        original=getattr(tui.PmpApp,name)
        @wraps(original)
        def wrapped(self,*a,**kw):
            emit('ui_method_before',method=name,state=ui_state(self),arguments=list(a))
            try:
                return original(self,*a,**kw)
            finally:
                emit('ui_method_after',method=name,state=ui_state(self))
        return wrapped
    original_commit=Field.commit
    @wraps(original_commit)
    def observed_commit(self):
        emit('form_commit_before',value=self.value,draft=self.text)
        result=original_commit(self)
        emit('form_commit_after',value=self.value,draft=self.text,result=result,error=self.error)
        return result
    previous=[None]
    original_poll=tui.PmpApp.poll
    @wraps(original_poll)
    def observed_poll(self):
        result=original_poll(self)
        state=ui_state(self)
        # Emit transitions and fresh snapshots; no operation or field changes.
        if state!=previous[0]:
            emit('ui_state',state=state)
            previous[0]=state
        return result
    rc=1
    try:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(tool._canopen.SdoClient,'read',traced_read))
            stack.enter_context(mock.patch.object(tool._canopen.SdoClient,'write',traced_write))
            stack.enter_context(mock.patch.object(tui.JobOutput,'event',observed_event))
            stack.enter_context(mock.patch.object(tui.JobOutput,'_line',observed_line))
            for alias in ('info','plain','title','cmd'):
                stack.enter_context(mock.patch.object(tui.JobOutput,alias,observed_line))
            for name in ('review_action','_execute','dispatch_key'):
                stack.enter_context(mock.patch.object(tui.PmpApp,name,observe_method(name)))
            stack.enter_context(mock.patch.object(Field,'commit',observed_commit))
            stack.enter_context(mock.patch.object(tui.PmpApp,'poll',observed_poll))
            # No consent/telemetry/review mutation. Parser defaults require
            # keyboard RT/OD approval and F2 bounds/budget before any action.
            config=tool.build_parser().parse_args(['--node','1','--node-range','1','--timeout','0.25','--state-timeout','2','--move-timeout','15','--rows','36','--cols','160'])
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
