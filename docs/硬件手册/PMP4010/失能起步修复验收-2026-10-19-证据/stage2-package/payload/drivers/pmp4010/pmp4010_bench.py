"""Non-RT bench model. One serialized bus, independent telemetry/action clients.

All object addresses/types come from the checked OD; PP motion reuses the CLI
position module. Disabled preparation is a separate two-object transaction. This module opens no bus until
an explicitly acknowledged background operation asks for it.
"""
import copy
from fractions import Fraction
import threading
import time
import uuid
from pathlib import Path

import pmp4010_position as position
from pmp4010_params import TYPES

# One policy is consumed by both entry and review. Read-only navigation never
# enters confirmation; every supported write retains OD and two-stage consent.
ACTION_POLICY = {
    'absolute': dict(label='绝对位置', confirm=True, od=True, settings=True, batch=False, input=True),
    'relative': dict(label='相对位置', confirm=True, od=True, settings=True, batch=False, input=True),
    'prepare_pp': dict(label='失能准备', confirm=True, od=True, settings=True, batch=False, input=False),
    'probe_pp_start': dict(label='未使能起步核证', confirm=True, od=True, settings=True, batch=False, input=False),
    'zero': dict(label='设零点', confirm=True, od=True, settings=True, batch=False, input=False),
    'enable': dict(label='使能', confirm=True, od=True, settings=True, batch=False, input=False),
    'disable': dict(label='失能', confirm=True, od=True, settings=False, batch=False, input=False),
    'stop': dict(label='快停', confirm=True, od=True, settings=False, batch=False, input=False),
    'disable_all': dict(label='全部失能', confirm=True, od=True, settings=False, batch=True, input=False, member='disable'),
    'stop_all': dict(label='全部快停', confirm=True, od=True, settings=False, batch=True, input=False, member='stop'),
    **{key: dict(label=label, confirm=False, od=False, settings=False, batch=False, input=False)
       for key, label in [('scan', '扫描'), ('refresh', '刷新'), ('detail', '详情'), ('help', '帮助')]},
}
ACTIONS = {key: policy['label'] for key, policy in ACTION_POLICY.items() if policy['confirm']}


def action_policy(action):
    if action not in ACTION_POLICY:
        raise ValueError('不支持的动作；非零速度运动未开放')
    return ACTION_POLICY[action]


def write_text(item):
    """OD/type/value formatting also supports a future verified 60FFh plan."""
    value = '0x{:04X}'.format(item['value']) if item['key'] == 'controlword' else str(item['value'])
    return '写 {:04X}h = {}（{}{}）'.format(item['index'], value, item['type'],
        '，' + item['note'] if item.get('note') else '')
TELEMETRY = ('statusword', 'actual_position', 'actual_velocity',
             'modes_of_operation_display', 'error_code_object', 'torque_feedback',
             'velocity_feedback_unit', 'profile_velocity', 'profile_acceleration',
             'profile_deceleration', 'home_offset', 'control_mode_select',
             'modes_of_operation', 'target_position', 'profile_end_velocity',
             'software_position_limit', 'software_position_limit_max', 'controlword',
             'halt_option', 'position_demand') + position.LIMITS


def state_text(row):
    if row.get('quarantined'):
        return '已隔离；状态未知'
    if row.get('lost'):
        return '失联；保留旧值'
    if not row.get('online'):
        return '离线'
    sw = row.get('values', {}).get('statusword', 0)
    if sw & 8:  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        return '故障'
    if sw & 4:  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        return '使能'
    return '未使能'


def mode_text(value):
    return {1: 'PP', 6: 'HM', 8: 'CSP', 10: 'CST', 3: 'PV', 9: 'CSV'}.get(value, str(value))


def velocity_unit(values):
    return {0: 'rpm', 1: '用户单位'}.get(values.get('velocity_feedback_unit'), '单位未知')


class SilentOutput:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class PrepareReview(dict):
    """Origin survives field deletion, cancellation, consumption and deepcopy."""


class DisabledStartReview(dict):
    """A strict-disabled PP review cannot become an ordinary action by editing fields."""


