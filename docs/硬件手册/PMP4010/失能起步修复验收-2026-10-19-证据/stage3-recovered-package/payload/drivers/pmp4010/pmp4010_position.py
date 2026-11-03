"""Single PP acceptance transaction. No hardware is opened by this module.

E06 §5.1/5.2, §6.3.2/6.3.4/6.3.6 and table 10-7 are the source.
The offline preview executes this same transaction against the shared MockBus;
it is a nominal path, while polling and failure cleanup are conditional at runtime.
"""
import datetime
from fractions import Fraction
import json
import math
import time

from pmp4010_params import TYPES, encode, integer
from pmp4010_velocity import (require_velocity_library, unit_to_velocity,
                              velocity_to_int32, velocity_to_unit)

CALIBRATION = ('encoder_increments', 'encoder_motor_revolutions',
               'gear_motor_revolutions', 'gear_output_revolutions')
LIMITS = ('max_velocity', 'max_motor_speed', 'max_profile_acceleration', 'max_deceleration')
REQUIRED = CALIBRATION + LIMITS + ('actual_position', 'actual_velocity', 'statusword', 'controlword',
    'control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
    'target_position', 'profile_velocity', 'profile_end_velocity',
    'profile_acceleration', 'profile_deceleration', 'halt_option', 'position_demand')


def specifications(od):
    result = {}
    for entry in od['entries']:
        objects = list(entry.get('motion_objects', []))
        if entry['key'] in REQUIRED and entry.get('type') in TYPES:
            objects.append(dict(entry, ranges=None))
        for obj in objects:
            if entry['status'] != 'verified' or not entry['cite']:
                raise ValueError('运动对象缺少 verified 手册来源')
            key = obj['key']
            if key in result:
                raise ValueError('重复运动对象: ' + key)
            size, signed = TYPES[obj['type']]
            low, high = (-(1 << (size*8-1)), (1 << (size*8-1))-1) if signed else (0, (1 << (size*8))-1)
            index, sub = int(obj['index'], 0), integer(obj['sub'])
            ranges = obj.get('ranges') or [[low, high]]
            if not 1 <= index <= 65535 or not 0 <= sub <= 255:
                raise ValueError('运动对象地址非法')
            for a, b in ranges:
                if not low <= integer(a) <= integer(b) <= high:
                    raise ValueError('运动对象范围超出宽度')
            result[key] = dict(index_value=index, sub=sub, size=size,
                               signed=signed, ranges=ranges)
    if any(key not in result for key in REQUIRED):
        raise ValueError('缺少结构化运动/标定对象')
    trigger = next(e for e in od['entries'] if e['key'] == 'pp_trigger_patterns')
    if trigger['status'] != 'verified' or not trigger['cite']:
        raise ValueError('PP握手模式缺少手册来源')
    result['pp_protocol'] = trigger['pp_absolute_immediate']
    for key in ('mode', 'idle_word', 'trigger_word'):
        if not 0 <= integer(result['pp_protocol'][key]) <= 65535:
            raise ValueError('PP握手声明非法')
    return result


def calibration(values):
    if len(values) != 4 or any(not 1 <= integer(v) <= 0xffffffff for v in values):
        raise ValueError('608F/6091四项标定值均须为正Uint32，不猜测设备分辨率')
    r, g = Fraction(values[0], values[1]), Fraction(values[2], values[3])
    return dict(values=list(values), resolution=str(r), gear_ratio=str(g),
                motor_degrees_per_user_unit=str(g / r * 360),
                output_degrees_per_user_unit=str(Fraction(360) / r))


