"""固定7355 Python实际协议路径，本地Mock，绝不允许socket/process。"""
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import time
from unittest import mock

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
REPO = BASE.parents[4]
PAYLOAD = BASE.parents[1]/'报警内容显示-2026-10-19-证据/package/payload'
sys.path.insert(0, str(BASE))
import motion_compare_once as p
import pinned_payload as pin


def offline_only(event, args):
    if event in ('socket.__new__', 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
        raise AssertionError('本地验证禁止真实socket/process：'+event)


sys.addaudithook(offline_only)
with redirect_stdout(io.StringIO()):
    tool, Controller = pin.load_pinned(PAYLOAD)
host_library = (REPO/'build-host/libpmp4010_velocity.so').resolve(strict=True)
arm_library = PAYLOAD/'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'
assert struct.unpack('<H', host_library.read_bytes()[18:20])[0] == 62
assert struct.unpack('<H', arm_library.read_bytes()[18:20])[0] == 183
os.environ['PMP4010_VELOCITY_LIBRARY'] = str(host_library)
sys.modules['pmp4010_velocity'].require_velocity_library()

# Existing test-node behavior is reused read-only. Redirect their two explicit
# adjacent CLI loaders so that no source implementation escapes the fixed bag.
original_spec = importlib.util.spec_from_file_location
def pinned_spec(name, location, *args, **kwargs):
    if name in ('bench_acceptance_cli', 'position_cli_test'):
        assert Path(location).resolve() == REPO/'device/drivers/pmp4010/tools/pmp4010.py'
        return original_spec(name, PAYLOAD/'drivers/pmp4010/pmp4010.py', *args, **kwargs)
    return original_spec(name, location, *args, **kwargs)
sys.path.append(str(REPO/'device/drivers/pmp4010/tools'))
with mock.patch.object(importlib.util, 'spec_from_file_location', pinned_spec):
    fixture = importlib.import_module('_test_bench_support')
    manual_fixture = importlib.import_module('_test_manual')
# Fixture aliases must use the same fixed modules and standard layer.
assert Path(fixture.tool.__file__).resolve() == PAYLOAD/'drivers/pmp4010/pmp4010.py'
assert fixture.tool._canopen is tool._canopen
assert sys.modules['pmp4010_simple'].SimpleController is Controller


class Clock:
    def __init__(self): self.value = 100.0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


class Node(manual_fixture.ManualNode):
    """Actual standard SDO replies, model motion on feedback reads after edge.

    This is not motor physics. Tests prove command/guard/cleanup paths only.
    """
    def __init__(self):
        super().__init__()
        self.enable_drift = 0
        self.set_value(0x6041,0x440)
        for index in (0x6064,0x607A,0x6062):
            self.set_value(index,-1464)
        self.motion = False
        self.stuck = False
        self.bad_readback = None
        self.downloaded = set()
        self.trip_trace = None
        self.trace_seen = False
        self.trip_position = None
        self.trace_key_after_trip = []
        self.tripped = False
        self.drop_trace = None
        self.slow_trace = None
        self.short_trace = False
        self.change_before_second = False
        self.clock = None
        for index, value in ((0x607F, 10000000), (0x60C5, 10000000), (0x60C6, 10000000),
                             (0x6083, 5000000), (0x6084, 5000000)):
            self.set_value(index, value)
        for index, sub, width, signed, label in (*p.CORE, *p.PHASES, (0x1001,0,1,False,'寄存器')):
            value = -9 if signed else 123
            if index == 0x1001: value = 0
            self.od[index, sub] = fixture.mockbus.OdEntry(value.to_bytes(width,'little',signed=signed), writable=True)

    def handle(self, frame, now):
        data = frame.data
        key = (data[1] | data[2] << 8, data[3])
        reading = data[0] == 0x40
        if self.tripped and reading and key in {(i,s) for i,s,*_ in (*p.CORE,*p.PHASES)}:
            self.trace_key_after_trip.append(key)
        if reading and key == (0x60FC,0):
            self.trace_seen = True
            if self.short_trace:
                self.od[key].data = b'\0\0'
            if self.drop_trace:
                # Drop the reply at the requested fixed trace stage.
                wanted = self.motion if self.drop_trace == 'Go' else not self.motion
                if wanted:
                    self.tripped = True
                    return
            if self.trip_trace:
                wanted = self.motion if self.trip_trace == 'Go' else not self.motion
                if wanted:
                    self.set_value(0x6041, self.value(0x6041) | 0x08)
                    self.set_value(0x603F, 0x2312)
                    self.tripped = True
                    # The current object cannot diagnose state; the next guard
                    # finds the fault. No trace beyond that guard is allowed.
            if self.slow_trace and self.clock:
                self.clock.value += self.slow_trace
        if reading and key == self.bad_readback and key in self.downloaded:
            old = self.od[key].data
            self.set_value(key[0], p.ACCELERATION-1, key[1])
            super().handle(frame,now)
            self.od[key].data = old
            return
        # For the fixed one-shot behavior, model target arrival only after the
        # enabled bit4 edge. No new host command is synthesized by the model.
        if reading and key == (0x6064,0) and self.motion and not self.stuck:
            current = self.value(0x6064,signed=True)
            target = self.value(0x607A,signed=True)
            step = min(80000, abs(target-current))
            self.set_value(0x6064, current+(step if target>current else -step))
            self.set_value(0x606C, 0 if current == target else -25)
            if self.value(0x6064,signed=True) == target:
                self.set_value(0x606C,0)
                self.set_value(0x6041,self.value(0x6041) | 0x400)
        # Parent fixture auto-arrives on last 002F; preserve that path only
        # until enabled execution and model following feedback ourselves.
        before_position = self.value(0x6064,signed=True)
        super().handle(frame, now)
        if data[0] in (0x23,0x2B,0x2F):
            self.downloaded.add(key)
        if self.change_before_second and data[0] == 0x23 and key == (0x6083,0):
            self.set_value(0x6084,4000000)
        if data[:4] == bytes.fromhex('2b406000'):
            word = int.from_bytes(data[4:6],'little')
            if word == 0x3F: self.motion = True
            if word == 0x2F and self.motion:
                self.set_value(0x6064,before_position)
                self.set_value(0x606C,0 if self.stuck else -25)
                self.set_value(0x6041,self.value(0x6041) & ~0x400)
            if word in (0,2,6): self.motion = False
            if (0x6040,0) in self.drop_write_ack:
                self._outbox.clear()


checks = []
run_root = BASE/('mock-runs-'+str(time.time_ns()))
run_root.mkdir()


def case(name, setup=None, expect_error=False, verify=None, fake=False, hook=None, emit_cost=0, iface='can1', via_main=False):
    node = Node()
    bus = fixture.DelayedBus([node])
    out = pin.Output()
    clock = Clock()
    node.clock = clock
    if setup: setup(node)
    path = run_root/name
    path.mkdir()
    args = tool.build_parser().parse_args(['--manual','--manual-linear','--manual-mm-per-rev','4',
        '--node','1','--node-range','1','--timeout','0.25','--motion-log',str(path/'motion.jsonl')])
    ctl = Controller(tool,args)
    ctl.rt_ack = True
    output = io.StringIO()
    failure = None
    result = None
    main_rc = None
    with ExitStack() as stack:
        for patch in fixture.environment(bus): stack.enter_context(patch)
        cfg = {'bus': {'can': [{'logical':tool.LOGICAL_BUS,'iface':iface,'bitrate':1000000}]}}
        stack.enter_context(mock.patch.object(tool,'load_device_json',return_value=(cfg,'mock-config')))
        # The existing environment targets its own CLI alias, so redirect these
        # operations too; both aliases execute manifest-verified identical code.
        for key in ('read_iface_actual','service_active','parse_ip_stats','connect_bus','_std_open_active'):
            stack.enter_context(mock.patch.object(tool,key,getattr(fixture.tool,key)))
        if fake:
            stack.enter_context(mock.patch('time.monotonic',clock.now))
            stack.enter_context(mock.patch('time.sleep',clock.sleep))
        if emit_cost:
            original_emit = p.emit
            def slow_emit(event, **data):
                original_emit(event, **data)
                if event == '诊断实读': clock.value += emit_cost
            stack.enter_context(mock.patch.object(p,'emit',slow_emit))
        if hook: out.hook = lambda event,data: hook(event,data,node,ctl)
        if via_main:
            # Exercise the real main return path with actual Mock SDO. Only OS
            # identity and ARM library loading are adapted for this x86 process.
            velocity = sys.modules['pmp4010_velocity']
            velocity.require_velocity_library()
            stack.enter_context(mock.patch.object(p,'load_pinned',return_value=(tool,Controller)))
            stack.enter_context(mock.patch.object(p,'os_preflight'))
            stack.enter_context(mock.patch.object(p,'ROOT',path/'payload'))
            stack.enter_context(mock.patch.object(p,'make_controller',return_value=ctl))
            stack.enter_context(mock.patch.object(p,'Output',return_value=out))
            stack.enter_context(mock.patch.object(velocity,'_candidate_paths',return_value=[host_library]))
            stack.enter_context(mock.patch.dict(os.environ))
            os.environ.pop('PMP4010_VELOCITY_LIBRARY')
        with redirect_stdout(output):
            try:
                if via_main:
                    main_rc = p.main(['--execute-once','--od-session-ack',p.OD_ACK,
                                      '--motion-confirm',p.MOTION_ACK])
                    result = next(json.loads(line) for line in reversed(output.getvalue().splitlines())
                                  if json.loads(line)['event']=='一次动作反馈完成')
                else:
                    result = p.perform_once(ctl,out,clock.now if fake else time.monotonic,
                                            clock.sleep if fake else time.sleep)
            except BaseException as exc:
                failure = dict(type=type(exc).__name__,message=str(exc))
    (path/'stdout.jsonl').write_text(output.getvalue())
    raw = [dict(cob_id=frame.arbitration_id,data_hex=frame.data.hex()) for frame in bus.sent]
    (path/'tx.json').write_text(json.dumps(raw,indent=2)+'\n')
    writes = fixture.requests(bus,True)
    controls = [v for i,s,v in writes if i==0x6040]
    record = dict(name=name,error=failure,writes=writes,controls=controls,
                  requests=len(bus.sent),clock_elapsed_seconds=clock.value-100 if fake else None,
                  result=result,main_rc=main_rc,raw_directory=str(path.relative_to(BASE)))
    if expect_error:
        assert failure, (name, 'expected rejection', record)
    else:
        assert failure is None, (name, failure)
    if verify: verify(node,bus,ctl,record)
    checks.append(record)
    print(json.dumps(dict(name=name,result='PASS',requests=len(bus.sent),failure=failure,
                          controls=[f'{v:04X}' for v in controls]),ensure_ascii=False))


def zero_writes(node,bus,ctl,record): assert not record['writes'], record
def cleanup(node,bus,ctl,record):
    assert record['controls'][-2:] == [2,0], record
    assert record['controls'].count(2)==1 and record['controls'].count(0)==1, record
    assert not any(i==0x607A for i,s,v in record['writes']), record


def normal(node,bus,ctl,record):
    result = record['result']
    assert result['target_reached'] and result['operation_enabled_cleared']
    assert result['target'] == -1464 + p.REQUESTED_DELTA
    assert [v for i,s,v in record['writes'] if i==0x607A] == [result['target'] & 0xffffffff]*2
    assert set(v for i,s,v in record['writes'] if i==0x6081) == {218453}
    assert record['controls'] == [0x106,0x107,0x127,0x137,0x127,0x12F,0x2F,0x3F,0x2F,6]
    assert node.value(0x6083)==131072 and node.value(0x6084)==131072
    assert not result['mechanical_stop_confirmed']
    assert not any(i in (0x1010,0x2004) or (i==0x2002 and s==4) for i,s,v in record['writes'])


case('normal_frozen_minus10_100rpm',verify=normal)
for name,index,sub,value in (
    ('607C_nonzero',0x607C,0,1), ('2002_not_CiA402',0x2002,1,1),
    ('Warning',0x6041,0,0x4C0), ('Fault',0x6041,0,0x448),
    ('603F_nonzero',0x603F,0,0x2312), ('1001_nonzero',0x1001,0,1),
    ('scale_changed',0x608F,1,131073), ('max_acceleration',0x60C5,0,131071),
    ('max_deceleration',0x60C6,0,131071), ('original_a_changed',0x6083,0,131072),
    ('target_outside_device_limit',0x607D,1,-10000)):
    case('guard_'+name,setup=lambda n,i=index,s=sub,v=value:n.set_value(i,v,s),
         expect_error=True,verify=zero_writes)
for index in (0x6083,0x6084):
    case(f'readback_{index:04X}',setup=lambda n,i=index:setattr(n,'bad_readback',(i,0)),
         expect_error=True,verify=cleanup)


def communication(node,bus,ctl,record):
    assert record['controls'][-1] == (0x2F if node.drop_trace=='Go' else 0x12F), record
    assert 2 not in record['controls'] and 0 not in record['controls'], record
    assert ctl.manual_sessions[1].client.quarantined
    before = len(bus.sent)
    ctl.close()
    assert len(bus.sent) == before
    assert not node.trace_key_after_trip, record


for stage in ('Halt','Go'):
    case('communication_loss_'+stage,setup=lambda n,s=stage:setattr(n,'drop_trace',s),
         expect_error=True,verify=communication)


def fault_before_trace(event,data,node,ctl):
    if event=='manual_result' and data.get('action')=='enable':
        node.set_value(0x6041,0x2F)
        node.set_value(0x603F,0x2312)
        node.tripped = True


def alarm(node,bus,ctl,record):
    assert record['controls'][-2:]==[2,0],record
    assert 0x2F not in record['controls'] and 0x3F not in record['controls']
    assert not node.trace_key_after_trip,record


# Original hook runs after Trace.hook by design. Trigger before enable's final
# journal event, so the actual diagnostic health pre-read observes the fault.
def alarm_setup(n):
    original = n.handle
    def handle(frame,now):
        original(frame,now)
        if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6],'little')==0x12F:
            n.set_value(0x6041,n.value(0x6041)|8)
            n.set_value(0x603F,0x2312)
            n.tripped=True
    n.handle=handle
