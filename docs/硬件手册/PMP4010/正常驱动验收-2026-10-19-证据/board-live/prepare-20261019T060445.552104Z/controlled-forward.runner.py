#!/usr/bin/env python3
"""One sealed SimpleController forward review, stdin approval, observe, stop.

Preparation alone does not authorize deploying or running this on hardware.
Live mode requires a separate read-stage authorization and the exact subsequent
EXECUTE digest from the coordinator. No drive control write precedes that line.
"""
import argparse
import datetime
from fractions import Fraction
import hashlib
import importlib
import json
import os
from pathlib import Path
import select
import signal
import sys
import threading
import time
import uuid

sys.dont_write_bytecode = True
EXPECTED_MANIFEST = '26c7f2bdcc7e3b79e899ab06b703561e6dca2c377983ab7454908de6d01c3e0d'
EXPECTED_PAYLOAD = Path('/var/tmp/km3-pmp4010-state-20261019-43b0b2355c3b/payload')


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_payload(root):
    require(sha(root/'payload-manifest.json') == EXPECTED_MANIFEST, '冻结manifest不符')
    manifest = json.loads((root/'payload-manifest.json').read_text())
    for item in manifest['files']:
        p = root/item['path']
        require(root in p.resolve().parents, '包路径越界')
        if 'symlink' in item:
            require(p.is_symlink() and os.readlink(p) == item['symlink'], '包链接不符')
        else:
            require(sha(p) == item['sha256'], '包文件哈希不符: '+item['path'])
    sys.path[:0] = [str(root/'drivers/pmp4010'), str(root/'hwtui'), str(root)]
    modules = {n: importlib.import_module(n) for n in
               ('pmp4010', 'pmp4010_simple', 'pmp4010_position', 'pmp4010_velocity')}
    for name, module in modules.items():
        require(root in Path(module.__file__).resolve().parents, '模块来源不符: '+name)
    return modules


def passive_owner_check(expected_raw):
    """OS files only. Fail closed if socket counts cannot exclude another owner."""
    protocols = {}
    for line in Path('/proc/net/protocols').read_text().splitlines()[1:]:
        cols = line.split()
        if cols and cols[0].startswith('CAN'):
            protocols[cols[0]] = int(cols[2])
    require('CAN_RAW' in protocols, '内核未公开CAN_RAW socket计数')
    require(protocols['CAN_RAW'] == expected_raw and
            all(v == 0 for k, v in protocols.items() if k != 'CAN_RAW'),
            '存在额外CAN socket或计数不符: '+repr(protocols))
    related = []
    own = os.getpid()
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == own:
            continue
        try:
            cmd = (p/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            continue
        # Match executable script names, not arbitrary shell argument substrings.
        words = cmd.split()
        names = {Path(w).name for w in words[:2]}
        if any(n.startswith(('pmp4010', 'drive_probe', 'pty_runner', 'board_app')) or
               n in ('candump', 'cansend', 'cangen', 'canplayer') for n in names):
            related.append({'pid': int(p.name), 'cmdline': cmd})
    require(not related, '存在外部测试进程: '+repr(related))
    return {'protocol_socket_counts': protocols, 'related_test_processes': related}


class Output:
    def __init__(self, path):
        self.cancel_event = threading.Event()
        self.stream = path.open('x', encoding='utf-8')
    def event(self, name, data):
        row = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   event=name, data=data)
        line = json.dumps(row, ensure_ascii=False, default=str)
        self.stream.write(line+'\n')
        self.stream.flush()
        print(line, flush=True)
    def info(self, text): self.event('信息', str(text))
    plain = title = cmd = info
    def warn(self, text): self.event('警告', str(text))
    def bad(self, text): self.event('错误', str(text))
    ok = info
    def field(self, key, value): self.info(str(key)+': '+str(value))
    def c(self, name, text): return str(text)
    def check_cancel(self):
        if self.cancel_event.is_set():
            raise KeyboardInterrupt('验收已中止')