def validate(args, specs):
    for key, value in [('target_position', args.target), ('actual_position', args.soft_min),
                       ('actual_position', args.soft_max)]:
        if value is None:
            raise ValueError('必须人工指定目标和脚本软行程 --target/--soft-min/--soft-max')
        encode(specs[key], value)
    if not args.soft_min < args.soft_max or not args.soft_min <= args.target <= args.soft_max:
        raise ValueError('目标超出脚本软行程，或行程无效')
    if args.max_displacement is None or not 1 <= integer(args.max_displacement) <= 0x7fffffff:
        raise ValueError('必须人工给定正整数 --max-displacement（用户单位）')
    for key, value in [('profile_velocity', args.velocity), ('profile_acceleration', args.acceleration),
                       ('profile_deceleration', args.deceleration)]:
        if value is None or value <= 0:
            raise ValueError('速度/加速度/减速度须为正整数')
        encode(specs[key], value)
    if not math.isfinite(args.move_timeout) or args.move_timeout <= 0:
        raise ValueError('--move-timeout须为有限正数')
    if not args.motion_log:
        raise ValueError('必须指定 --motion-log 保存运动前后及异常JSONL记录')


def check_envelope(args, start, current):
    if not args.soft_min <= current <= args.soft_max:
        raise ValueError('实际位置越过脚本软行程: {}'.format(current))
    if abs(current - start) > args.max_displacement:
        raise ValueError('实际位移越过人工单次位移上限')


def check_start(args, start, cal):
    check_envelope(args, start, start)
    delta = abs(args.target - start)
    if not 0 < delta <= args.max_displacement:
        raise ValueError('目标位移必须非零且不超过人工单次位移上限')
    check_budget(args, cal)
    g = Fraction(cal['gear_ratio'])
    if any(not -0x80000000 <= value*g <= 0x7fffffff for value in (args.target, args.soft_min, args.soft_max)):
        raise ValueError('脚本行程/目标经G换算后超出6063编码器Int32范围')


def check_budget(args, cal):
    """Shared hard ceiling, including non-travelling bench actions."""
    if args.max_displacement * Fraction(cal['motor_degrees_per_user_unit']) > 1:
        raise ValueError('人工单次位移上限超过1度电机轴角度；请用实读标定缩小')


class Journal:
    def __init__(self, path, source, metadata, observer=None):
        self.stream = open(path, 'x', encoding='utf-8')
        self.source = source
        self.observer = observer
        try:
            self.event('begin', **metadata)
        except BaseException:
            self.stream.close()
            raise

    def event(self, event, **data):
        self.stream.write(json.dumps(dict(timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            source=self.source, event=event, **data), ensure_ascii=False) + '\n')
        self.stream.flush()
        if self.observer is not None:
            self.observer(event, data)

    def close(self):
        self.stream.close()


