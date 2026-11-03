"""Focused local-only reset experiment checks. No device socket permitted."""
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import socket
import struct
import subprocess
import sys
from unittest import mock

BASE=pathlib.Path(__file__).resolve().parent
PAYLOAD=BASE.parents[1]/'报警内容显示-2026-10-19-证据/package/payload'
spec=importlib.util.spec_from_file_location('reset_observe_once',BASE/'reset_observe_once.py')
p=importlib.util.module_from_spec(spec);sys.modules[spec.name]=p;spec.loader.exec_module(p)
checks=[]


class Clock:
    def __init__(self):self.value=0
    def now(self):return self.value
    def sleep(self,seconds):assert seconds>=0;self.value+=seconds


class FakeSocket:
    def __init__(self,clock,profile='delayed',at=None,fault=None):
        self.clock,self.profile,self.at,self.fault=clock,profile,at,fault
        self.sent=[];self.closes=0;self.acks=[];self.options={}
    def settimeout(self,seconds):assert 0<seconds<=.25
    def setsockopt(self,level,key,value):self.options[(level,key)]=value
    def bind(self,address):assert address==('can1',)
    def close(self):self.closes+=1
    def send(self,raw):
        n=len(self.sent)+1;op,idx,sub,size,word=p.SEQUENCE[n-1]
        cid,dlc,data=struct.unpack('<IB3x8s',raw)
        expected=bytes((0x2B if op=='write' else 0x40,idx&255,idx>>8,sub))
        expected+=(word.to_bytes(2,'little')+bytes(2)) if op=='write' else bytes(4)
        assert cid==0x601 and dlc==8 and data==expected
        self.sent.append((raw,self.clock.value))
        if n==self.at and self.fault=='send_error':raise OSError('mock send error')
        if n==self.at and self.fault=='short_send':return 15
        return 16
    def recv(self,size):
        assert size==17
        n=len(self.sent);op,idx,sub,width,word=p.SEQUENCE[n-1]
        value={0x2002:0,0x6041:0x2C0,0x603F:0x2312,0x1001:0,0x200E:0x03002312,0x6040:0}[idx]
        if n in (10,13,19,22):
            clear=(self.profile=='delayed' and n!=10) or self.profile in ('retained','low_code_early_only','low_code_late_only') or (self.profile=='relatch' and n in (10,13)) or (self.profile=='low_warning_early_only' and n==22) or (self.profile=='low_warning_late_only' and n==19)
            if clear:value=0x240
        if n in (11,14,20,23):
            clear=(self.profile=='delayed' and n!=11) or (self.profile=='relatch' and n in (11,14)) or (self.profile=='low_code_early_only' and n==20) or (self.profile=='low_code_late_only' and n==23) or (self.profile in ('low_warning_early_only','low_warning_late_only') and n in (20,23))
            if clear:value=0
        cs=0x60 if op=='write' else {1:0x4F,2:0x4B,4:0x43}[width]
        body=bytes(4) if op=='write' else value.to_bytes(width,'little')+bytes(4-width)
        data=bytes((cs,idx&255,idx>>8,sub))+body
        cid=0x581
        if n==self.at:
            if self.fault=='timeout':raise socket.timeout('mock silence')
            if self.fault=='abort':data=bytes((0x80,))+data[1:4]+bytes.fromhex('00000206')
            if self.fault=='wrong_command':data=bytes((0x20,))+data[1:]
            if self.fault=='wrong_object':data=data[:3]+bytes(((sub+1)%256,))+data[4:]
            if self.fault=='wrong_id':cid=0x582
            if self.fault=='late_receive':self.clock.value+=.251
            if self.fault=='logical':
                bad=1 if idx==0x2002 else (0x27 if idx==0x6041 else (0x9999 if idx==0x603F else (0x80 if idx==0x6040 else 1)))
                data=data[:4]+bad.to_bytes(width,'little')+bytes(4-width)
        raw=struct.pack('<IB3x8s',cid,8,data)
        if n==self.at and self.fault=='short_receive':return raw[:-1]
        if n==self.at and self.fault=='fd_receive':return raw+b'!'
        if self.profile=='slow_guard' and n==7:self.clock.value+=.06
        if op=='write':self.acks.append((word,self.clock.value))
        return raw