case('alarm_enable_before_trace',setup=alarm_setup,expect_error=True,verify=alarm)


def timeout_cleanup(node,bus,ctl,record):
    assert record['controls'][-2:]==[2,0],record
    assert record['controls'].count(2)==1 and record['controls'].count(0)==1
    assert record['clock_elapsed_seconds'] <= 10.5+4.0,record
    assert node.value(0x6083)==131072 and node.value(0x6084)==131072
case('observation_total_deadline_stuck',setup=lambda n:setattr(n,'stuck',True),
     expect_error=True,verify=timeout_cleanup,fake=True)
case('Halt_inflight_late_reply_quarantine',setup=lambda n:setattr(n,'slow_trace',.6),
     expect_error=True,verify=communication,fake=True)
case('Halt_logical_half_second_budget_cleanup',expect_error=True,
     verify=timeout_cleanup,fake=True,emit_cost=.11)

def observer_failure(event,data,node,ctl):
    if event == 'manual_result' and data.get('action') == 'enable':
        raise RuntimeError('Mock observer异常，禁止Go')
case('observer_exception_original_cleanup_once',hook=observer_failure,expect_error=True,
     verify=lambda n,b,c,r: (
         timeout_cleanup(n,b,c,r),
         (_ for _ in ()).throw(AssertionError(r)) if 0x2F in r['controls'] else None),fake=True)