def run(session, args, specs, journal):
    """Absolute overall deadline; cleanup gets independent bounded deadlines.

    Responsibility starts before the first potentially state-changing request.
    A lost download ACK can mean the drive already acted. Never clear SDO
    quarantine or instantiate a replacement client to attempt cleanup.
    """
    require_velocity_library()
    import canopen as protocol
    pp = specs['pp_protocol']
    overall = time.monotonic() + args.move_timeout
    original_deadline = session.deadline
    session.deadline = lambda: min(overall, original_deadline())
    session.needs_stop = True
    confirmed = False
    error = None
    before = None

    def read(key, deadline=None):
        spec = specs[key]
        raw = session.call(session.deadline() if deadline is None else deadline,
                           '读取 ' + key, session.client.read, spec['index_value'], spec['sub'])
        if len(raw) != spec['size']:
            raise ValueError(key + ' 读回宽度错误')
        return int.from_bytes(raw, 'little', signed=spec['signed'])

    def snapshot(label, guarded=True, deadline=None):
        sw = read('statusword', deadline)
        pos = read('actual_position', deadline)
        journal.event(label, statusword=sw, statusword_hex='0x{:04X}'.format(sw), actual_position=pos)
        if guarded and before is not None:
            check_envelope(args, before, pos)
        return sw, pos

    def checked_write(key, value):
        spec = specs[key]
        session.call(session.deadline(), '设置 ' + key, session.client.write,
                     spec['index_value'], spec['sub'], encode(spec, value))
        actual = read(key)
        journal.event('write_readback', object=key, expected=value, actual=actual)
        if actual != value:
            raise ValueError(key + ' 写读回不一致')

    def poll(label, predicate):
        deadline = session.deadline()
        while time.monotonic() < deadline:
            sw, pos = snapshot(label, deadline=deadline)
            if (sw & protocol.CIA402_FAULT or sw & protocol.CIA402_PP_FOLLOWING_ERROR
                    or sw & protocol.CIA402_MASK != protocol.CIA402_ENABLED):
                raise ValueError('位置运动状态异常: 0x{:04X}'.format(sw))
            if read('modes_of_operation_display', deadline) != pp['mode']:
                raise ValueError('位置运动期间运行模式退出PP')
            if predicate(sw, pos):
                return sw, pos
            session.pause(deadline)
        raise ValueError(label + ' 超时')

    try:
        _, before = snapshot('before', guarded=False)
        expected_start = getattr(args, '_bench_expected_start', before)
        if before != expected_start:
            raise ValueError('审阅后起点已变化；拒绝执行，请重新审阅')
        session.require_cia402(session.deadline())
        # Refuse faults: acceptance never resets alarms automatically.
        if read('statusword') & protocol.CIA402_FAULT:
            raise ValueError('存在报警，先由人类查明原因；工具不自动复位')
        cal = calibration([read(key) for key in CALIBRATION])
        journal.event('calibration', **cal)
        check_start(args, before, cal)
        limits = {key: read(key) for key in LIMITS}
        journal.event('limits', **limits)
        if (args.velocity > limits['max_velocity'] or args.acceleration > limits['max_profile_acceleration']
                or args.deceleration > limits['max_deceleration']
                or unit_to_velocity(args.velocity, cal) > limits['max_motor_speed']):
            raise ValueError('速度/加减速度超过设备607F/6080/60C5/60C6上限')
        bench_guard = getattr(args, '_bench_guard', None)
        if bench_guard is not None:
            bench_guard(session, 'before_setup')
        session.disable()  # E06 §6.1: mode change only while disabled.
        checked_write('modes_of_operation', pp['mode'])
        if read('modes_of_operation_display') != pp['mode']:
            raise ValueError('PP模式回显不符')
        for key, value in [('profile_end_velocity', 0), ('profile_velocity', args.velocity),
                           ('profile_acceleration', args.acceleration), ('profile_deceleration', args.deceleration),
                           ('target_position', args.target)]:
            if key == 'target_position' and bench_guard is not None:
                bench_guard(session, 'before_target')
            checked_write(key, value)
        # No calls to enable() that might reset a fault arising during setup.
        for word, state in ((protocol.CIA402_SHUTDOWN, protocol.CIA402_READY),
                            (protocol.CIA402_SWITCH_ON, protocol.CIA402_SWITCHED_ON),
                            (protocol.CIA402_ENABLE, protocol.CIA402_ENABLED)):
            session.control(word, session.deadline())
            session.wait_status(lambda sw: sw & protocol.CIA402_MASK == state, 'PP使能迁移', session.deadline())
        session.control(pp['idle_word'], session.deadline())  # E06 §6.3.6 absolute/immediate, bit4=0.
        poll('ready_for_setpoint', lambda sw, pos: not sw & protocol.CIA402_PP_SETPOINT_ACK)
        if bench_guard is not None:
            bench_guard(session, 'before_trigger')
        session.control(pp['trigger_word'], session.deadline())
        # The fresh ACK 0 -> 1 -> 0 handshake identifies this command.
        # A short move may finish before one poll; bit10 need not be observed 0.
        poll('setpoint_ack', lambda sw, pos: bool(sw & protocol.CIA402_PP_SETPOINT_ACK))
        session.control(pp['idle_word'], session.deadline())
        poll('target_completion', lambda sw, pos:
             not sw & protocol.CIA402_PP_SETPOINT_ACK
             and bool(sw & protocol.CIA402_HOMING_TARGET_REACHED) and pos == args.target)
        snapshot('target_reached')
    except BaseException as exc:
        error = exc
        try:
            journal.event('exception', type=type(exc).__name__, message=str(exc),
                          quarantined=bool(getattr(session.client, 'quarantined', False)))
        except (OSError, ValueError):
            pass
    finally:
        # Cancellation interrupts the transaction, never its bounded cleanup.
        session.cancel_event = None
        session.deadline = original_deadline
        # Always try both operations, even if quick-stop confirmation fails.
        confirmed = session.stop_after_failure()
        try:
            sw, pos = snapshot('after_cleanup', guarded=False)
            if sw & protocol.CIA402_DISABLED_MASK != protocol.CIA402_DISABLED:
                confirmed = False
                raise ValueError('清理后状态字已不是disabled；机械状态未知')
            if before is not None:
                check_envelope(args, before, pos)
            if error is None and pos != args.target:
                raise ValueError('清理后实际位置偏离目标')
        except (Exception, KeyboardInterrupt) as exc:
            error = error or exc
            try:
                journal.event('after_cleanup_unavailable', message=str(exc))
            except (OSError, ValueError):
                pass
        try:
            journal.event('result', completed=error is None and confirmed,
                          disabled_confirmed=confirmed, mechanical_stop_confirmed=False,
                          independent_estop_required=not confirmed)
        except (OSError, ValueError) as exc:
            error = error or exc
    if error is not None:
        raise error
    if not confirmed:
        raise ValueError('未确认失能；机械停止未知，立即使用独立急停')