def case(profile='delayed',at=None,fault=None,classification=None):
    clock=Clock();sock=FakeSocket(clock,profile,at,fault)
    experiment=p.Experiment(sock,co,cb,clock.now,clock.sleep)
    error=None;result=None
    with contextlib.redirect_stdout(io.StringIO()):
        try:result=experiment.run()
        except Exception as exc:error=str(exc)
        finally:sock.close()
    assert sock.closes==1
    if fault:
        assert error is not None,(at,fault)
        assert len(sock.sent)==at,(at,fault,len(sock.sent))
        expected_high=9<=at<=17 or (at==18 and fault=='logical')
        assert experiment.bit7_may_remain_set==expected_high,(at,fault,experiment.bit7_may_remain_set)
    else:
        assert error is None,error
        assert result==classification,(result,classification)
        assert len(sock.sent)==24
        assert [word for word,t in sock.acks]==[0,0x80,0]
        assert sock.acks[1][1]-sock.acks[0][1]>=.1
        assert all(s['actual_start_offset_seconds']+1e-9>=s['target_offset_seconds'] for s in experiment.samples)
        assert not experiment.bit7_may_remain_set and experiment.final_zero_ack
        assert clock.value<10
        if profile=='slow_guard':assert abs(sock.acks[1][1]-sock.acks[0][1]-.1)<1e-9
    checks.append(dict(profile=profile,at=at,fault=fault,result='PASS',classification=result,error=error,sent=len(sock.sent),bit7_may_remain_set=experiment.bit7_may_remain_set))


with mock.patch('socket.socket',side_effect=AssertionError('native socket prohibited during local verification')):
    with contextlib.redirect_stdout(io.StringIO()):co,cb=p.load_pinned(PAYLOAD)
    for profile,classification in (
        ('delayed','cleared_at_final_observation'),
        ('never','warning_unchanged_not_cleared'),
        ('retained','warning_cleared_error_code_retained_not_tool_unlocked'),
        ('relatch','warning_relatched_after_high_clear'),
        ('slow_guard','warning_unchanged_not_cleared'),
        ('low_code_early_only','transient_or_incomplete_clear_not_confirmed'),
        ('low_code_late_only','transient_or_incomplete_clear_not_confirmed'),
        ('low_warning_early_only','transient_or_incomplete_clear_not_confirmed'),
        ('low_warning_late_only','transient_or_incomplete_clear_not_confirmed')):
        case(profile,classification=classification)
    for ordinal in range(1,25):
        for fault in ('timeout','abort','wrong_command','wrong_object','wrong_id','short_receive',
                      'fd_receive','late_receive','send_error','short_send'):
            case(at=ordinal,fault=fault)
    for ordinal in (1,2,3,4,5,7,8,10,11,12,13,14,15,16,18,19,20,21,22,23,24):
        case(at=ordinal,fault='logical')
    # Exercise the real main cleanup branch both before and after the high write.
    for ordinal in (1,10):
        clock=Clock();sock=FakeSocket(clock,at=ordinal,fault='timeout')
        factory=lambda s,c,b:p.Experiment_original(s,c,b,clock.now,clock.sleep)
        p.Experiment_original=p.Experiment
        with mock.patch.object(p,'load_pinned',return_value=(co,cb)),mock.patch.object(p,'os_preflight'), \
             mock.patch.object(p.socket,'socket',return_value=sock),mock.patch.object(p.signal,'signal'), \
             mock.patch.object(p.signal,'setitimer'),mock.patch.object(p,'Experiment',side_effect=factory), \
             mock.patch.object(sys,'argv',['reset_observe_once.py','--execute-reset-observe-once']), \
             contextlib.redirect_stdout(io.StringIO()):
            try:p.main()
            except socket.timeout:pass
            else:raise AssertionError('main hid timeout')
        assert sock.closes==1 and len(sock.sent)==ordinal
        cid,mask=struct.unpack('=II',sock.options[(101,1)])
        assert cid==0x581 and mask==0xC00007FF and not mask&0x20000000
        checks.append(dict(name='real_main_close_only',at=ordinal,result='PASS'))

argv=[sys.executable,'-I','-B',str(BASE/'reset_observe_once.py'),'--cold-load','--payload-root',str(PAYLOAD)]
r=subprocess.run(argv,capture_output=True,text=True,timeout=10)
(BASE/'cold-load.stdout').write_text(r.stdout);(BASE/'cold-load.stderr').write_text(r.stderr)
assert r.returncode==0,r.stdout+r.stderr
checks.append(dict(name='isolated_cold_process',result='PASS',argv=argv,rc=r.returncode))
summary=dict(result='PASS',checks=len(checks),cases=checks,physical_CAN_opens=0,physical_CAN_sends=0,
             source_sha256=hashlib.sha256((BASE/'reset_observe_once.py').read_bytes()).hexdigest())
(BASE/'mock-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='cases'}))