case('trace_width_quarantine_no_later_SDO',setup=lambda n:setattr(n,'short_trace',True),
     expect_error=True,verify=communication)

def second_parameter_changed(node,bus,ctl,record):
    assert record['controls'][-2:]==[2,0],record
    assert [i for i,s,v in record['writes'] if i in (0x6083,0x6084)]==[0x6083],record
    assert node.value(0x6084)==4000000
case('second_parameter_external_change_not_overwritten',
     setup=lambda n:setattr(n,'change_before_second',True),expect_error=True,verify=second_parameter_changed)
case('can0_rejected_before_any_request',iface='can0',expect_error=True,
     verify=lambda n,b,c,r: (zero_writes(n,b,c,r),
         (_ for _ in ()).throw(AssertionError(r)) if r['requests'] else None))

def drop_normal_shutdown(event,data,node,ctl):
    if event == 'manual_result' and data.get('action') == 'go':
        node.drop_write_ack.add((0x6040,0))
def shutdown_unconfirmed(node,bus,ctl,record):
    assert record['result']['target_reached'] is True
    assert record['result']['operation_enabled_cleared'] is False
    assert record['controls'][-1]==6 and record['controls'].count(6)==1
    assert ctl.manual_sessions[1].client.quarantined
    count=len(bus.sent)
    ctl.close()
    assert count==len(bus.sent)