def run_transaction(modules, output_dir, rt_reason, od_reason, owner_check,
                    approve, observe_seconds=10, hardware=False):
    tool = modules['pmp4010']
    args = tool.build_parser().parse_args([
        '--manual', '--node', '1', '--node-range', '1', '--manual-velocity', '100',
        '--timeout', '0.1', '--state-timeout', '2', '--move-timeout', '10',
        '--motion-log', str(output_dir/'motion.jsonl')])
    out = Output(output_dir/'runner.jsonl')
    ctl = modules['pmp4010_simple'].SimpleController(tool, args)
    executing = False
    completed = False
    failure = None
    session = None
    review = None
    cleanup = []
    try:
        out.event('显式会话理由', {'RT': rt_reason, 'OD': od_reason})
        out.event('打开总线前被动检查', owner_check(0))
        cfg, _ = tool.load_device_json()
        item, _ = tool.find_can_bus(cfg, tool.LOGICAL_BUS)
        require(item.get('iface') == 'can1' and item.get('bitrate') == 1000000,
                '仅允许can-2映射can1/1Mbps')
        ctl.rt_ack = True  # Explicit nonempty coordinator reason recorded above.
        require(ctl.scan(out) == [1], '仅节点1，扫描不符')
        ctl.authorize()
        ctl.od_reason = od_reason
        row = ctl.row(1)
        step, degrees, rpm = ctl.quantities(row)
        require(step >= 1 and 0 < degrees <= Fraction('0.1'), '量化0.1度预算不符')
        # Tighten only the per-action budget; retain authorized origin +/-1deg
        # intersected with measured 607D limits from SimpleController.authorize.
        ctl.settings['max_displacement'] = step
        review = ctl.review_action('forward', out)
        session = review['session']
        require(ctl.context == ('can1', 1000000), '实际controller接口不符')
        require(review['target']-review['start'] == step and review['velocity'] == 100,
                '冻结动作不符')
        require(not ctl.enabled_sessions and not session.needs_stop,
                '读阶段不可持有任何控制写责任')
        snapshot = dict(review_nonce=uuid.uuid4().hex,
                        node=1, interface='can1', mode='PP1', start=review['start'],
                        target=review['target'], displacement=step,
                        motor_degrees=str(degrees), velocity_units_per_s=100,
                        velocity_rpm=str(rpm), origin=ctl.origin,
                        settings=review['settings'], calibration=review['calibration'],
                        values=review['before'], lines=review['lines'],
                        RT_reason=rt_reason, OD_reason=od_reason)
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True,
                       ensure_ascii=False, default=str).encode()).hexdigest()
        (output_dir/'review.json').write_text(json.dumps(dict(snapshot=snapshot,
                         digest=digest), ensure_ascii=False, indent=2, default=str)+'\n')
        out.event('REVIEW_READY', dict(snapshot=snapshot, digest=digest,
                         required_line='EXECUTE '+digest,
                         abort_line='ABORT', read_phase_control_writes=0))
        if not approve(digest):
            out.event('审阅中止', {'control_writes': 0})
            return 2
        out.event('执行前被动检查', owner_check(1))
        # Public refresh makes cached telemetry fresh after a long review pause.
        # It does not replace/mutate/recompute the sealed review or its client.
        require(ctl.refresh(out), '执行前刷新失败')
        fresh = ctl.row(1)
        require(not fresh['lost'] and not fresh['quarantined'], '执行前已失联/隔离')
        require(fresh['values']['actual_position'] == review['start'] and
                fresh['values']['actual_velocity'] == 0, '审阅暂停期间起点漂移/速度非零')
        require(ctl.manual_sessions[1] is session, '禁止替换SDO会话')
        executing = True
        ctl.execute_action(review, out)
        deadline = time.monotonic()+observe_seconds
        stable = 0
        while time.monotonic() < deadline:
            out.check_cancel()
            require(ctl.manual_sessions[1] is session, '禁止替换SDO会话')
            require(ctl.refresh(out), '监测刷新失败')
            row = ctl.row(1)
            require(not row['lost'] and not row['quarantined'], '监测失联/隔离')
            values = row['values']
            out.event('动作后实读', values)
            reached = (values['actual_position'] == review['target'] and
                       values['position_demand'] == review['target'] and
                       values['target_position'] == review['target'] and
                       values['actual_velocity'] == 0 and
                       bool(values['statusword'] & 0x400))
            stable = stable+1 if reached else 0
            if stable >= 2:
                completed = True
                break
            time.sleep(.05)
        require(completed, '动作监测超时；没有两份到达目标/零速度/bit10一致快照')
    except BaseException as exc:
        failure = type(exc).__name__+': '+str(exc)
        out.event('runner_failure', {'reason': failure,
                                    'first_failure': ctl.failure_diagnostics})
    finally:
        # Existing controller methods own all controlwords, deadlines, failure
        # cleanup and quarantine rules. No raw SDO writes or replacement client.
        if executing and session is not None:
            for action in ('stop', 'disable'):
                if (ctl.context[0], 1) in ctl.blocked or getattr(session.client, 'quarantined', False):
                    cleanup.append({'action': action, 'skipped': '已隔离；禁止重试'})
                    continue
                try:
                    ctl.safety_action(action, out)
                    cleanup.append({'action': action, 'values': ctl.row(1)['values']})
                except BaseException as exc:
                    cleanup.append({'action': action, 'error': str(exc)})
                    failure = failure or ('清理未确认: '+str(exc))
        elif ctl.enabled_sessions:
            failure = failure or '读阶段异常出现控制写责任'
        close_result = ctl.close(out)
        if any(not result['disabled_confirmed'] for result in close_result):
            failure = failure or '退出失能未确认'
        out.event('FINAL', dict(completed=completed, failure=failure,
                  hardware_motion_acceptance=hardware and completed and failure is None,
                  mock_only=not hardware,
                  cleanup=cleanup, close_results=close_result,
                  close_lines=ctl.shutdown_lines,
                  final_velocity_note='606C实读在disable0完整快照中；close6后只再读6041并关闭总线',
                  mechanical_stop_confirmed=False))
        out.stream.close()
    return 0 if completed and failure is None else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('mock', 'live'))
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rt-reason', required=True)
    parser.add_argument('--od-reason', required=True)
    parser.add_argument('--mock-repo', type=Path)
    parser.add_argument('--mock-library', type=Path)
    parser.add_argument('--mock-case', choices=('success', 'abort', 'drift', 'fault', 'uncertain'), default='success')
    args = parser.parse_args()
    require(args.rt_reason.strip() and args.od_reason.strip(), '须显式提供会话理由')
    root = args.payload.resolve(strict=True)
    if args.mode == 'live':
        require(root == EXPECTED_PAYLOAD, 'live只允许已冻结43b包')
        require(args.mock_repo is None and args.mock_library is None and args.mock_case == 'success',
                'live禁止mock覆盖')
    args.output.mkdir(parents=True, exist_ok=False)
    modules = load_payload(root)
    velocity = modules['pmp4010_velocity']
    os.environ.pop(velocity.LIBRARY_ENV, None)
    if args.mode == 'mock':
        require(args.mock_repo is not None and args.mock_library is not None, 'mock须指定本地夹具/host库')
        os.environ[velocity.LIBRARY_ENV] = str(args.mock_library.resolve(strict=True))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        support = importlib.import_module('mock_runner_support')
        return support.run_mock(args, modules, run_transaction)
    velocity.require_velocity_library()
    def interrupted(signum, frame):
        raise KeyboardInterrupt('收到中止信号 '+str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    def approval(digest):
        ready, _, _ = select.select([sys.stdin], [], [], 300)
        if not ready:
            return False
        return sys.stdin.readline().rstrip('\r\n') == 'EXECUTE '+digest
    return run_transaction(modules, args.output, args.rt_reason, args.od_reason,
                           passive_owner_check, approval, hardware=True)


if __name__ == '__main__':
    raise SystemExit(main())