def _require_unenabled_probe_word(word):
    if word & 8:
        raise ValueError('未使能起步核证禁止Operation enable（bit3必须为0）')
    if not word & 0x100:
        raise ValueError('未使能起步核证禁止解除Halt')


def stop_disabled_start_new_enabled(session):
    """One initial stop, or one extra stop after confirmed cleanup.

    None means never attempted, True confirmed, False failed/uncertain.
    Observing Enabled creates a responsibility; it never permits retrying an
    earlier failed cleanup or bypassing SDO quarantine.
    """
    previous = getattr(session, '_probe_cleanup_confirmed', None)
    if (getattr(session.client, 'quarantined', False) or previous is False
            or previous is True and getattr(session, '_probe_extra_stop_used', False)):
        return None
    if previous is True:
        session._probe_extra_stop_used = True
    session.needs_stop = True
    session._probe_cleanup_confirmed = False  # Attempted, until confirmed.
    confirmed = session.stop_after_failure()
    session._probe_cleanup_confirmed = confirmed
    return confirmed


def run_disabled_start_probe(session, args, specs, journal, guard):
    """Bench-only Switched-on capability probe; never enables operation."""
    if getattr(args, '_disabled_start_action', None) != 'probe_pp_start':
        raise ValueError('未使能核证来源动作不符')
    return _run_disabled_start(session, args, specs, journal, guard, move=False)


def run_disabled_start_move(session, args, specs, journal, guard):
    """Sealed bench absolute/relative move, with a fresh preload every time."""
    if getattr(args, '_disabled_start_action', None) not in ('absolute', 'relative'):
        raise ValueError('严格失能PP运动来源动作不符')
    require_velocity_library()
    return _run_disabled_start(session, args, specs, journal, guard, move=True)


