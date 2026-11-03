"""Proposed disabled-only bit7 edge, independent of motion; no live use yet."""
import argparse
import copy
from pathlib import Path
import sys
import time

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pinned_payload import ROOT, MANIFEST_SHA, Output, emit, load_pinned, make_controller

SEQUENCE=(('read',0x2002,1),('read',0x6041,0),('read',0x603f,0),
          ('write',0x6040,0),('write',0x6040,0x80),('write',0x6040,0),
          ('read',0x603f,0),('read',0x6041,0))


class WireGuard:
    """Exact one-pending 8-frame whitelist; raw TX/RX retained in stdout JSONL."""
    def __init__(self,bus,module):
        self.bus,self.module=bus,module
        self.cursor,self.attempts=0,0
        self.pending=None
        self.failed=False
        self.end=time.monotonic()+3
    def reject(self,message):
        self.failed=True
        raise RuntimeError(message)
    def begin(self,token):
        if self.failed or self.pending is not None or self.cursor>=8 or token!=SEQUENCE[self.cursor]:
            self.reject('复位交易顺序不在8交易白名单')
        if time.monotonic()>=self.end:
            self.reject('复位总3秒预算已耗尽')
        op,index,sub_or_word=token
        sub=0 if op=='write' else sub_or_word
        self.expected=(bytes((0x2b,index&255,index>>8,sub))+sub_or_word.to_bytes(2,'little')+bytes(2)
                       if op=='write' else bytes((0x40,index&255,index>>8,sub,0,0,0,0)))
        self.pending=token
        self.sent=False
        self.request_end=min(self.end,time.monotonic()+.25)
    def send(self,frame):
        if (self.failed or self.pending is None or self.sent or self.attempts>=8
                or frame.arbitration_id!=0x601 or frame.extended or frame.remote
                or frame.frame_type!=self.module.FRAME_CLASSIC or frame.data!=self.expected):
            self.reject('复位发送帧不在精确白名单')
        remaining=self.request_end-time.monotonic()
        if remaining<=0:
            self.reject('复位发送期限已耗尽')
        self.sent=True
        self.attempts+=1
        emit('复位TX',transaction=self.cursor+1,cob_id='601',raw_hex=frame.data.hex(),attempt=self.attempts)
        try:
            sock=getattr(self.bus,'_sock',None)
            if sock is not None:
                sock.settimeout(remaining)
            self.bus.send(frame)
        except BaseException:
            self.failed=True
            raise
    def recv(self,timeout=None):
        if self.failed or self.pending is None or not self.sent:
            self.reject('复位接收缺少唯一pending请求')
        remaining=self.request_end-time.monotonic()
        if remaining<=0:
            self.reject('复位接收期限已耗尽')
        try:
            frame=self.bus.recv(min(remaining,timeout) if timeout is not None else remaining)
        except BaseException:
            self.failed=True
            raise
        if frame is None:
            self.reject('复位应答超时；不重试')
        emit('复位RX',transaction=self.cursor+1,cob_id='{:03X}'.format(frame.arbitration_id),
             raw_hex=frame.data.hex(),extended=frame.extended,remote=frame.remote,frame_type=frame.frame_type)
        expected_cs=0x60 if self.pending[0]=='write' else 0x4b
        if (time.monotonic()>self.request_end or frame.arbitration_id!=0x581
                or frame.extended or frame.remote or frame.frame_type!=self.module.FRAME_CLASSIC
                or len(frame.data)!=8 or frame.data[0]!=expected_cs or frame.data[1:4]!=self.expected[1:4]):
            self.reject('复位应答非精确pending的UInt16/写ACK；立即结束')
        self.pending=None
        self.cursor+=1
        return frame
    def close(self):
        self.bus.close()


