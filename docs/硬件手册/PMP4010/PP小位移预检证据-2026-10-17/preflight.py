#!/usr/bin/env python3
"""Read-only PP preflight; default is an offline object/guard plan.

Uses the shipped BenchController configuration/read/latch path and exactly one
shipped SdoClient for node 1. Never scans, downloads, enables, stops, or recovers.
A live run requires separate explicit permission; this script is not permission.
"""
import argparse
import datetime
from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True

KEYS = ('control_mode_select', 'controlword', 'statusword',
        'modes_of_operation', 'modes_of_operation_display',
        'velocity_feedback_unit', 'actual_position', 'actual_velocity',
        'encoder_increments', 'encoder_motor_revolutions',
        'gear_motor_revolutions', 'gear_output_revolutions',
        'software_position_limit', 'software_position_limit_max', 'home_offset',
        'max_velocity', 'max_motor_speed', 'max_profile_acceleration', 'max_deceleration',
        'target_position', 'profile_velocity', 'profile_end_velocity',
        'profile_acceleration', 'profile_deceleration')
SAMPLE_KEYS = ('statusword', 'modes_of_operation', 'modes_of_operation_display',
               'velocity_feedback_unit', 'actual_position', 'actual_velocity')
IDENTITY = ((0x1000, 0, 4), (0x1008, 0, None), (0x100a, 0, None),
            (0x1018, 0, 1), (0x1018, 1, 4), (0x1018, 2, 4),
            (0x1018, 3, 4), (0x1018, 4, 4))


def emit(event, **data):
    print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          event=event, **data), ensure_ascii=False), flush=True)


class Output:
    cancel_event = None
    def check_cancel(self):
        pass
    def __getattr__(self, name):
        return lambda message='', *a, **kw: emit('tool_' + name, message=str(message))