def _run_disabled_start(session, args, specs, journal, guard, move):
    """One deadline and one cleanup responsibility across preload and travel.

    E06 4.1.4 table 4-7, 6.3.3 tables 6-8/6-9, 6.3.4 and 6.3.6:
    The preload keeps bit3=0 and bit8=1 in every business controlword. The manual does not
    guarantee setpoint acceptance in Switched on: an absent fresh ACK fails
    without escalation to Operation enabled. Halt bit10 is not completion.
    """
    import canopen as protocol
    if not getattr(args, '_disabled_start_origin', False):
        raise ValueError('起步核证缺少测试台单次审阅来源')
    if not move and args.target != args._bench_expected_start:
        raise ValueError('起步核证只允许当前位置，不允许位移目标')
    overall = time.monotonic() + min(args.move_timeout, 15.0)
    original_deadline = session.deadline
    session.deadline = lambda: min(overall, original_deadline())
    session.needs_stop = False
    session._probe_operation_enabled_observed = False
    session._probe_extra_stop_used = False
    session._disabled_start_enable_authorized = False
    session._disabled_start_operation_enable_requested = False
    session._disabled_start_travel_trigger_attempted = False
    session._disabled_start_current_demand_expected = False
    error = None
    cleanup_confirmed = None
    session._probe_cleanup_confirmed = None
    disabled_confirmed = position_unchanged = zero_velocity_confirmed = None
    no_fault_following_error_confirmed = None
    position_target_confirmed = None
    operation_enable_requested = halt_released = halt_release_attempted = travel_executed = motion_complete = moving = False
    before = args._bench_expected_start
    current_state = protocol.CIA402_DISABLED
    last = None

    def read(key, cleanup=False):
        spec = specs[key]
        raw = session.call(original_deadline() if cleanup else session.deadline(), '起步核证读取 ' + key,
                           session.client.read, spec['index_value'], spec['sub'])
        if len(raw) != spec['size']:
            raise ValueError(key + ' 读回宽度不符')
        return int.from_bytes(raw, 'little', signed=spec['signed'])

    def check(phase, states=None, updates=None, demand=False):
        nonlocal last
        if time.monotonic() >= overall:
            raise ValueError('起步核证整体超时')
        last = guard(session, phase, states=(current_state,) if states is None else states,
                     updates=updates, moving=moving)
        if time.monotonic() >= overall:
            raise ValueError('起步核证整体超时')
        if demand and (last['position_demand'] != before or last['target_position'] != before):
            raise ValueError('当前位置握手后607A/6062未同时等于起点；保持Halt，拒绝继续')
        if demand and last['statusword'] & protocol.CIA402_PP_SETPOINT_ACK:
            raise ValueError('Halt保持核证中ACK不再为0')
        return last

    def stable(phase, demand=False):
        for i in range(5):
            if i:
                deadline = time.monotonic() + .2
                if deadline > overall:
                    raise ValueError('起步核证整体时限不足以完成五次采样')
                while time.monotonic() < deadline:
                    session.pause(deadline)
            check('{}{}/5'.format(phase, i + 1), demand=demand)

    def write(key, value, demand=False):
        check('写前' + key, demand=demand)
        spec = specs[key]
        session.needs_stop = True  # Lost download ACK may mean the drive acted.
        journal.event('probe_write_attempt', object=key, expected=value)
        session.call(session.deadline(), '起步核证写入 ' + key, session.client.write,
                     spec['index_value'], spec['sub'], encode(spec, value))
        actual = read(key)
        journal.event('write_readback', object=key, expected=value, actual=actual)
        if actual != value:
            raise ValueError(key + ' 写读回不一致')
        check('写后' + key, updates={key: value})

    def control(word, next_state, label, require_ack_zero=False, demand=False, trigger_motion=False):
        nonlocal current_state, moving, operation_enable_requested, halt_released, halt_release_attempted, travel_executed
        sample = check('控制字前' + label, demand=demand)
        if require_ack_zero and sample['statusword'] & protocol.CIA402_PP_SETPOINT_ACK:
            raise ValueError('新命令触发前ACK已置位；拒绝复用陈旧ACK')
        if not session._disabled_start_enable_authorized:
            _require_unenabled_probe_word(word)
        elif not move or word not in (0x12f, 0x2f, 0x3f):
            raise ValueError('严格失能PP阶段禁止未审阅控制字')
        session.needs_stop = True
        if require_ack_zero and word & 0x10:
            session._probe_require_ack_zero = False  # Next observations belong to this attempted rising edge.
        if word == 0x12f:
            operation_enable_requested = True
            session._disabled_start_operation_enable_requested = True
        if move and word == 0x2f and not halt_release_attempted:
            halt_release_attempted = True
            halt_released = None  # Unknown until this same client's fresh readback.
        if trigger_motion:
            moving = True
            session._disabled_start_current_demand_expected = False
            travel_executed = True
            session._disabled_start_travel_trigger_attempted = True
        session.control(word, session.deadline())
        actual = read('controlword')
        journal.event('probe_control_readback', expected=word, actual=actual, phase=label)
        if not session._disabled_start_enable_authorized and (actual & 8 or not actual & 0x100):
            raise ValueError('未使能起步核证控制字读回bit3/Halt不符；禁止继续')
        if actual != word:
            raise ValueError('起步核证控制字读回不一致；Halt未确认')
        if move and word == 0x2f:
            halt_released = True
        deadline = session.deadline()
        while time.monotonic() < deadline:
            sample = check(label, states=(current_state, next_state), updates={'controlword': word})
            if sample['statusword'] & protocol.CIA402_MASK == next_state:
                current_state = next_state
                return sample
            session.pause(deadline)
        raise ValueError('起步核证状态迁移超时: ' + label)

    def ack(want, label):
        deadline = session.deadline()
        while time.monotonic() < deadline:
            sample = check(label)
            if bool(sample['statusword'] & protocol.CIA402_PP_SETPOINT_ACK) == want:
                return sample
            session.pause(deadline)
        raise ValueError('起步核证新ACK握手超时: ' + label)

    try:
        first = check('before_setup')
        if move:
            check_start(args, before, first['calibration'])
        stable('起步稳定采样')
        write('target_position', before)
        for key, value in [('profile_end_velocity', 0), ('profile_velocity', args.velocity),
                           ('profile_acceleration', args.acceleration), ('profile_deceleration', args.deceleration)]:
            write(key, value)
        # E06 table 4-2: keep bit3=0 (Disable operation), including handshake.
        for word, state in ((0x106, protocol.CIA402_READY), (0x107, protocol.CIA402_SWITCHED_ON)):
            control(word, state, '未使能Halt迁移{:04X}'.format(word))
        control(0x127, protocol.CIA402_SWITCHED_ON, '未使能Halt空闲')
        ack(False, 'current_ack_initial_zero')
        session._probe_require_ack_zero = True
        for phase in ('before_target', 'before_trigger'):
            sample = check(phase)  # The sole target is still the reviewed start.
            if sample['statusword'] & protocol.CIA402_PP_SETPOINT_ACK:
                raise ValueError('当前位置新命令触发前ACK提前置位；禁止复用陈旧ACK')
        control(0x137, protocol.CIA402_SWITCHED_ON, '未使能Halt当前位置新命令', require_ack_zero=True)
        ack(True, 'current_setpoint_ack')
        control(0x127, protocol.CIA402_SWITCHED_ON, '未使能Halt当前位置确认')
        ack(False, 'current_ack_return_zero')
        session._probe_require_ack_zero = True
        session._disabled_start_current_demand_expected = True
        check('current_demand_readback', demand=True)
        stable('Halt保持核证采样', demand=True)
        journal.event('probe_observed', current_setpoint_acknowledged=True, position_demand=before,
                      halt_held=True, halt_released=False, operation_enable_requested=False,
                      operation_enabled_observed=session._probe_operation_enabled_observed, travel_executed=False)
        if move:
            # Authorization is local to this sealed transaction, after fresh
            # current ACK + 6062 evidence. No historical probe grants it.
            check('before_halt_enable', demand=True)
            session._disabled_start_enable_authorized = True
            control(0x12f, protocol.CIA402_ENABLED, 'Halt保持使能', require_ack_zero=True, demand=True)
            # No new setpoint was sent by 12F. Every status through the final
            # trigger must still be ACK0, including the enabled stable window.
            session._probe_require_ack_zero = True
            stable('Halt使能零漂移采样', demand=True)
            check('before_halt_release', demand=True)
            control(0x2f, protocol.CIA402_ENABLED, '释放Halt保持当前位置', demand=True)
            check('after_halt_release', demand=True)
            check('before_target', demand=True)
            write('target_position', args.target, demand=True)
            check('before_trigger')
            control(0x3f, protocol.CIA402_ENABLED, '最终目标新命令', require_ack_zero=True, trigger_motion=True)
            ack(True, 'final_setpoint_ack')
            control(0x2f, protocol.CIA402_ENABLED, '最终目标ACK返回')
            ack(False, 'final_ack_return_zero')
            session._probe_require_ack_zero = True
            deadline = session.deadline()
            while time.monotonic() < deadline:
                sample = check('final_target_completion')
                if (sample['statusword'] & protocol.CIA402_HOMING_TARGET_REACHED
                        and sample['actual_position'] == args.target
                        and sample['position_demand'] == args.target and sample['actual_velocity'] == 0
                        and sample['controlword'] == 0x2f and sample['target_position'] == args.target):
                    motion_complete = True
                    journal.event('target_reached', actual_position=sample['actual_position'],
                                  actual_velocity=sample['actual_velocity'], position_demand=sample['position_demand'])
                    break
                session.pause(deadline)
            if not motion_complete:
                raise ValueError('最终目标新ACK/bit10/实际位置/6062/零速联合完成超时')
    except BaseException as exc:
        error = exc
        journal.event('exception', type=type(exc).__name__, message=str(exc),
                      quarantined=bool(getattr(session.client, 'quarantined', False)))
    finally:
        session.cancel_event = None
        session._probe_require_ack_zero = False
        session.deadline = original_deadline
        # Cleanup alone uses the original bounded 2 -> 0 stop/disable sequence.
        # It does not resume PP by writing an enabled word with bit8 cleared.
        if session.needs_stop:
            session._probe_cleanup_confirmed = False
            cleanup_confirmed = session.stop_after_failure()
        session._probe_cleanup_confirmed = cleanup_confirmed
        try:
            sw = read('statusword', cleanup=True)
            if sw & protocol.CIA402_MASK == protocol.CIA402_ENABLED:
                session._probe_operation_enabled_observed = True
                session.needs_stop = True
                error = error or ValueError('未使能核证收尾后意外Operation enabled；立即收尾')
                journal.event('unexpected_enabled_after_cleanup', statusword=sw)
                confirmed = stop_disabled_start_new_enabled(session)
                if confirmed is not None:
                    cleanup_confirmed = confirmed
                    # Report only fresh evidence after the additional writes.
                    disabled_confirmed = no_fault_following_error_confirmed = None
                    sw = read('statusword', cleanup=True)
            disabled_confirmed = sw & protocol.CIA402_DISABLED_MASK == protocol.CIA402_DISABLED
            no_fault_following_error_confirmed = not bool(sw & (protocol.CIA402_FAULT | protocol.CIA402_PP_FOLLOWING_ERROR))
            pos = read('actual_position', cleanup=True)
            position_unchanged = pos == before
            position_target_confirmed = pos == args.target
            velocity = read('actual_velocity', cleanup=True)
            zero_velocity_confirmed = velocity == 0
            journal.event('after_cleanup', statusword=sw, actual_position=pos, actual_velocity=velocity,
                          disabled_confirmed=disabled_confirmed, position_unchanged=position_unchanged,
                          position_target_confirmed=position_target_confirmed, zero_velocity_confirmed=zero_velocity_confirmed)
            if (not disabled_confirmed or not (position_target_confirmed if move else position_unchanged) or not zero_velocity_confirmed
                    or not no_fault_following_error_confirmed):
                raise ValueError('严格失能PP清理后失能/最终目标/零速未同时确认' if move
                                 else '起步核证清理后失能/起点/零速未同时确认')
            check_envelope(args, before, pos)
            if error is None and not cleanup_confirmed:
                raise ValueError('起步核证未确认有界快停/失能收尾')
        except (Exception, KeyboardInterrupt) as exc:
            error = error or exc
            journal.event('after_cleanup_unavailable', message=str(exc))
        cleanup_ok = (cleanup_confirmed and disabled_confirmed is True
                      and (position_target_confirmed is True if move else position_unchanged is True)
                      and zero_velocity_confirmed is True
                      and no_fault_following_error_confirmed is True)
        completed = error is None and cleanup_ok and (motion_complete if move else True)
        journal.event('result', action=args._disabled_start_action, strict_disabled_start=True, completed=completed,
                      disabled_confirmed=disabled_confirmed, position_unchanged=position_unchanged,
                      position_target_confirmed=position_target_confirmed,
                      zero_velocity_confirmed=zero_velocity_confirmed, cleanup_confirmed=cleanup_confirmed,
                      no_fault_following_error_confirmed=no_fault_following_error_confirmed,
                      mechanical_stop_confirmed=False, halt_released=halt_released, operation_enable_requested=operation_enable_requested,
                      halt_release_attempted=halt_release_attempted,
                      operation_enable_attempted=operation_enable_requested, travel_trigger_attempted=travel_executed,
                      completed_motion=completed if move else False,
                      operation_enabled_observed=session._probe_operation_enabled_observed,
                      travel_executed=travel_executed,
                      travel_executed_semantics='legacy protocol trigger attempt, not physical displacement evidence',
                      independent_estop_required=not cleanup_ok)
    if error is not None:
        raise error
    if not completed:
        raise ValueError('起步核证未确认失能；立即使用独立急停')