case('normal_target_reached_shutdown_unconfirmed_is_failure',hook=drop_normal_shutdown,
     verify=shutdown_unconfirmed)
def main_shutdown_failure(node,bus,ctl,record):
    shutdown_unconfirmed(node,bus,ctl,record)
    assert record['main_rc']==1,record
case('main_return1_when_shutdown_unconfirmed',hook=drop_normal_shutdown,
     verify=main_shutdown_failure,via_main=True)

production = {}
for name in pin.MODULES:
    path = Path(sys.modules[name].__file__).resolve()
    assert path.is_relative_to(PAYLOAD)
    production[name] = dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
summary = dict(result='PASS',checks=len(checks),cases=checks,
               manifest_sha256=pin.MANIFEST_SHA,production_modules=production,
               host_library=dict(path=str(host_library),sha256=hashlib.sha256(host_library.read_bytes()).hexdigest(),
                                 reason='仅x86 Mock代替ARM库；实际换算C ABI1'),
               ARM_library_sha256=hashlib.sha256(arm_library.read_bytes()).hexdigest(),
               live_CAN_opens=0,live_CAN_sends=0,hardware_execution=False,
               actual_model='unknown',actual_reasoning_effort='unknown',last_token_usage_input_tokens='unknown')
(BASE/'mock-results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(result='PASS',checks=len(checks),live_CAN_opens=0,live_CAN_sends=0)))
