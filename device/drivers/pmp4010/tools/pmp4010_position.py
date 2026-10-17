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

CALIBRATION = ('encoder_increments', 'encoder_motor_revolutions',
               'gear_motor_revolutions', 'gear_output_revolutions')
LIMITS = ('max_velocity', 'max_motor_speed', 'max_profile_acceleration', 'max_deceleration')
REQUIRED = CALIBRATION + LIMITS + ('actual_position', 'statusword', 'controlword',
    'control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
    'target_position', 'profile_velocity', 'profile_end_velocity',
    'profile_acceleration', 'profile_deceleration')


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
                or args.velocity * Fraction(cal['gear_ratio']) / Fraction(cal['resolution']) * 60 > limits['max_motor_speed']):
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
    values.update(actual_position=args.start_position, statusword=canopen.CIA402_DISABLED, controlword=canopen.CIA402_DISABLE,
                  control_mode_select=0, modes_of_operation=pp['mode'], modes_of_operation_display=pp['mode'],
                  target_position=0, profile_velocity=0, profile_end_velocity=0,
                  profile_acceleration=0, profile_deceleration=0)
    node = PositionNode(args.node, od={addr(key): mockbus.OdEntry(encode(specs[key], value), writable=True)
                                      for key, value in values.items()})
    node.triggered = False
    return mockbus.MockBus([node], loopback=False)