def perform_reset_only(ctl, out, authorization_reason):
    """Eight ordered standard SDO calls; any failure ends without cleanup writes."""
    try:
        if not authorization_reason or not authorization_reason.strip():
            raise ValueError('缺少已有手动会话的明确OD授权理由')
        gate_args=copy.copy(ctl.args)
        gate_args.od_gaps_bypass=authorization_reason
        emit('本次手动会话授权理由',reason=authorization_reason)
        if not ctl.tool.refuse_motion(out,'已授权手动会话的脱使能单次复位',gate_args):
            raise ValueError('原OD闸门拒绝复位')
        ctl._configuration(out)  # Original config/interface/RT acknowledgement guards.
        if ctl.context!=('can1',1000000) or ctl.node!=1 or ctl.args.node!=1 or ctl.args.node_range!='1':
            raise ValueError('实际配置不再是can1/1Mbps/节点1；禁止复位')
        wire=WireGuard(ctl.bus,ctl.tool._canbus)
        ctl.bus=wire
        client=ctl.tool._canopen.SdoClient(wire,1,timeout=.25)
        end=time.monotonic()+3
        def transaction(method,*args):
            remaining=end-time.monotonic()
            if remaining<=0 or getattr(client,'quarantined',False):
                raise TimeoutError('复位单次预算耗尽或client隔离；不重试')
            if len(args)==3:
                token=('write',args[0],args[2])
            else:
                token=('read',args[0],args[1])
            wire.begin(token)
            client.timeout=min(.25,remaining)
            value=method(*args)
            if time.monotonic()>end:
                raise TimeoutError('复位事务超过总预算；不继续')
            return value
        mode=transaction(client.read_uint16,0x2002,1)
        status=transaction(client.read_uint16,0x6041,0)
        code=transaction(client.read_uint16,0x603f,0)
        p=ctl.tool._canopen
        emit('复位前实读',mode=mode,statusword='0x{:04X}'.format(status),error_code='0x{:04X}'.format(code))
        if mode!=0 or status & p.CIA402_DISABLED_MASK!=p.CIA402_DISABLED or status & p.CIA402_FAULT or code!=0x2312:
            raise ValueError('未满足2002=0、disabled、Fault=0、603F=2312；禁止任何复位写')
        for word in (0,0x80,0):
            transaction(client.write_uint16,0x6040,0,word)
            emit('复位控制字写确认',controlword='0x{:04X}'.format(word))
        after_code=transaction(client.read_uint16,0x603f,0)
        after_status=transaction(client.read_uint16,0x6041,0)
        result=dict(error_code='0x{:04X}'.format(after_code),statusword='0x{:04X}'.format(after_status),
                    motion_authorized=False,mechanical_stop_confirmed=False,
                    warning_or_last_error_clear_not_guaranteed=True,SDO_attempts=wire.attempts)
        emit('复位后单次实读',**result)
        return result
    finally:
        # No MotionSession, enable, stop, fault cleanup, or extra read here.
        if ctl.bus is not None:
            ctl.bus.close()
            ctl.bus=None


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-reset-only-once',action='store_true')
    parser.add_argument('--manual-session-reason',default=None)
    args=parser.parse_args(argv)
    emit('待审复位计划',payload=str(ROOT),manifest_sha256=MANIFEST_SHA,node=1,
         sequence=SEQUENCE,per_transaction_seconds=.25,total_seconds=3,
         motion_operations=0,automatic_retries=0,live_requested=args.execute_reset_only_once)
    if not args.execute_reset_only_once:
        emit('仅本地计划',CAN_opens=0,CAN_sends=0)
        return 0
    if not args.manual_session_reason or not args.manual_session_reason.strip():
        parser.error('执行前必须提供本次已有手动会话的--manual-session-reason')
    tool,controller_type=load_pinned()
    run_dir=ROOT/'logs/reset-only-once-20261019'
    run_dir.mkdir(parents=True,exist_ok=False)
    ctl=make_controller(tool,controller_type,run_dir/'unused-motion.jsonl')
    try:
        perform_reset_only(ctl,Output(),args.manual_session_reason)
        return 0
    except BaseException as exc:
        emit('复位结束且不重试',exception=type(exc).__name__,message=str(exc),motion_authorized=False)
        return 130 if isinstance(exc,KeyboardInterrupt) else 1


if __name__=='__main__':
    raise SystemExit(main())
