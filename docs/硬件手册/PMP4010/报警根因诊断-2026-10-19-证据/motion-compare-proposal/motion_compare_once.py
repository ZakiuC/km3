"""仅准备相对-10mm/100rpm一次，6083/6084低加速度对照；根审后才可执行。

默认无CAN。--execute-once还必须带两个明确会话确认字串；它们不是现场授权。
原SimpleController冻结review/execute、ManualController守卫和清理完整保留。
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pinned_payload import ROOT, MANIFEST_SHA, Output, emit, load_pinned, make_controller, os_preflight

ACCELERATION = 131072
ORIGINAL_ACCELERATION = 5000000
REQUESTED_DELTA = -327680
REQUESTED_VELOCITY = 218453
MAX_BATCHES = 20
PERIOD = .5
OBSERVATION_SECONDS = 10.0
HALT_SECONDS = .5
OD_ACK = '接受本会话OD缺口和独立停机责任'
MOTION_ACK = '确认仅一次相对-10mm_100rpm_低加速度对照'
RUN_NAME = 'logs/relative-minus10-100rpm-a131072-once-20261019'
# E06 10.3: 60FC encoder units; 606B/6069 user units/s; 60F4 user units.
# 6077/6078 are I16 raw (0.1%); do not infer calibrated shaft torque/current.
CORE = ((0x60FC, 0, 4, True, '实时轨迹编码器单位'),
        (0x606B, 0, 4, True, '内部速度指令用户单位每秒'),
        (0x6069, 0, 4, True, '实际速度用户单位每秒'),
        (0x60F4, 0, 4, True, '跟随偏差用户单位'),
        (0x6077, 0, 2, True, '转矩反馈raw'),
        (0x6078, 0, 2, True, '电流反馈raw'))
PHASES = tuple((0x200B, sub, 2, False, label) for sub, label in
               ((0x12, 'A相电流raw_0.01A'), (0x13, 'B相电流raw_0.01A'),
                (0x14, 'C相电流raw_0.01A'), (0x15, '母线电压raw_0.1V')))


def healthy_status(value, disabled=False):
    if value & (0x80 | 0x08 | 0x2000):
        raise ValueError('6041 Warning/Fault/PP跟随误差：0x{:04X}'.format(value))
    if disabled and value & 0x6F != 0x40:
        raise ValueError('前置要求Switch on disabled')


class Trace:
    """同一个原ManualClient；只收紧call期限，绝不清除quarantine。"""
    def __init__(self, ctl, out, clock=time.monotonic, pause=time.sleep):
        self.ctl, self.out, self.clock, self.pause = ctl, out, clock, pause
        self.session = ctl.manual_sessions[ctl.node]
        self.motion_deadline = None
        self.go_started = None
        self.batch_count = 0
        self.halt_done = False
        self.cleanup_started = False
        self.original_call = self.session.call
        self.original_cleanup = ctl._cleanup_failure
        self.original_hook = out.hook

    def install(self):
        def bounded_call(deadline, label, method, *args):
            if self.motion_deadline is not None:
                deadline = min(deadline, self.motion_deadline)
            return self.original_call(deadline, label, method, *args)
        def cleanup(node, session, exc, out):
            # The original stop2 -> disable0 owns independent, bounded budgets.
            # Never let the observation deadline suppress allowed cleanup.
            self.cleanup_started = True
            self.motion_deadline = None
            return self.original_cleanup(node, session, exc, out)
        self.session.call = bounded_call
        self.ctl._cleanup_failure = cleanup
        self.out.hook = self.hook

    def detach(self):
        self.motion_deadline = None
        self.session.call = self.original_call
        self.ctl._cleanup_failure = self.original_cleanup
        self.out.hook = self.original_hook

    def read(self, item, deadline, phase):
        index, sub, size, signed, label = item
        client = self.session.client
        if self.cleanup_started or getattr(client, 'quarantined', False):
            raise ValueError('已停止/隔离；禁止补读诊断')
        if self.clock() >= deadline:
            raise TimeoutError('诊断批次期限已到；不追加请求')
        start = self.clock()
        emit('诊断读取前', phase=phase, index=f'{index:04X}', subindex=sub,
             deadline_monotonic=deadline)
        raw = self.session.call(deadline, '诊断 '+label, client.read, index, sub)
        if len(raw) != size:
            client.quarantined = True
            raise ValueError('诊断OD宽度不符；同client隔离')
        value = int.from_bytes(raw, 'little', signed=signed)
        emit('诊断实读', phase=phase, index=f'{index:04X}', subindex=sub, label=label,
             size_bytes=size, signed=signed, raw_value_hex=raw.hex(), value=value,
             request_start_monotonic=start, response_monotonic=self.clock(),
             simultaneous_sample=False)
        if index == 0x6041:
            healthy_status(value)
            if value & 0x6F != 0x27:
                raise ValueError('诊断阶段退出Operation enabled')
        elif index in (0x603F, 0x1001) and value:
            raise ValueError('诊断出现非零{:04X}；立即停止，不再补读'.format(index))
        return value

    def batch(self, phase, deadline, phases=False):
        if phase != 'enable-Halt':
            if self.batch_count >= MAX_BATCHES:
                raise TimeoutError('运行诊断达到20批上限')
            self.batch_count += 1
        start = self.clock()
        # Fresh health at both ends; reads between them are serial, not atomic.
        health = ((0x6041, 0, 2, False, '状态字'), (0x603F, 0, 2, False, '错误码'),
                  (0x1001, 0, 1, False, '错误寄存器'))
        values = []
        for item in (*health, *CORE, *(PHASES if phases else ()), *health):
            values.append(self.read(item, deadline, phase))
        emit('诊断批次完成', phase=phase, ordinal=self.batch_count, reads=len(values),
             elapsed_seconds=self.clock()-start, simultaneous_sample=False)

    def hook(self, event, data):
        # Journal.event propagates observer exceptions into Manual.execute's
        # original except handler. No extra SDO is inserted in Go handshakes.
        if event == 'manual_result' and data.get('action') == 'enable' and data.get('success'):
            if self.halt_done:
                raise ValueError('重复Halt使能钩子；禁止继续')
            self.halt_done = True
            end = self.clock()+HALT_SECONDS
            self.batch('enable-Halt', end, phases=True)
            if self.clock() < end:
                self.pause(end-self.clock())
            emit('Halt观察结束', maximum_seconds=HALT_SECONDS,
                 brake_mechanical_state_confirmed=False)
        elif (event == 'manual_controlword' and data.get('controlword') == 0x2F
              and data.get('point') == 'before_write' and self.go_started is None):
            if not self.halt_done:
                raise ValueError('未完成Halt诊断；禁止Go')
            self.go_started = self.clock()
            self.motion_deadline = self.go_started+OBSERVATION_SECONDS
            emit('Go观察计时起点', deadline_monotonic=self.motion_deadline,
                 definition='首次002F写前；含原Go握手，最多10秒；清理另有原bounded期限')
        if self.original_hook is not None:
            self.original_hook(event, data)


def write_low_acceleration(ctl, out):
    """Known disabled; precheck BOTH limits before the first attempted write.

    RAM only. Failure may leave one changed object; never restore the old high
    acceleration automatically. Responsibility is registered before download.
    """
    ctl.refresh(out)
    row = ctl._fresh(ctl.node)
    values = row['values']
    healthy_status(values['statusword'], disabled=True)
    if values['control_mode_select'] != 0 or values['home_offset'] != 0:
        raise ValueError('2002:01或607C前置不符')
    if any(values[key] < ACCELERATION for key in ('max_profile_acceleration', 'max_deceleration')):
        raise ValueError('131072超过60C5/60C6，零写拒绝')
    if any(values[key] != ORIGINAL_ACCELERATION for key in ('profile_acceleration', 'profile_deceleration')):
        raise ValueError('6083/6084不是已审阅的5000000；禁止推测或重复对照')
    session = ctl.manual_sessions[ctl.node]
    # Read-only prospective draft ensures target/calibration/device limits and
    # original motion guards all pass BEFORE writing either comparison value.
    prospective = dict(values)
    prospective.update(profile_acceleration=ACCELERATION, profile_deceleration=ACCELERATION)
    a = ctl._args(dict(row, values=prospective), values['actual_position']+REQUESTED_DELTA)
    a.velocity = REQUESTED_VELOCITY
    ctl._validate(prospective, row['calibration'], a, values['actual_position'], stationary=True)
    for key in ('profile_acceleration', 'profile_deceleration'):
        current, cal = ctl._snapshot(session)
        healthy_status(current['statusword'], disabled=True)
        if cal != row['calibration'] or current['home_offset'] != 0 or current['control_mode_select'] != 0:
            raise ValueError('参数下载前标定/控制模式/607C变化')
        if current['error_code_object'] or current['actual_velocity'] != 0:
            raise ValueError('参数下载前故障/非零速度')
        if current[key] != ORIGINAL_ACCELERATION:
            raise ValueError('本项6083/6084写前不再是已批准5000000；不覆盖外部变动')
        if current['max_profile_acceleration'] < ACCELERATION or current['max_deceleration'] < ACCELERATION:
            raise ValueError('参数下载前60C5/60C6上限变化')
        spec = ctl.objects[key]
        session.needs_stop = True
        ctl.enabled_sessions[ctl.node] = session
        emit('对照参数写前', index=f'{spec["index_value"]:04X}', before=current[key],
             value=ACCELERATION, RAM_only=True, automatic_restore=False)
        session.call(session.deadline(), '失能下低加速度对照写入', session.client.write,
                     spec['index_value'], spec['sub'], ACCELERATION.to_bytes(4, 'little'))
        actual = ctl._read(session.client, key, session)
        emit('对照参数读回', index=f'{spec["index_value"]:04X}', actual=actual, expected=ACCELERATION)
        if actual != ACCELERATION:
            raise ValueError('6083/6084写读回不符；不盲目恢复5000000')
    ctl.refresh(out)
    final = ctl._fresh(ctl.node)['values']
    if any(final[key] != ACCELERATION for key in ('profile_acceleration', 'profile_deceleration')):
        raise ValueError('参数最终快照不一致')


def perform_once(ctl, out, clock=time.monotonic, pause=time.sleep):
    trace = None
    original_open = ctl.tool._std_open_active
    opens = 0
    def can1_once(iface):
        nonlocal opens
        if iface != 'can1' or opens:
            raise ValueError('本次只允许一次can1打开；禁止can0/重连')
        opens += 1  # Register before attempt; failure never grants retry.
        return original_open(iface)
    ctl.tool._std_open_active = can1_once
    result = dict(target_reached=False, mechanical_stop_confirmed=False,
                  acceleration_restore_attempted=False, save_operations=0)
    try:
        ctl.scan(out)
        if ctl.context != ('can1', 1000000) or ctl.node != 1 or ctl.args.node != 1 or ctl.args.node_range != '1':
            raise ValueError('实际配置不是can1/1Mbps/节点1')
        ctl.refresh(out)
        row = ctl._fresh(ctl.node)
        v, cal = row['values'], row['calibration']
        healthy_status(v['statusword'], disabled=True)
        if (v['error_code_object'] != 0 or v['control_mode_select'] != 0 or v['home_offset'] != 0
                or cal['values'] != [131072, 1, 1, 1]):
            raise ValueError('报警/2002:01/607C/608F/6091前置不符')
        session = ctl.manual_sessions[ctl.node]
        raw_register = session.call(session.deadline(), '前置读取1001', session.client.read, 0x1001, 0)
        if raw_register != b'\0':
            if len(raw_register) != 1:
                session.client.quarantined = True
            raise ValueError('1001非零或OD宽度不符')
        ctl.authorize()  # Existing explicit per-session OD acknowledgement API.
        ctl.set_value('speed', '100')
        if ctl.next_velocity != REQUESTED_VELOCITY:
            raise ValueError('100rpm量化不再为218453U/s')
        trace = Trace(ctl, out, clock, pause)
        trace.install()
        write_low_acceleration(ctl, out)
        reviewed = ctl.review_action('relative', out, '-10')
        if reviewed['target']-reviewed['start'] != REQUESTED_DELTA or reviewed['velocity'] != REQUESTED_VELOCITY:
            raise ValueError('冻结目标/速度与本次批准参数不符')
        emit('单次冻结审阅', start=reviewed['start'], target=reviewed['target'], delta=REQUESTED_DELTA,
             velocity=reviewed['velocity'], requested_mm=-10, requested_rpm=100,
             mm_per_motor_revolution=4, calibration=reviewed['calibration'],
             settings=reviewed['settings'], lines=reviewed['lines'], two_phase_API='review_action -> execute_action',
             single_factor_experiment=False, high_acceleration_cause_proven=False)
        ctl.execute_action(reviewed, out)  # Exactly one original aggregate action.
        if trace.motion_deadline is None:
            raise ValueError('缺少原Go计时事件')
        next_sample = clock()
        while trace.batch_count < MAX_BATCHES:
            if next_sample >= trace.motion_deadline or clock() >= trace.motion_deadline:
                break
            if clock() < next_sample:
                pause(next_sample-clock())
            # Original protection snapshot unchanged; no diagnostic reads after
            # a failed refresh/_fresh or client quarantine.
            ctl.refresh(out)
            current = ctl._fresh(ctl.node)['values']
            healthy_status(current['statusword'])
            deadline = min(trace.motion_deadline, clock()+.5)
            trace.batch('Go后运行', deadline, phases=trace.batch_count == 0)
            emit('原反馈判据采样', position=current['actual_position'], velocity=current['actual_velocity'],
                 statusword=current['statusword'], target=reviewed['target'])
            if (current['actual_position'] == reviewed['target'] and current['actual_velocity'] == 0
                    and current['statusword'] & ctl.tool._canopen.CIA402_HOMING_TARGET_REACHED):
                result.update(target_reached=True, target=reviewed['target'], position=current['actual_position'],
                              runtime_batches=trace.batch_count, observation_elapsed_seconds=clock()-trace.go_started)
                return result
            next_sample = max(next_sample+PERIOD, clock())
        raise TimeoutError('首次002F写前起10秒/20批内未满足原exact目标+零速+bit10')
    except BaseException as exc:
        session = ctl.manual_sessions.get(ctl.node)
        if (session is not None and ctl.context is not None
                and (ctl.context[0], ctl.node) not in ctl.blocked
                and (ctl.node in ctl.proofs or ctl.node in ctl.enabled_sessions
                     or session.needs_stop or getattr(session.client, 'quarantined', False))):
            ctl._cleanup_failure(ctl.node, session, exc, out)
        raise
    finally:
        if trace is not None:
            trace.detach()
        try:
            results = ctl.close(out)  # Original normal close: 0006, operation enabled=0.
        finally:
            ctl.tool._std_open_active = original_open
        result.update(shutdown_results=results, operation_enabled_cleared=any(
            item['node'] == ctl.node and item['disabled_confirmed'] is True for item in results))
        emit('单次退出', **result, automatic_retries=0, second_target=False,
             RAM_acceleration_after_failure_may_be_partial=True,
             normal_exit='原close=0006，仅确认operation enabled清零',
             exceptional_exit='原cleanup=0002→0000；quarantine则零追加请求')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute-once', action='store_true')
    mode.add_argument('--cold-load', action='store_true')
    parser.add_argument('--payload-root', type=Path, default=ROOT)
    parser.add_argument('--od-session-ack')
    parser.add_argument('--motion-confirm')
    args = parser.parse_args(argv)
    if not args.execute_once and not args.cold_load:
        emit('仅本地计划', CAN_opens=0, CAN_sends=0, manifest_sha256=MANIFEST_SHA,
             relative_mm=-10, requested_rpm=100, target_delta=REQUESTED_DELTA,
             velocity_raw=REQUESTED_VELOCITY, profile_acceleration=ACCELERATION,
             max_running_batches=MAX_BATCHES, trace_period_seconds=PERIOD,
             enable_Halt_max_seconds=HALT_SECONDS, observation_max_seconds=OBSERVATION_SECONDS,
             exceptional_cleanup_max_seconds=4.0, maximum_added_trace_reads=16+16+19*12,
             reset_operations=0, save_operations=0, single_factor_experiment=False,
             root_review_required=True, hardware_execution_authorized_here=False)
        return 0
    tool, controller_type = load_pinned(args.payload_root)
    if args.cold_load:
        return 0
    if args.payload_root != ROOT:
        raise ValueError('现场禁止覆盖固定payload路径')
    if args.od_session_ack != OD_ACK or args.motion_confirm != MOTION_ACK:
        raise ValueError('缺少明确OD会话确认/本次冻结动作确认')
    import os
    if 'PMP4010_VELOCITY_LIBRARY' in os.environ:
        raise ValueError('现场禁止外部速度库覆盖')
    # Native dependency must really load before bus open on ARM; cold-load on
    # x86 intentionally verifies provenance without trying to execute ARM ELF.
    sys.modules['pmp4010_velocity'].require_velocity_library()
    os_preflight()
    run_dir = ROOT/RUN_NAME
    run_dir.mkdir(parents=True, exist_ok=False)  # Never delete/retry this sentinel.
    ctl = make_controller(tool, controller_type, run_dir/'motion.jsonl')
    result = perform_once(ctl, Output())
    emit('一次动作反馈完成', **result, causal_conclusion='仅此方向与此配置观察；未证明高加速度根因')
    return 0 if result['target_reached'] and result['operation_enabled_cleared'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        emit('结束且不重试', exception=type(exc).__name__, message=str(exc),
             mechanical_stop_confirmed=False, automatic_retries=0)
        raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1)