def load(root):
    root = root.resolve(strict=True)
    driver = root / 'drivers/pmp4010'
    if not (driver / 'pmp4010.py').is_file():
        driver = root / 'device/drivers/pmp4010/tools'
    paths = (driver, root / 'hwtui', root / 'tools/hwtui', root)
    sys.path[:0] = [str(p) for p in paths if p.is_dir()]
    filename = driver / 'pmp4010.py'
    spec = importlib.util.spec_from_file_location('preflight_shipped_tool', filename)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    if not tool.require_std_layer('只读预检'):
        raise ValueError('缺少同源标准层')
    import pmp4010_bench as bench
    import pmp4010_position as position
    import pmp4010_params as params
    import pmp4010_velocity as velocity
    import canopen_consts
    import canopen_wire
    modules = (tool, bench, position, params, velocity, tool._canopen, tool._canbus, canopen_consts, canopen_wire)
    for module in modules:
        path = Path(module.__file__).resolve(strict=True)
        if root not in path.parents:
            raise ValueError('模块不属于指定同源包: ' + str(path))
        emit('source', path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return tool, bench, position


def objects(tool, position):
    from pmp4010_params import TYPES
    od = tool.load_od()
    specs = {}
    for e in od['entries']:
        if e.get('type') in TYPES and e.get('index') and e['status'] == 'verified' and e['cite']:
            size, signed = TYPES[e['type']]
            specs[e['key']] = dict(index_value=int(e['index'], 0), sub=e['sub'], size=size, signed=signed)
    specs.update({k:v for k,v in position.specifications(od).items() if k != 'pp_protocol'})
    return od, {k: specs[k] for k in KEYS}


def collect(controller, tool, position, client, interval):
    # No replacement client, no recovery, and no continuation after any failure.
    raw_read = client.read
    reads = 0
    def read(index, sub=0):
        nonlocal reads
        reads += 1
        emit('upload_begin', ordinal=reads, index='{:04X}'.format(index), sub=sub)
        value = raw_read(index, sub)
        emit('upload_done', ordinal=reads, index='{:04X}'.format(index), sub=sub, raw_hex=value.hex())
        return value
    client.read = read
    identity = {}
    for index, sub, size in IDENTITY:
        raw = client.read(index, sub)
        if size is not None and len(raw) != size:
            raise ValueError('身份对象宽度错误')
        identity['{:04X}:{:02X}'.format(index, sub)] = raw.hex() if size is None else int.from_bytes(raw, 'little')
    if identity['1018:00'] < 4:
        raise ValueError('1018身份对象少于4项')
    values = {key: controller._read(client, key) for key in KEYS}
    emit('snapshot', identity=identity, values=values)
    cal = position.calibration([values[k] for k in position.CALIBRATION])
    samples = []
    for n in range(5):
        if n:
            time.sleep(interval)
        sample = {k: controller._read(client, k) for k in SAMPLE_KEYS}
        samples.append(sample)
        emit('stationary_sample', ordinal=n+1, values=sample)
    p = tool._canopen
    reasons = []
    if values['control_mode_select'] != 0:
        reasons.append('2002:01不是CiA402=0')
    if values['home_offset'] != 0:
        reasons.append('607C非零；本次PP预检拒绝')
    if values['software_position_limit'] >= values['software_position_limit_max']:
        reasons.append('607D上下限倒置/相等')
    if values['modes_of_operation'] != values['modes_of_operation_display']:
        reasons.append('6060/6061不一致')
    start = values['actual_position']
    for sample in [values] + samples:
        if sample['actual_position'] != start or sample['actual_velocity'] != 0:
            reasons.append('多次6064/606C未确认静止')
        if sample['velocity_feedback_unit'] not in (0, 1):
            reasons.append('2006:12未知速度单位')
        if any(sample[k] != values[k] for k in ('modes_of_operation', 'modes_of_operation_display', 'velocity_feedback_unit')):
            reasons.append('采样期间模式/速度单位发生变化')
        if sample['statusword'] & p.CIA402_FAULT or not sample['statusword'] & p.CIA402_HOMING_TARGET_REACHED:
            reasons.append('故障或target reached未置位')
        if sample['statusword'] & p.CIA402_DISABLED_MASK != p.CIA402_DISABLED:
            reasons.append('当前不是确认失能状态；本次预检不替代失能动作')
    degrees = Fraction(cal['motor_degrees_per_user_unit'])
    delta = int(Fraction(1, 10) / degrees)  # floor: never exceed 0.1 motor degree
    hard_max = int(Fraction(1) / degrees)
    if delta < 1:
        reasons.append('当前标定下0.1度不足1个用户单位')
    target = start + delta
    if not -0x80000000 <= target <= 0x7fffffff:
        reasons.append('目标超出Int32')
    lo, hi = values['software_position_limit'], values['software_position_limit_max']
    if not lo <= start <= target <= hi:
        reasons.append('起点或目标超出607D')
    rpm = position.unit_to_velocity(values['profile_velocity'], cal)
    if (values['profile_velocity'] <= 0 or values['profile_velocity'] > values['max_velocity']
            or rpm > values['max_motor_speed'] or values['profile_acceleration'] <= 0
            or values['profile_acceleration'] > values['max_profile_acceleration']
            or values['profile_deceleration'] <= 0
            or values['profile_deceleration'] > values['max_deceleration']):
        reasons.append('现有PP速度/加减速不满足设备上限')
    result = dict(calibration=cal, start=start, candidate_delta=delta, candidate_target=target,
                  candidate_motor_degrees=str(delta*degrees), hard_one_degree_max_units=hard_max,
                  existing_profile_motor_rpm=str(rpm), live_read_count=reads,
                  rejection_reasons=sorted(set(reasons)), motion_authorized=False,
                  requires_human_script_limits=True, requires_explicit_od_reason=True,
                  requires_review_current_profile=True, writes=0, nmt=0)
    emit('preflight_result', **result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True, type=Path)
    parser.add_argument('--live-read-only', action='store_true')
    parser.add_argument('--i-know-rt-is-running', action='store_true')
    parser.add_argument('--timeout', type=float, default=.25)
    args = parser.parse_args()
    if not 0 < args.timeout <= 1:
        parser.error('SDO超时须为0到1秒之间的主机策略')
    tool, bench, position = load(args.source_root)
    od, specs = objects(tool, position)
    od_path = Path(tool.od_json_path()).resolve(strict=True)
    if args.source_root.resolve() not in od_path.parents:
        raise ValueError('OD不属于指定同源包')
    emit('od_source', path=str(od_path), sha256=hashlib.sha256(od_path.read_bytes()).hexdigest())
    emit('plan', node=1, interface='can1', bitrate=1000000,
         identity=[dict(index='{:04X}'.format(i),sub=s,size=n) for i,s,n in IDENTITY],
         objects=specs, stationary_samples=5, interval_seconds=.2,
         od_gaps=[dict(key=e['key'], status=e['status'], reason=e['note']) for e in od['entries'] if e['status']!='verified'],
         writes=0, nmt=0, new_client_on_failure=False,
         live_read_only=args.live_read_only)
    if not args.live_read_only:
        emit('offline_complete', actual_can_opens=0, actual_can_sends=0)
        return 0
    if not args.i_know_rt_is_running:
        parser.error('真实只读预检须获授权并显式确认 --i-know-rt-is-running；没有停服声明')
    library = position.require_velocity_library()
    library_path = Path(library._name).resolve(strict=True)
    if args.source_root.resolve() not in library_path.parents:
        raise ValueError('真实预检速度换算库不属于指定同源包')
    emit('velocity_library', path=str(library_path), abi=library.pmp4010_velocity_abi_version(),
         sha256=hashlib.sha256(library_path.read_bytes()).hexdigest())
    cfg, cfg_path = tool.load_device_json()
    item, _ = tool.find_can_bus(cfg, tool.LOGICAL_BUS)
    if (cfg_path != '/etc/km3/device.json' or cfg.get('device',{}).get('id') != 'km3-test-001'
            or item['iface'] != 'can1' or item['bitrate'] != 1000000):
        raise ValueError('设备身份/配置路径/目标CAN不匹配；拒绝打开CAN')
    options = tool.build_parser().parse_args(['--node', '1', '--node-range', '1', '--timeout', str(args.timeout)])
    controller = bench.BenchController(tool, options)
    controller.rt_ack = True  # explicit CLI acknowledgement above, not a stopped-service assertion
    out = Output()
    client = None
    try:
        controller._configuration(out)  # existing RT/interface/OD/same-source gates, then one bus open
        client = tool._canopen.SdoClient(controller.bus, 1, timeout=args.timeout)
        result = collect(controller, tool, position, client, .2)
        return 2 if result['rejection_reasons'] else 0
    except BaseException as exc:
        if client is not None:
            controller._latch(client, 1, '只读预检', exc, out)
        emit('preflight_failed', type=type(exc).__name__, message=str(exc),
             quarantined=bool(client and client.quarantined), later_requests=0, motion_authorized=False)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        # No enabled_sessions ever exist in this script, so close sends no disable.
        controller.close(out)


if __name__ == '__main__':
    sys.exit(main())