def offline_bus(canopen, specs, args):
    """Shared MockNode encodes every response; no SocketCAN transport exists."""
    import mockbus
    pp = specs['pp_protocol']

    class PositionNode(mockbus.MockNode):
        def handle(self, frame, now):
            # Behaviour model only; standard MockNode owns all wire handling.
            super().handle(frame, now)
            word = int.from_bytes(self.od[addr('controlword')].data, 'little')
            reached = canopen.CIA402_ENABLED | canopen.CIA402_HOMING_TARGET_REACHED
            state = {canopen.CIA402_DISABLE: canopen.CIA402_DISABLED,
                     canopen.CIA402_QUICK_STOP_COMMAND: canopen.CIA402_QUICK_STOP,
                     canopen.CIA402_SHUTDOWN: canopen.CIA402_READY,
                     canopen.CIA402_SWITCH_ON: canopen.CIA402_SWITCHED_ON,
                     canopen.CIA402_ENABLE: reached, pp['idle_word']: reached,
                     pp['trigger_word']: canopen.CIA402_ENABLED | canopen.CIA402_PP_SETPOINT_ACK}.get(
                         word, canopen.CIA402_DISABLED)
            self.od[addr('statusword')].data = state.to_bytes(2, 'little')
            if word == pp['idle_word'] and self.triggered:
                self.od[addr('actual_position')].data = encode(specs['actual_position'], args.target)
            if word == pp['trigger_word']:
                self.triggered = True
            self.od[addr('modes_of_operation_display')].data = self.od[addr('modes_of_operation')].data

    def addr(key):
        return specs[key]['index_value'], specs[key]['sub']

    values = dict(zip(CALIBRATION, args.offline_calibration))
    values.update(zip(LIMITS, args.offline_limits))
    values.update(actual_position=args.start_position, actual_velocity=0, halt_option=1,
                  position_demand=args.start_position, statusword=canopen.CIA402_DISABLED, controlword=canopen.CIA402_DISABLE,
                  control_mode_select=0, modes_of_operation=pp['mode'], modes_of_operation_display=pp['mode'],
                  target_position=0, profile_velocity=0, profile_end_velocity=0,
                  profile_acceleration=0, profile_deceleration=0)
    node = PositionNode(args.node, od={addr(key): mockbus.OdEntry(encode(specs[key], value), writable=True)
                                      for key, value in values.items()})
    node.triggered = False
    return mockbus.MockBus([node], loopback=False)
