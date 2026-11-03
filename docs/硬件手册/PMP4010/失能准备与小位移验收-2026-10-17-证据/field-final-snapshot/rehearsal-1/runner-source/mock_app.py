#!/usr/bin/env python3
"""Local-only, real PmpApp.run() with standard MockBus and observation taps.

All taps call the original implementation exactly once. No UI approvals/fields
are seeded and no action is invoked outside the original terminal event path.
"""
import argparse
from contextlib import ExitStack
import datetime
from functools import wraps
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from unittest import mock

sys.dont_write_bytecode=True
ROOT=Path('/home/zakiu/km3')
sys.path[:0]=[str(ROOT/'device/drivers/pmp4010/tools'),str(ROOT/'tools/hwtui')]
from _test_bench_support import BenchNode, environment, mockbus, options, tool
import pmp4010_bench as bench
import pmp4010_tui as tui
from tui import Field


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    out=args.output_dir
    log=(out/'app-audit.jsonl').open('x')
    lock=threading.Lock()
    def emit(event,**data):
        with lock:
            log.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic=time.monotonic(),event=event,**data),ensure_ascii=False,default=str)+'\n')
            log.flush()
    for module in (tool,bench,tui,tool._canopen,tool._canbus):
        path=Path(module.__file__).resolve()
        emit('source',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if 'prepare_pp' not in bench.ACTION_POLICY:
        emit('fixture_not_ready',message='prepare_pp has not landed; no app or bus opened')
        return 3

    class PrepareNode(BenchNode):
        def handle(self,frame,now):
            super().handle(frame,now)
            # Explicit synthetic firmware behaviour, never a claim about board.
            # This is the favourable prepare outcome needed to rehearse PP UI.
            if (frame.arbitration_id==0x601 and frame.data[0]==0x23
                    and frame.data[1:4]==bytes([0x7a,0x60,0])):
                if self.value(0x607a,signed=True)==self.value(0x6064,signed=True):
                    self.set_value(0x6041,self.value(0x6041)|0x400)
                    emit('synthetic_bit10_after_target_alignment',value=True,hardware_fact=False)
    node=PrepareNode()
    for index,sub,value in ((0x6040,0,0),(0x6041,0,0x260),(0x6064,0,-1465),
                            (0x607a,0,7800),(0x6081,0,500000),(0x6083,0,5000000),
                            (0x6084,0,5000000),(0x607d,1,-2147483628),
                            (0x607f,0,9000000),(0x6080,0,4000),
                            (0x60c5,0,2147483647),(0x60c6,0,2147483647)):
        node.set_value(index,value,sub)
    bus=mockbus.MockBus([node],loopback=False)
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
            for patch in environment(bus):
                stack.enter_context(patch)
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
            # Profiles remain from telemetry; RT/OD/token entered via real keys.
            config=options()
            config.node_range='1'
            config.soft_min=-1465
            config.soft_max=-1429
            config.max_displacement=None
            config.move_timeout=5
            config.state_timeout=1
            app=tui.PmpApp(tool,config)
            rc=app.run()
        return rc
    finally:
        emit('mock_app_exit',rc=rc,closed=bus.closed,hardware_fact=False)
        (out/'mock-trace.json').write_text(json.dumps(dict(transport='standard MockBus',closed=bus.closed,
          hardware_motion_acceptance=False,requests=[dict(id=f.arbitration_id,data=f.data.hex()) for f in bus.sent]),ensure_ascii=False,indent=2)+'\n')
        log.close()


if __name__=='__main__':
    sys.exit(main())