class BenchController:
    """Thread-safe cached data; callers run I/O on worker threads only.

    The lock covers each whole operation, including an entire position action.
    Separate clients never consume the shared receive queue concurrently.
    Any quarantined client latches the *target*, across scan/refresh/action.
    There is deliberately no recovery/reset/reconnect API in this session.
    """
    freshness = 1.5

    def __init__(self, tool, args):
        self.tool, self.args = tool, copy.copy(args)
        self.settings = {k: getattr(args, k, None) for k in ('soft_min', 'soft_max', 'max_displacement')}
        self.rt_ack = False
        self.od_reason = None
        self.context = None
        self.bus_status = '等待读取总线状态'
        self.bus = None
        self.lock = threading.Lock()
        self.refresh_clients = {}
        self.scan_clients = {}
        self.blocked = {}
        self._rows = [self._empty() for _ in range(4)]
        self.specs = None
        self.objects = None
        self.od = None
        self._rt_seen = False
        self._rt_state = None
        self.enabled_sessions = {}
        self.shutdown_lines = []
        self.shutdown_results = None
        self._prepare_review = None
        self._disabled_start_review = None

    @staticmethod
    def _empty(node=None):
        return dict(node=node, online=False, lost=False, quarantined=False,
                    quarantine_reason='', values={}, calibration=None, updated=0,
                    name='未分配', firmware='未读取')

    @property
    def rows(self):
        return copy.deepcopy(self._rows)

    def row(self, node):
        return next((r for r in self.rows if r['node'] == node), None)

    def _put(self, row):
        rows = list(self._rows)
        index = next((i for i, r in enumerate(rows) if r['node'] == row['node']), None)
        if index is None:
            index = next((i for i, r in enumerate(rows) if r['node'] is None), None)
        if index is not None:
            rows[index] = row
            self._rows = rows

    def _configuration(self, out):
        cfg, _ = self.tool.load_device_json()
        item, _ = self.tool.find_can_bus(cfg, self.tool.LOGICAL_BUS)
        context = (item.get('iface'), item.get('bitrate'))
        if not context[0]:
            raise ValueError('配置中接口为空；拒绝打开总线')
        if self.context is not None and context != self.context:
            raise ValueError('总线配置已变化；须退出并重新核对，禁止自动重连')
        actual = self.tool.read_iface_actual(context[0])
        rt = self.tool.service_active(self.tool.RT_SERVICE)
        stats = self.tool.parse_ip_stats(context[0])
        xstats = self.tool.parse_can_xstats(stats.get('raw', ''))
        self.bus_status = '{}  {} bps  {}  {}  RT占用 {}'.format(context[0], context[1],
            (actual or {}).get('state', '未知'), (actual or {}).get('can_state', '未知'),
            '是' if rt else '否' if rt is False else '未知')
        self.bus_status += '  错误RX/TX {}/{} bus-off {}'.format(stats.get('rx_errors', '未知'),
            stats.get('tx_errors', '未知'), xstats.get('bus-off', '未知'))
        if not self.rt_ack:
            raise ValueError('启动前须显式确认 RT 总线占用风险')
        a = copy.copy(self.args)
        a.i_know_rt_is_running = self.rt_ack
        rt_out = out if not self._rt_seen or rt != self._rt_state else SilentOutput()
        self._rt_seen, self._rt_state = True, rt
        if not self.tool.require_rt_ack(a, rt_out, '测试台 SDO'):
            raise ValueError('RT 闸门拒绝')
        if (not actual or actual.get('error') or actual.get('state') != 'UP'
                or actual.get('bitrate') != context[1] or actual.get('can_state') != 'ERROR-ACTIVE'):
            raise ValueError('实际接口状态/位速率不匹配，拒绝打开总线')
        if not self.tool.require_std_layer('PMP4010 测试台'):
            raise ValueError('缺少标准 CANopen 层')
        if self.od is None:
            self.od = self.tool.load_od()
            self.specs = position.specifications(self.od)
            self.objects = {}
            for entry in self.od['entries']:
                if entry.get('type') in TYPES and entry.get('index') and entry.get('status') == 'verified' and entry.get('cite'):
                    size, signed = TYPES[entry['type']]
                    self.objects[entry['key']] = dict(index_value=int(entry['index'], 0), sub=entry['sub'], size=size, signed=signed)
            self.objects.update({k: v for k, v in self.specs.items() if k != 'pp_protocol'})
        elif self.tool.load_od() != self.od:
            raise ValueError('OD 已变化；须退出重新核对')
        self.context = context
        if self.bus is None:
            self.bus = self.tool._std_open_active(context[0])

    def _read(self, client, key, session=None):
        spec = self.objects[key]
        if session is None:
            raw = client.read(spec['index_value'], spec['sub'])
        else:
            raw = session.call(session.deadline(), '读取 ' + key, client.read, spec['index_value'], spec['sub'])
        if len(raw) != spec['size']:
            raise ValueError(key + ' 读回宽度不符')
        return int.from_bytes(raw, 'little', signed=spec['signed'])

    def _latch(self, client, node, scope, exc, out):
        if getattr(client, 'quarantined', False):
            reason = '{} SDO 已隔离：{}；需外部恢复'.format(scope, exc)
            self.blocked[(self.context[0], node)] = reason
            row = self.row(node)
            if row:
                row.update(lost=True, quarantined=True, quarantine_reason=reason)
                self._put(row)
            out.bad('节点 {} {}'.format(node, reason))

    def _allowed(self, node):
        reason = self.blocked.get((self.context[0], node))
        if reason:
            raise ValueError('节点 {} 已隔离；需外部恢复；{}'.format(node, reason))

    def scan(self, out):
        with self.lock:
            self._configuration(out)
            lo, hi = self.tool.parse_node_range(self.args.node_range) if self.args.node_range else (1, 127)
            found = []
            for node in range(lo, hi + 1):
                out.check_cancel()
                if (self.context[0], node) in self.blocked:
                    continue
                client = self.scan_clients.setdefault(node, self.tool._canopen.SdoClient(self.bus, node, timeout=self.args.timeout))
                try:
                    client.read_device_type()  # Same SDO identity primitive as standard scan_nodes; no heartbeat/NMT.
                    found.append(node)
                    row = self.row(node) or self._empty(node)
                    row.update(online=True, lost=False)
                    self._put(row)
                except Exception as exc:
                    self._latch(client, node, '扫描', exc, out)
                    old = self.row(node)
                    if old:
                        old['lost'] = True
                        self._put(old)
            out.info('扫描完成：发现 {} 台（节点 {}）；面板固定四席'.format(len(found), ','.join(map(str, found)) or '无'))
            if len(found) > 4:
                out.warn('发现超过四台；仅显示先发现的四台，请缩小扫描范围后重启')
            self._refresh_locked(out)
            return found

    def _refresh_locked(self, out):
        for row in self.rows:
            node = row['node']
            if node is None or (self.context[0], node) in self.blocked:
                continue
            out.check_cancel()
            client = self.refresh_clients.setdefault(node, self.tool._canopen.SdoClient(self.bus, node, timeout=self.args.timeout))
            try:
                # Commit a whole snapshot atomically; failures preserve previous values.
                values = {key: self._read(client, key) for key in TELEMETRY}
                cal = position.calibration([self._read(client, key) for key in position.CALIBRATION])
                if row['name'] == '未分配':
                    row['name'] = client.read_manufacturer_name()
                    row['firmware'] = client.read_software_version()
                row.update(values=values, calibration=cal, updated=time.monotonic(),
                           online=True, lost=False, quarantined=False)
                self._put(row)
            except Exception as exc:
                row['lost'] = True
                self._put(row)
                self._latch(client, node, '刷新', exc, out)
                out.warn('节点 {} 刷新失败；保留旧值：{}'.format(node, exc))

    def refresh(self, out):
        if not self.lock.acquire(False):
            return False
        try:
            self._configuration(out)
            self._refresh_locked(out)
            return True
        finally:
            self.lock.release()

    def _fresh(self, node):
        if self.context is None:
            raise ValueError('请先扫描并取得实时快照')
        self._allowed(node)
        row = self.row(node)
        if (not row or not row['online'] or row['lost'] or not row['calibration']
                or time.monotonic() - row['updated'] > self.freshness):
            raise ValueError('实时快照过期或失联；请刷新后重新审阅')
        return row

    def _args(self, row, target):
        a = copy.copy(self.args)
        a.node, a.target = row['node'], target
        for key, value in self.settings.items():
            setattr(a, key, value)
        for key, obj in [('velocity', 'profile_velocity'), ('acceleration', 'profile_acceleration'), ('deceleration', 'profile_deceleration')]:
            setattr(a, key, row['values'].get(obj))
        a.apply, a.dry_run, a.position, a.calibrate = True, False, True, False
        a.offline_calibration = a.offline_limits = a.start_position = None
        a.i_know_rt_is_running = self.rt_ack
        a.od_gaps_bypass = self.od_reason
        a.motion_log = self.args.motion_log or str(Path.cwd() / ('pmp4010-motion-' + uuid.uuid4().hex + '.jsonl'))
        return a

    def _stationary(self, values, reached=True):
        p = self.tool._canopen
        if values['actual_velocity'] != 0:
            raise ValueError('实际速度非零，拒绝操作')
        if reached and not values['statusword'] & p.CIA402_HOMING_TARGET_REACHED:
            raise ValueError('目标到达标志未置位（target reached/bit10）；当前实际速度为零')
        if values['statusword'] & p.CIA402_FAULT:
            raise ValueError('存在故障；测试台不自动复位')

    def review(self, action, node, value, out, check_gate=True):
        if check_gate:
            self.discard_prepare()
        if action not in ACTIONS or action_policy(action)['batch']:
            raise ValueError('不支持的动作；非零速度运动未开放')
        row = self._fresh(node)
        start = row['values']['actual_position']
        target = start + int(value) if action == 'relative' else int(value) if action == 'absolute' else 0 if action == 'zero' else start
        a = self._args(row, start if action == 'zero' else target)
        p = self.tool._canopen
        disabled_start = action == 'probe_pp_start'
        if disabled_start:
            # Private provenance remains in a shallow dict copy as well as in
            # the Review subtype. It is never a caller-controlled bypass flag.
            a._disabled_start_origin = True
            a.velocity = min(row['values']['profile_velocity'], 100)
        if action == 'prepare_pp':
            a.velocity = 100
        if check_gate:
            if not self.tool.refuse_motion(out, ACTIONS[action], a):
                raise ValueError('OD 闸门关闭：默认只读，须显式会话确认')
        elif not self.od_reason and any(e['status'] != 'verified' for e in self.od['entries']):
            raise ValueError('OD 闸门关闭：默认只读，须显式会话确认')
        if action_policy(action)['settings']:
            # These common guards also apply to zero/enable, without inventing
            # a nonzero PP motion merely to invoke check_start().
            position.validate(a, self.specs)
            position.check_envelope(a, start, start)
            position.check_budget(a, row['calibration'])
            self._stationary(row['values'], reached=action in ('relative', 'absolute', 'zero') and not disabled_start)
            if action in ('absolute', 'relative'):
                position.check_start(a, start, row['calibration'])
            if action == 'enable':
                mode = row['values']['modes_of_operation_display']
                if mode not in (self.specs['pp_protocol']['mode'], 3, 9):
                    raise ValueError('测试台只允许 PP 或预写零速度后的 PV/CSV 模式使能')
                if mode in (3, 9):
                    position.require_velocity_library()
            if action == 'zero' and row['values']['home_offset'] != 0:
                raise ValueError('607Ch 非零；设零点拒绝，须人工核对原点偏移')
        if action == 'prepare_pp':
            self._prepare_values(row['values'], a, row['calibration'])
        if disabled_start:
            self._disabled_start_values(row['values'], a, row['calibration'])
        writes = []
        def planned(key, value, note=''):
            spec = self.objects[key]
            writes.append(dict(key=key, index=spec['index_value'], sub=spec['sub'],
                type=('int' if spec['signed'] else 'uint') + str(spec['size']*8), value=value, note=note))
        if action in ('absolute', 'relative', 'probe_pp_start'):
            if disabled_start:
                planned('target_position', start, '只对齐当前位置；保持Halt核证，不写位移目标')
            for key, value in [('profile_end_velocity', 0), ('profile_velocity', a.velocity),
                               ('profile_acceleration', a.acceleration), ('profile_deceleration', a.deceleration),
                               *([] if disabled_start else [('target_position', a.target)])]:
                planned(key, value, '用户单位' if key == 'target_position' else '')
        elif action == 'prepare_pp':
            planned('target_position', start, '仅对齐本次实读起点；不触发轨迹')
            planned('profile_velocity', 100, '固定100用户单位/s；仅降速')
        else:
            if action == 'enable' and row['values']['modes_of_operation_display'] in (3, 9):
                planned('target_velocity', 0, '先预写零速度并读回，再执行使能序列')
            planned('controlword', {'zero': self.tool._canopen.CIA402_START_HOMING,
                'enable': self.tool._canopen.CIA402_ENABLE, 'disable': self.tool._canopen.CIA402_DISABLE,
                'stop': self.tool._canopen.CIA402_QUICK_STOP_COMMAND}[action], '触发回零' if action == 'zero' else '')
        degrees = Fraction(row['calibration']['motor_degrees_per_user_unit'])
        delta = 0 if action == 'zero' else target - start
        lines = ['接口 {}  节点 {}  动作 {}'.format(self.context[0], node, ACTIONS[action]),
            '目标 {} U = {:.3f}度 电机轴；相对当前位置 {:+d} U / {:+.3f}度'.format(target, float(target * degrees), delta, float(delta * degrees))]
        if a.max_displacement is not None:
            lines.append('单次位移上限 {} U (硬顶1度)；本次 {} U，剩余 {} U'.format(a.max_displacement, abs(delta), a.max_displacement - abs(delta)))
        if action == 'zero':
            lines[1] = '坐标目标0 U；机械目标为当前位置（物理位移预算0 U），非移动到旧坐标0。'
            lines += ['方法35：当前位置为机械原点；不写607Ch，不配置DI。',
                      'DI需求未验证；完成须新bit10+bit12与6064=0读回；不使用bit15。',
                      '归零是坐标重置；完成后必须重新输入脚本行程与位移预算。']
        if action in ('absolute', 'relative'):
            lines.append('PP速度/加减速度取设备实读：{}/{}/{}；日志 {}'.format(a.velocity, a.acceleration, a.deceleration, a.motion_log))
        if disabled_start:
            lines += ['未使能起步核证：保持Halt；不解除暂停；结束失能。计划位移0，不代表真实位移验收。',
                      '执行前5次新鲜采样速度0、位置不变，间隔0.2秒；启动bit10不作静止判据。',
                      '6081 {} → {} U/s（现值与100取较小正值）；保留正预算、1度、脚本及设备行程。'.format(
                          row['values']['profile_velocity'], a.velocity),
                      '先607A对齐起点并读回；0106→0107→0127→0137→0127，所有业务控制字bit3=0、Halt=1。',
                      '仅至Switched on；固件能力核证可能拒绝。失败不升级到010F/012F/013F，不接受任何位置漂移。',
                      '须新ACK 0→1→0、607A=6062=起点、位置/零速/Halt保持5次采样；Halt中bit10仅表示零速。',
                      '不写605Dh；要求实读1或2。写607A不保证清旧轨迹；核证失败立即有界快停/失能，不重试。',
                      '日志 ' + a.motion_log]
        if action == 'enable' and row['values']['modes_of_operation_display'] in (3, 9):
            lines.append('PV/CSV 仅按本次审阅显式准备 60FFh=0；不执行非零速度运动。')
        if action == 'prepare_pp':
            v = row['values']
            rpm = Fraction(100) * Fraction(row['calibration']['gear_ratio']) * 60 / Fraction(row['calibration']['resolution'])
            reached = bool(v['statusword'] & self.tool._canopen.CIA402_HOMING_TARGET_REACHED)
            lines += ['失能准备：607A {} → {} U；6081 {} → 100 U/s（{:.6f} rpm轮廓值）'.format(
                          v['target_position'], start, v['profile_velocity'], float(rpm)),
                      '实际速度0（约0 rpm），计划位移0 U；执行前5次采样，间隔0.2秒。',
                      '保持失能；不发控制字/NMT，不触发轨迹；target reached当前={}。'.format(reached),
                      '准备完成不代表可运动，也不保证bit10置位；运动须重新静止检查和两段确认。',
                      '日志 ' + a.motion_log]
        lines += [write_text(item) for item in writes]
        token = '{} {} {} {}'.format(action.upper(), self.context[0], node, target)
        review = dict(action=action, node=node, target=target, start=start,
                    context=self.context, args=a, lines=lines, token=token,
                    od=copy.deepcopy(self.od), calibration=row['calibration'], writes=writes,
                    mode=row['values']['modes_of_operation_display'])
        if action == 'prepare_pp':
            review = PrepareReview(review)
            review['prepare_before'] = copy.deepcopy(row['values'])
            review['settings'] = copy.deepcopy(self.settings)
            if check_gate:
                self.freeze_prepare(review)
        elif disabled_start:
            review = DisabledStartReview(review)
            review['disabled_start_before'] = copy.deepcopy(row['values'])
            review['settings'] = copy.deepcopy(self.settings)
            if check_gate:
                self.freeze_prepare(review)
        return review

    def discard_prepare(self):
        self._prepare_review = None
        self._disabled_start_review = None

    def freeze_prepare(self, review):
        # Preview is pure cached rendering. Only F5 (or an explicit model review)
        # registers a single-use, complete copy; execution never trusts edits.
        self.discard_prepare()
        if isinstance(review, DisabledStartReview):
            review['disabled_start_id'] = uuid.uuid4().hex
            self._disabled_start_review = (review, copy.deepcopy(review))
        elif review['action'] == 'prepare_pp':
            review['prepare_id'] = uuid.uuid4().hex
            self._prepare_review = (review, copy.deepcopy(review))

    def _disabled_start_values(self, values, args, cal):
        # The sole exception to startup bit10 applies to this sealed PP action.
        # It is not used by zero, ordinary enabled starts, or the CLI.
        p = self.tool._canopen
        self._stationary(values, reached=False)
        if values['statusword'] & p.CIA402_PP_FOLLOWING_ERROR:
            raise ValueError('PP跟随误差；拒绝严格失能起步')
        if values['statusword'] & p.CIA402_DISABLED_MASK != p.CIA402_DISABLED:
            raise ValueError('起步核证要求严格disabled')
        if values['control_mode_select'] != 0 or values['modes_of_operation'] != 1 or values['modes_of_operation_display'] != 1:
            raise ValueError('起步核证要求CiA402控制、6060/6061均为PP1')
        if values['home_offset'] != 0:
            raise ValueError('607Ch非零；拒绝起步核证')
        if values['controlword'] & 0x100:
            raise ValueError('起步核证要求初始6040h bit8=0，以建立新Halt边沿')
        if values['halt_option'] not in (1, 2):
            raise ValueError('605Dh暂停方式必须实读为1或2')
        position.validate(args, self.specs)
        position.check_envelope(args, args.target, values['actual_position'])
        position.check_budget(args, cal)
        if args.velocity != min(values['profile_velocity'], 100) or args.velocity <= 0:
            raise ValueError('起步核证6081必须是现值与100中较小正值，不得升速')
        if (args.velocity > values['max_velocity'] or position.unit_to_velocity(args.velocity, cal) > values['max_motor_speed']
                or args.acceleration > values['max_profile_acceleration'] or args.deceleration > values['max_deceleration']):
            raise ValueError('起步核证轮廓超过设备607F/6080/60C5/60C6上限')
        low, high = values['software_position_limit'], values['software_position_limit_max']
        if not low <= values['actual_position'] <= high or not low <= args.target <= high:
            raise ValueError('起点/目标超出设备607Dh实读行程，或设备上下限倒置')

    def _disabled_start_guard(self, review, args, client, out, journal):
        """Re-read the sealed setup before *each* pretrigger transition.

        State/position/velocity reads are observations, not a guarantee that an
        old trajectory cannot run when enabled. The execution strategy must
        separately establish that its controlword sequence is acceptable.
        """
        expected = copy.deepcopy(review['disabled_start_before'])
        p = self.tool._canopen
        fixed = ('control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
                 'home_offset', 'profile_velocity', 'profile_end_velocity', 'target_position',
                 'profile_acceleration', 'profile_deceleration', 'velocity_feedback_unit',
                 'software_position_limit', 'software_position_limit_max') + position.LIMITS
        fixed += ('controlword', 'halt_option')

        def guard(session, phase, states=None, updates=None):
            states = (p.CIA402_DISABLED,) if states is None else tuple(states)
            out.check_cancel()
            self._configuration(out)
            self._allowed(review['node'])
            if (self.context != review['context'] or self.od != review['od']
                    or self.settings != review['settings'] or self.od_reason != args.od_gaps_bypass):
                raise ValueError('严格失能起步接口/OD/设置/确认理由已变化')
            if updates:
                expected.update(updates)

            def state_ok(sw):
                # Validate immediately: a later normal status must never hide
                # an earlier fault or following-error observation.
                if sw & p.CIA402_MASK == p.CIA402_ENABLED:
                    session._probe_operation_enabled_observed = True
                    session.needs_stop = True
                    raise ValueError('未使能起步核证意外观察到Operation enabled；立即收尾')
                if sw & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR):
                    raise ValueError('严格失能起步检测到故障或PP跟随误差')
                if getattr(session, '_probe_require_ack_zero', False) and sw & p.CIA402_PP_SETPOINT_ACK:
                    raise ValueError('当前位置新命令前ACK提前置位；拒绝复用陈旧ACK')
                allowed = any((sw & p.CIA402_DISABLED_MASK == state if state == p.CIA402_DISABLED
                               else sw & p.CIA402_MASK == state) for state in states)
                if not allowed:
                    raise ValueError('严格失能起步状态迁移不符: 0x{:04X}'.format(sw))

            first = self._read(client, 'statusword', session)
            state_ok(first)
            current = {'statusword': first}
            current.update({k: self._read(client, k, session) for k in TELEMETRY if k != 'statusword'})
            cal = position.calibration([self._read(client, k, session) for k in position.CALIBRATION])

            def values_ok():
                self._stationary(current, reached=False)
                state_ok(current['statusword'])
                if current['actual_position'] != review['start']:
                    raise ValueError('严格失能起步位置漂移；拒绝继续')
                if cal != review['calibration']:
                    raise ValueError('严格失能起步标定已变化')
                if any(current[k] != expected[k] for k in fixed):
                    raise ValueError('严格失能起步模式/原点偏移/轮廓/设备上限已变化')
                position.validate(args, self.specs)
                position.check_budget(args, cal)
                position.check_envelope(args, review['start'], current['actual_position'])
                low, high = current['software_position_limit'], current['software_position_limit_max']
                if not low <= current['actual_position'] <= high or not low <= args.target <= high:
                    raise ValueError('严格失能起步起点/目标不在设备607Dh实读行程内')

            values_ok()
            # Bracket the longer settings/calibration reads. Validate every
            # fresh critical value before replacing its earlier observation.
            for key in ('statusword', 'actual_position', 'actual_velocity', 'control_mode_select',
                        'modes_of_operation', 'modes_of_operation_display', 'home_offset',
                        'controlword', 'target_position', 'position_demand', 'halt_option'):
                value = self._read(client, key, session)
                if key == 'statusword':
                    state_ok(value)
                elif value != current[key]:
                    raise ValueError('严格失能起步采样期间关键值变化: ' + key)
                current[key] = value
            values_ok()
            out.check_cancel()
            journal.event('disabled_start_sample', phase=phase, values=current, calibration=cal)
            out.check_cancel()
            return current

        return guard

    @staticmethod
    def _disabled_start_samples(session, guard, overall=None):
        for i in range(5):
            if i:
                deadline = time.monotonic() + .2
                if overall is not None and deadline > overall:
                    raise ValueError('严格失能起步五采样整体时限不足')
                while time.monotonic() < deadline:
                    session.pause(deadline)
            guard(session, '稳定采样{}/5'.format(i + 1))

    def _claim_disabled_start(self, review):
        sealed = self._disabled_start_review
        self._disabled_start_review = None
        if sealed is None or review is not sealed[0] or review != sealed[1]:
            raise ValueError('严格失能起步审阅已失效、复用或被修改；请重新审阅')
        return sealed[1]

    def _prepare_values(self, values, args, cal):
        p = self.tool._canopen
        self._stationary(values, reached=False)  # Exception belongs only to disabled preparation.
        if values['statusword'] & p.CIA402_DISABLED_MASK != p.CIA402_DISABLED:
            raise ValueError('失能准备要求严格disabled；当前未确认失能')
        if values['control_mode_select'] != 0:
            raise ValueError('失能准备要求CiA402控制模式')
        if values['modes_of_operation'] != 1 or values['modes_of_operation_display'] != 1:
            raise ValueError('失能准备要求6060/6061均为PP模式1')
        if values['home_offset'] != 0:
            raise ValueError('607Ch非零；拒绝失能准备')
        if values['profile_velocity'] < 100:
            raise ValueError('原6081小于100；失能准备不得擅自升速')
        position.validate(args, self.specs)
        position.check_envelope(args, args.target, values['actual_position'])
        position.check_budget(args, cal)
        rpm = Fraction(100) * Fraction(cal['gear_ratio']) * 60 / Fraction(cal['resolution'])
        if (100 > values['max_velocity'] or rpm > values['max_motor_speed']
                or args.acceleration > values['max_profile_acceleration']
                or args.deceleration > values['max_deceleration']):
            raise ValueError('失能准备超过设备607F/6080/60C5/60C6上限')

    def _execute_prepare(self, review, out):
        # Caller owns the bus lock. No enable/stop/disable cleanup is appropriate
        # for this two-object disabled transaction, even after a partial write.
        sealed = self._prepare_review
        self._prepare_review = None
        if sealed is None or review is not sealed[0] or review != sealed[1]:
            raise ValueError('失能准备审阅已失效、复用或被修改；请重新审阅')
        review = sealed[1]
        node, a = review['node'], review['args']
        self._configuration(out)
        self._allowed(node)
        if (self.context != review['context'] or self.od != review['od']
                or self.settings != review['settings'] or self.od_reason != a.od_gaps_bypass):
            raise ValueError('失能准备接口/OD/设置在审阅后已变化')
        if not self.tool.refuse_motion(out, ACTIONS['prepare_pp'], a):
            raise ValueError('OD闸门拒绝失能准备')
        # Reconstruct the complete narrow plan from sealed start and fixed speed.
        # Never interpret user input or a review writes list as arbitrary writes.
        plan = [('target_position', review['start']), ('profile_velocity', 100)]
        expected = copy.deepcopy(review['prepare_before'])
        client = self.tool._canopen.SdoClient(self.bus, node, timeout=self.args.timeout)
        session = self.tool.MotionSession(client, out, a.state_timeout)
        session.cancel_event = out.cancel_event
        journal = None
        attempts = []
        last = None
        success = False
        def sample(phase):
            nonlocal last
            out.check_cancel()
            self._configuration(out)
            self._allowed(node)
            current = {k: self._read(client, k, session) for k in TELEMETRY}
            cal = position.calibration([self._read(client, k, session) for k in position.CALIBRATION])
            # Validate the first observed state before a later read can replace it.
            self._prepare_values(current, a, cal)
            # Bracket the longer configuration reads with fresh critical state.
            for k in ('control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
                      'home_offset', 'actual_velocity', 'actual_position', 'statusword'):
                again = self._read(client, k, session)
                changed = again ^ current[k] if k == 'statusword' else again != current[k]
                if k == 'statusword':
                    changed &= ~self.tool._canopen.CIA402_HOMING_TARGET_REACHED
                if changed:
                    raise ValueError('失能准备采样期间状态变化；请重新审阅')
                current[k] = again
            last = current
            journal.event('prepare_sample', phase=phase, values=current, calibration=cal)
            self._prepare_values(current, a, cal)
            if current['actual_position'] != review['start']:
                raise ValueError('失能准备位置漂移；请重新审阅')
            if cal != review['calibration']:
                raise ValueError('失能准备标定已变化；请重新审阅')
            keys = ('control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
                    'home_offset', 'profile_velocity', 'target_position', 'profile_acceleration',
                    'profile_deceleration', 'velocity_feedback_unit') + position.LIMITS
            if any(current[k] != expected[k] for k in keys):
                raise ValueError('失能准备关键设置已变化；请重新审阅')
            if self.settings != review['settings'] or self.od_reason != a.od_gaps_bypass:
                raise ValueError('失能准备人工设置或OD确认已变化')
            return current
        try:
            journal = position.Journal(a.motion_log, 'device', dict(action='prepare_pp', node=node,
                reviewed_start=review['start'], planned_displacement=0,
                settings=review['settings'], od_gaps_bypass=a.od_gaps_bypass), observer=out.event)
            for i in range(5):
                if i:
                    deadline = time.monotonic() + .2
                    while time.monotonic() < deadline:
                        session.pause(deadline)
                sample('稳定采样{}/5'.format(i + 1))
            for key, value in plan:
                sample('写前' + key)
                spec = self.specs[key]
                attempt = dict(object=key, before=expected[key], expected=value,
                               write_attempted=True, readback=None)
                attempts.append(attempt)
                journal.event('prepare_write_attempt', **attempt)
                session.call(session.deadline(), '失能准备写入 ' + key, client.write,
                    spec['index_value'], spec['sub'], position.encode(spec, value))
                actual = self._read(client, key, session)
                attempt['readback'] = actual
                journal.event('write_readback', **attempt)
                out.info('失能准备 {}：{} → {}；读回 {}'.format(key, attempt['before'], value, actual))
                if actual != value:
                    raise ValueError('失能准备 ' + key + ' 写读回不一致；停止，保留部分写入证据')
                expected[key] = value
                sample('写后' + key)
            success = True
            reached = bool(last['statusword'] & self.tool._canopen.CIA402_HOMING_TARGET_REACHED)
            out.info('失能准备完成：仅两处写读回，仍失能，实际速度0、位移0 U；target reached={}。须重新运动审阅与两段确认。'.format(reached))
        except BaseException as exc:
            self._latch(client, node, '失能准备', exc, out)
            last = None  # A pre-write snapshot cannot prove the final state.
            if journal:
                journal.event('prepare_exception', message=str(exc), attempts=attempts,
                              quarantined=bool(getattr(client, 'quarantined', False)))
            out.bad('失能准备失败；保留已写证据，不回滚、不重试、不发控制字：' + str(exc))
            raise
        finally:
            row = self.row(node)
            if row:
                row.update(updated=0, lost=True)
                self._put(row)
            result = dict(action='prepare_pp', node=node, success=success, attempts=attempts,
                readback=last, disabled_confirmed=True if success else None,
                target_reached=bool(last['statusword'] & self.tool._canopen.CIA402_HOMING_TARGET_REACHED) if last else None,
                mechanical_stop_confirmed=False, state_unknown=not success)
            if journal:
                try:
                    journal.event('bench_result', **result)
                finally:
                    journal.close()
            else:
                out.event('bench_result', result)
        return 0

    def execute(self, review, out):
        with self.lock:
            disabled_start = (isinstance(review, DisabledStartReview)
                or review.get('action') == 'probe_pp_start'
                or 'disabled_start_id' in review or 'disabled_start_before' in review
                or getattr(review.get('args'), '_disabled_start_origin', False))
            if disabled_start:
                review = self._claim_disabled_start(review)
            if (isinstance(review, PrepareReview) or review.get('action') == 'prepare_pp'
                    or 'prepare_id' in review or 'prepare_before' in review
                    or self._prepare_review is not None and review is self._prepare_review[0]):
                return self._execute_prepare(review, out)
            self._configuration(out)
            node, action, a = review['node'], review['action'], copy.copy(review['args'])
            if self.context != review['context'] or self.od != review['od']:
                raise ValueError('接口或 OD 在审阅后已变化；请重新审阅')
            if disabled_start and (self.settings != review['settings'] or self.od_reason != a.od_gaps_bypass):
                raise ValueError('严格失能起步设置或OD确认在审阅后已变化')
            self._allowed(node)
            if not self.tool.refuse_motion(out, ACTIONS[action], a):
                raise ValueError('OD 闸门拒绝')
            client = self.tool._canopen.SdoClient(self.bus, node, timeout=self.args.timeout)
            controller = self
            class BenchSession(self.tool.MotionSession):
                def enable(self):
                    controller.enabled_sessions[node] = self
                    if self.status(self.deadline()) & controller.tool._canopen.CIA402_FAULT:
                        raise ValueError('存在故障；测试台不自动复位')
                    return super().enable()
                def control(self, word, deadline):
                    # A new fault must never enter the shared fault-reset branch.
                    if word == controller.tool._canopen.CIA402_FAULT_RESET:
                        raise ValueError('测试台禁止自动故障复位')
                    if word not in (controller.tool._canopen.CIA402_DISABLE, controller.tool._canopen.CIA402_QUICK_STOP_COMMAND):
                        controller.enabled_sessions[node] = self
                    return super().control(word, deadline)
            session = BenchSession(client, out, a.state_timeout)
            session.cancel_event = out.cancel_event
            journal = None
            error = None
            values = None
            disabled = None
            position_unchanged = zero_velocity_confirmed = None
            unexpected_enabled_after_cleanup = False
            zero_attempted = False
            try:
                if action_policy(action)['settings'] and not disabled_start:
                    def guard(s, phase):
                        current = {k: self._read(client, k, s) for k in ('statusword', 'actual_velocity', 'actual_position')}
                        self._stationary(current, reached=phase == 'before_setup' and action in ('absolute', 'relative', 'zero'))
                        if current['actual_position'] != review['start']:
                            raise ValueError('审阅后起点已变化；拒绝执行，请重新审阅')
                        position.check_envelope(a, review['start'], current['actual_position'])
                    guard(session, 'before_setup')
                    cal = position.calibration([self._read(client, k, session) for k in position.CALIBRATION])
                    if cal != review['calibration']:
                        raise ValueError('审阅后标定已变化；拒绝执行')
                if disabled_start:
                    a._bench_expected_start = review['start']
                    journal = position.Journal(a.motion_log, 'device',
                        dict(node=node, action=action, reviewed_start=review['start'],
                             planned_displacement=0, halt_released=False, settings=review['settings'],
                             od_gaps_bypass=a.od_gaps_bypass), observer=out.event)
                    probe_guard = self._disabled_start_guard(review, a, client, out, journal)
                    position.run_disabled_start_probe(session, a, self.specs, journal, probe_guard)
                    out.info('未使能起步核证取得Switched on当前位置新ACK与Halt保持证据；已收尾失能，不代表真实位移验收。')
                elif action in ('absolute', 'relative'):
                    # The reviewed integers are the execution inputs, including
                    # a relative action's already-resolved absolute coordinate.
                    plan = {item['key']: item['value'] for item in review['writes']}
                    a.target = plan['target_position']
                    if a.target != review['target']:
                        raise ValueError('写入计划与审阅目标不一致')
                    a.velocity, a.acceleration, a.deceleration = (plan[k] for k in
                        ('profile_velocity', 'profile_acceleration', 'profile_deceleration'))
                    a._bench_expected_start = review['start']
                    a._bench_guard = guard
                    journal = position.Journal(a.motion_log, 'device',
                        dict(node=node, target=a.target, action=action, reviewed_start=review['start'],
                             soft_min=a.soft_min, soft_max=a.soft_max, max_displacement=a.max_displacement,
                             od_gaps_bypass=a.od_gaps_bypass), observer=out.event)
                    position.run(session, a, self.specs, journal)
                elif action == 'zero':
                    if self._read(client, 'home_offset', session) != 0:
                        raise ValueError('607Ch 非零；拒绝设零点')
                    out.warn('方法35的 DI 配置需求未验证；不写 DI/607Ch')
                    zero_attempted = True
                    session.needs_stop = True
                    session.home(35, a.home_timeout)
                    if self._read(client, 'actual_position', session) != 0:
                        raise ValueError('回零完成位已到，但6064不为0；结果未确认')
                    out.info('方法35完成：新bit10+bit12；6064=0；bit15仅为手册散见说法，未作判据')
                else:
                    if action == 'enable':
                        mode = self._read(client, 'modes_of_operation_display', session)
                        if mode != review['mode'] or mode not in (self.specs['pp_protocol']['mode'], 3, 9):
                            raise ValueError('使能模式在审阅后已变化；请重新审阅')
                        if mode in (3, 9):
                            targets = [item for item in review['writes'] if item['key'] == 'target_velocity']
                            if len(targets) != 1 or targets[0]['value'] != 0:
                                raise ValueError('速度模式使能必须审阅并预写 60FFh=0')
                            session.set_target_velocity(0)
                            cal_now = position.calibration([self._read(client, k, session) for k in position.CALIBRATION])
                            if cal_now != review['calibration']:
                                raise ValueError('速度准备后标定与审阅不符；请重新审阅')
                        session.needs_stop = True
                    # An interrupted stop/disable also owes bounded cleanup.
                    session.needs_stop = True
                    getattr(session, {'enable': 'enable', 'disable': 'disable', 'stop': 'stop'}[action])()
            except BaseException as exc:
                error = exc
                self._latch(client, node, '运动', exc, out)
            finally:
                session.cancel_event = None
                if (error is not None or action == 'zero') and session.needs_stop and not disabled_start:
                    disabled = session.stop_after_failure()
                    if disabled is False and error is None:
                        error = ValueError('失能未确认；状态未知，须独立急停')
                if zero_attempted:
                    self.settings = dict(soft_min=None, soft_max=None, max_displacement=None)
                if journal:
                    journal.close()
                try:
                    values = {}
                    for key in ('statusword', 'actual_position', 'actual_velocity', 'modes_of_operation_display'):
                        values[key] = self._read(client, key, session)
                        if disabled_start:
                            if key == 'statusword':
                                sw = values[key]
                                disabled = sw & self.tool._canopen.CIA402_DISABLED_MASK == self.tool._canopen.CIA402_DISABLED
                                if sw & self.tool._canopen.CIA402_MASK == self.tool._canopen.CIA402_ENABLED:
                                    unexpected_enabled_after_cleanup = True
                                    session._probe_operation_enabled_observed = True
                                    session.needs_stop = True
                                    raise ValueError('未使能核证最终读回意外Operation enabled；立即收尾')
                                if sw & (self.tool._canopen.CIA402_FAULT | self.tool._canopen.CIA402_PP_FOLLOWING_ERROR):
                                    raise ValueError('起步核证最终读回出现故障或PP跟随误差；不得报告成功')
                            elif key == 'actual_position':
                                position_unchanged = values[key] == review['start']
                            elif key == 'actual_velocity':
                                zero_velocity_confirmed = values[key] == 0
                    sw = values['statusword']
                    disabled = sw & self.tool._canopen.CIA402_DISABLED_MASK == self.tool._canopen.CIA402_DISABLED
                    if action != 'zero' and all(getattr(a, k, None) is not None for k in ('soft_min', 'soft_max', 'max_displacement')):
                        position.check_envelope(a, review['start'], values['actual_position'])
                    if action in ('zero', 'absolute', 'relative', 'disable', 'probe_pp_start') and not disabled:
                        session.needs_stop = True
                        raise ValueError('操作后未确认失能；状态未知')
                    if action == 'zero' and values['actual_position'] != 0:
                        raise ValueError('清理后6064已偏离0；设零点未确认')
                    if disabled_start and (values['actual_position'] != review['start'] or values['actual_velocity'] != 0):
                        raise ValueError('起步核证结果读回不是起点/零速；不得报告成功')
                    out.info('结果读回：节点 {} 位置 {} U 状态 0x{:04X}；机械停止状态未知'.format(node, values['actual_position'], sw))
                except BaseException as exc:
                    self._latch(client, node, '结果读回', exc, out)
                    error = error or exc
                    out.bad('结果读回或包络检查失败；状态未知：{}'.format(exc))
                    # Postflight errors carry exactly the same cleanup obligation
                    # as an interrupted write. In particular E must not be left
                    # enabled when its final envelope/readback check fails.
                    if session.needs_stop and not disabled_start:
                        disabled = session.stop_after_failure()
                    elif (disabled_start and unexpected_enabled_after_cleanup
                          and getattr(session, '_probe_cleanup_confirmed', False)
                          and not getattr(session, '_probe_extra_stop_used', False)
                          and not getattr(client, 'quarantined', False)):
                        # A newly observed enabled state after a confirmed
                        # cleanup is a new stop obligation, never a retry of a
                        # failed/uncertain cleanup or a replacement client.
                        session._probe_extra_stop_used = True
                        disabled = session.stop_after_failure()
                        position_unchanged = zero_velocity_confirmed = None
                    values = None
                row = self.row(node)
                if row:
                    row.update(updated=0, lost=True)
                    self._put(row)
                result = dict(node=node, success=error is None, readback=values,
                    disabled_confirmed=disabled, mechanical_stop_confirmed=False,
                    state_unknown=values is None or disabled is False)
                if disabled_start:
                    result.update(action=action, position_unchanged=position_unchanged,
                                  zero_velocity_confirmed=zero_velocity_confirmed,
                                  operation_enable_requested=False,
                                  operation_enabled_observed=getattr(session, '_probe_operation_enabled_observed', False))
                out.event('bench_result', result)
            if error is not None:
                raise error
            return 0

    def batch_selection(self):
        nodes, skipped = [], []
        for row in self.rows:
            node = row['node']
            label = '节点 {}'.format(node) if node is not None else '未分配席位'
            if row['quarantined'] or (self.context and (self.context[0], node) in self.blocked):
                skipped.append(label + '：已隔离，跳过')
            elif not row['online'] or row['lost']:
                skipped.append(label + '：离线或失联，跳过')
            elif node is not None:
                nodes.append(node)
        return nodes, skipped

    def review_batch(self, action, out, check_gate=True):
        policy = action_policy(action)
        if not policy['batch']:
            raise ValueError('仅支持全部失能或全部快停')
        nodes, skipped = self.batch_selection()
        for line in skipped:
            out.warn(line)
        if not nodes:
            raise ValueError('无在线节点；请先扫描')
        members = [self.review(policy['member'], node, None, out, check_gate) for node in nodes]
        token = '{} ALL {}'.format(policy['member'].upper(), ','.join(map(str, nodes)))
        return dict(action=action, nodes=nodes, members=members, skipped=skipped,
            token=token, lines=['接口 {}  {}  节点 {}'.format(self.context[0], policy['label'], ','.join(map(str, nodes)))]
                + skipped + [line for member in members for line in member['lines'] if line.startswith('写 ')])

    def execute_batch(self, review, out):
        policy = action_policy(review['action'])
        if not policy['batch'] or any(m['action'] != policy['member'] for m in review['members']):
            raise ValueError('仅支持全部失能或全部快停')
        nodes, skipped = self.batch_selection()
        if nodes != review['nodes'] or skipped != review['skipped']:
            raise ValueError('在线或隔离节点清单在审阅后变化；请重新审阅')
        for line in skipped:
            out.warn(line)
        results = []
        for member in review['members']:
            node = member['node']
            try:
                out.check_cancel()
                # Recheck this target only: completed members deliberately mark
                # their snapshots stale and must not change the frozen cohort.
                self._fresh(node)
                self.execute(member, out)
                result = dict(node=node, success=True, state_unknown=False)
                out.ok('节点 {} {}成功；状态已读回，机械停止未知'.format(node, policy['label']))
            except BaseException as exc:
                result = dict(node=node, success=False, state_unknown=True, error=str(exc))
                out.bad('节点 {} {}失败，状态未知：{}'.format(node, policy['label'], exc))
            results.append(result)
            out.event('batch_result', result)
        return results

    def close(self, out=None):
        """Bounded exit-only Shutdown; preserve ordinary disable=0 semantics.

        Never persist coordinates/parameters during teardown. Reuse each action's
        original client, including lost ACKs; a latched node is never retried.
        """
        with self.lock:
            if self.shutdown_results is not None:
                return self.shutdown_results
            results = []
            p = self.tool._canopen
            class ExitSession(self.tool.MotionSession):
                def disable(self):
                    deadline = self.deadline()
                    self.control(p.CIA402_SHUTDOWN, deadline)
                    # E06 4.1.3: operation-enabled is statusword bit 2.
                    return self.wait_status(lambda sw: not sw & 4,
                        '退出 Shutdown；operation enabled 清零', deadline)
            def report(line):
                self.shutdown_lines.append(line)
                if out is not None:
                    out.info(line)
            if self.bus is not None:
                try:
                    for node, original in sorted(self.enabled_sessions.items()):
                        confirmed = False
                        try:
                            if ((self.context[0], node) in self.blocked
                                    or getattr(original.client, 'quarantined', False)):
                                raise ValueError('SDO 已隔离，禁止重试')
                            session = ExitSession(original.client, out or SilentOutput(), self.args.state_timeout)
                            sw = session.disable()
                            confirmed = True
                            report('节点 {} 已发失能 6040h=0x0006，6041h=0x{:04X}，operation enabled=0，失能已确认'.format(node, sw))
                        except BaseException as exc:
                            self._latch(original.client, node, '退出失能', exc, out or SilentOutput())
                            report('节点 {} 失能未确认，状态未知，须独立急停'.format(node))
                        results.append(dict(node=node, disabled_confirmed=confirmed))
                finally:
                    try:
                        self.bus.close()
                        report('总线已关闭（失能确认流程之后）')
                    except BaseException as exc:
                        report('总线关闭失败：{}'.format(exc))
                    self.bus = None
            self.shutdown_results = results
            return results
