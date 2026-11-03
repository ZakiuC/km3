"""Explicit PP-only manual steps. No automatic enable, release, or zero-drift test.

Each reviewed action is consumed once. The submitted target, original start,
calibration and limits stay bound to one SDO client until stop/disable/exit.
Actual-position changes inside that original envelope are observations, not a
reason to silently rebase a target or resume the old automatic transaction.
"""
import copy
import time
import uuid
from pathlib import Path

import pmp4010_position as position
from pmp4010_bench import BenchController, TELEMETRY
from pmp4010_tui import PmpApp, LogView

MANUAL_ACTIONS = ('absolute', 'relative', 'enable', 'go', 'disable', 'stop')
FIXED = ('control_mode_select', 'modes_of_operation', 'modes_of_operation_display',
         'home_offset', 'profile_velocity', 'profile_acceleration',
         'profile_deceleration', 'profile_end_velocity', 'velocity_feedback_unit',
         'software_position_limit', 'software_position_limit_max', 'halt_option') + position.LIMITS
LABELS = dict(absolute='提交绝对目标，不使能', relative='提交相对目标，不使能',
              enable='保持Halt使能', go='使能后新触发执行已提交目标', disable='失能', stop='软件快停')


class ManualController(BenchController):
    def __init__(self, tool, args):
        super().__init__(tool, args)
        velocity = getattr(args, 'manual_velocity', None)
        self.manual_velocity = 1000 if velocity is None else velocity
        self.manual_sessions = {}
        self.proofs = {}
        self._manual_review = None
        self.failure_diagnostics = {}

    def _configuration(self, out):
        if self.shutdown_results is not None:
            raise ValueError('手动会话已退出；禁止重新打开总线')
        try:
            return super()._configuration(out)
        except BaseException as exc:
            for node in list(self.proofs):
                self._cleanup_failure(node, self.manual_sessions[node], exc, out)
            raise

    def scan(self, out):
        with self.lock:
            if self.proofs:
                raise ValueError('已有提交目标；先单独D失能后才能重新扫描，持续保护保持运行')
            self._configuration(out)
            lo, hi = self.tool.parse_node_range(self.args.node_range) if self.args.node_range else (1, 127)
            found = []
            for node in range(lo, hi + 1):
                out.check_cancel()
                if (self.context[0], node) in self.blocked:
                    continue
                session = self._session(node, out)
                try:
                    session.call(session.deadline(), '扫描SDO身份', session.client.read_device_type)
                    found.append(node)
                    row = self.row(node) or self._empty(node)
                    row.update(online=True, lost=False)
                    self._put(row)
                except BaseException as exc:
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    self._cleanup_failure(node, session, exc, out)
            out.info('扫描完成：发现{}台；手动入口未发控制写入'.format(len(found)))
            self._refresh_locked(out)
            return found

    def discard_prepare(self):
        super().discard_prepare()
        self._manual_review = None

    def freeze_prepare(self, review):
        self.discard_prepare()
        self._manual_review = (review, copy.deepcopy(review))

    def _session(self, node, out):
        session = self.manual_sessions.get(node)
        if session is None:
            p, transport = self.tool._canopen, self.tool._canbus
            class ManualClient(p.SdoClient):
                def _manual_sdo(self, method, *args):
                    # Scope OSError/decoding ValueError to actual SDO methods.
                    # Local journal failures never enter this boundary. Mark
                    # the client itself so cleanup and ExitSession share the
                    # same uncertainty, including a send that enqueued first.
                    try:
                        return method(*args)
                    except p.SdoAbort:
                        raise  # Explicit device rejection is a known reply.
                    except (p.CanopenError, transport.CanBusError, OSError, ValueError):
                        self.quarantined = True
                        raise
                def read(self, index, subindex=0):
                    return self._manual_sdo(super().read, index, subindex)
                def write(self, index, subindex, data):
                    return self._manual_sdo(super().write, index, subindex, data)
                def _as_int(self, raw, size):
                    return self._manual_sdo(super()._as_int, raw, size)
            client = ManualClient(self.bus, node, timeout=self.args.timeout)
            session = self.tool.MotionSession(client, out, self.args.state_timeout)
            self.manual_sessions[node] = session
        session.out = out
        return session

    def _read(self, client, key, session=None):
        try:
            return super()._read(client, key, session)
        except ValueError:
            # The shared reader only raises ValueError here for OD-width
            # mismatch. A malformed response cannot authorize another request.
            client.quarantined = True
            raise

    def _snapshot(self, session, envelope=None, states=None, observer=None):
        proof = self.proofs.get(session.client.node_id)
        if envelope is None and proof:
            envelope = (proof['args'], proof['start'])
        def observed(key):
            value = self._read(session.client, key, session)
            if observer is not None:
                observer(key, value)
            p = self.tool._canopen
            if key == 'statusword':
                if value & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR):
                    raise ValueError('首个或末个状态样本出现故障/PP跟随误差；6041=0x{:04X}'.format(value))
                state = (p.CIA402_DISABLED if value & p.CIA402_DISABLED_MASK == p.CIA402_DISABLED
                         else value & p.CIA402_MASK)
                if states is not None and state not in states:
                    raise ValueError('关键状态样本异常，禁止隐含使能；6041=0x{:04X}，允许状态={}'.format(
                        value, ','.join('0x{:04X}'.format(s) for s in states)))
            if key == 'error_code_object' and value:
                raise ValueError('603Fh故障码非零')
            if key == 'actual_position' and envelope:
                position.check_envelope(envelope[0], envelope[1], value)
                if proof and not proof['expected']['software_position_limit'] <= value <= proof['expected']['software_position_limit_max']:
                    raise ValueError('实际位置越过设备607Dh行程')
            return value
        values = {k: observed(k) for k in TELEMETRY}
        cal = position.calibration([self._read(session.client, k, session) for k in position.CALIBRATION])
        # Bracket the slower protected-parameter reads with the latest state.
        for key in ('statusword', 'actual_position', 'actual_velocity'):
            values[key] = observed(key)
        return values, cal

    def _validate(self, values, cal, a, start, stationary=False, allow_hold=False):
        p = self.tool._canopen
        if values['statusword'] & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR) or values['error_code_object']:
            raise ValueError('故障或PP跟随误差；禁止自动复位')
        if values['control_mode_select'] != 0 or values['modes_of_operation'] != 1 or values['modes_of_operation_display'] != 1:
            raise ValueError('手动入口仅支持CiA402 PP1；模式已变化')
        if values['home_offset'] != 0:
            raise ValueError('607Ch非零；拒绝手动动作')
        if values['halt_option'] not in (1, 2):
            raise ValueError('605Dh暂停方式必须实读为1或2')
        if values['profile_end_velocity'] != 0:
            raise ValueError('6082h末速度非零；拒绝手动动作')
        position.validate(a, self.specs)
        position.check_start(a, start, cal, allow_hold=allow_hold)
        position.check_envelope(a, start, values['actual_position'])
        low, high = values['software_position_limit'], values['software_position_limit_max']
        if not low <= start <= high or not low <= a.target <= high or not low <= values['actual_position'] <= high:
            raise ValueError('起点/目标/实际位置超出设备607Dh行程')
        if (a.velocity > values['max_velocity'] or position.unit_to_velocity(a.velocity, cal) > values['max_motor_speed']
                or a.acceleration > values['max_profile_acceleration'] or a.deceleration > values['max_deceleration']):
            raise ValueError('速度/加减速度超过设备607F/6080/60C5/60C6上限')
        if stationary and values['actual_velocity'] != 0:
            raise ValueError('实际速度非零；请先用X/D独立停止')

    def _proof_guard(self, node, values, cal, stationary=False, *, _controlword=None, _ack=False):
        proof = self.proofs.get(node)
        if proof is None or proof['session'] is not self.manual_sessions.get(node):
            raise ValueError('缺少本会话同client已提交目标证明；请先A/R')
        self._validate(values, cal, proof['args'], proof['start'], stationary,
                       allow_hold=proof.get('hold', False))
        if (cal != proof['calibration'] or self.settings != proof['settings']
                or self.manual_velocity != proof['velocity'] or self.od_reason != proof['od_reason']
                or self.context != proof['context'] or self.od != proof['od']):
            raise ValueError('目标证明的标定/设置/OD已变化；拒绝继续')
        if any(values[k] != proof['expected'][k] for k in FIXED):
            raise ValueError('目标证明的受保护参数已变化；拒绝继续')
        if values['target_position'] != proof['target'] or values['position_demand'] != proof['target']:
            raise ValueError('607A/6062不是本会话人工目标；拒绝释放Halt')
        p = self.tool._canopen
        state = (p.CIA402_DISABLED if values['statusword'] & p.CIA402_DISABLED_MASK == p.CIA402_DISABLED
                 else values['statusword'] & p.CIA402_MASK)
        expected = {'submitted': (p.CIA402_SWITCHED_ON,), 'enabled': (p.CIA402_ENABLED,),
                    'running': (p.CIA402_ENABLED,), 'stopped': (p.CIA402_QUICK_STOP, p.CIA402_DISABLED)}[proof['phase']]
        word = {'submitted': 0x127, 'enabled': 0x12f, 'running': 0x2f, 'stopped': 2}[proof['phase']]
        # Only the internal enabled Go handshake supplies these overrides;
        # review/public actions retain the exact stable phase proof by default.
        if _controlword is not None:
            word = _controlword
        if (state not in expected or values['controlword'] != word or
                _ack is not None and bool(values['statusword'] & p.CIA402_PP_SETPOINT_ACK) != _ack):
            raise ValueError('目标证明的状态/Halt/ACK已变化；拒绝继续')
        return proof

    def review(self, action, node, value, out, check_gate=True, *, _hold=False, _start=None):
        if action not in MANUAL_ACTIONS:
            raise ValueError('手动PP入口不开放Z/P/B或自动准备/回零')
        if check_gate:
            self.discard_prepare()
        row = self._fresh(node)
        v, cal = row['values'], row['calibration']
        if action in ('absolute', 'relative'):
            if node in self.proofs:
                raise ValueError('本会话已有提交目标；须先单独D失能再重新提交')
            start = v['actual_position'] if _start is None else _start
            target = start + int(value) if action == 'relative' else int(value)
            a = self._args(row, target)
            a.velocity = self.manual_velocity
            if _hold and (action != 'absolute' or target != start):
                raise ValueError('保持位置仅可提交本次冻结起点')
            self._validate(v, cal, a, start, stationary=True, allow_hold=_hold)
            if v['statusword'] & self.tool._canopen.CIA402_DISABLED_MASK != self.tool._canopen.CIA402_DISABLED:
                raise ValueError('提交目标要求Switch on disabled；请先单独D失能并重新审阅')
        elif action in ('enable', 'go'):
            proof = self._proof_guard(node, v, cal, stationary=True)
            if proof['phase'] != ('submitted' if action == 'enable' else 'enabled'):
                raise ValueError('阶段不符：须按A/R提交→E保持Halt使能→G解除Halt操作')
            start, target, a = proof['start'], proof['target'], copy.copy(proof['args'])
        else:
            start = target = v['actual_position']
            a = self._args(row, target)
        if not self.tool.refuse_motion(out, LABELS[action], a):
            raise ValueError('OD闸门关闭；须本会话显式确认')
        controls = {'absolute': [0x106, 0x107, 0x127, 0x137, 0x127],
                    'relative': [0x106, 0x107, 0x127, 0x137, 0x127],
                    'enable': [0x12f], 'go': [0x2f, 0x3f, 0x2f], 'stop': [2], 'disable': [0]}[action]
        lines = ['手动PP：' + LABELS[action],
                 '接口 {} 节点 {}；原始起点 {} U，人工目标 {} U'.format(self.context[0], node, start, target),
                 '已取消自动起步与零漂移检查；每阶段停住，等待下一次人工确认。']
        if action not in ('disable', 'stop'):
            rpm = position.unit_to_velocity(a.velocity, cal)
            lines += ['轮廓速度 {} U/s = {:.6f} rpm；加/减速度 {}/{} U/s²'.format(a.velocity, float(rpm), a.acceleration, a.deceleration),
                      '原始包络 [{},{}] U；单次预算 {} U，另限电机轴1度'.format(a.soft_min, a.soft_max, a.max_displacement)]
        if action in ('absolute', 'relative'):
            lines += ['仅607A={}写读回及新ACK 0→1→0、6062=人工目标；保持Halt，不使能。'.format(target)]
        if action == 'enable':
            lines += ['仅012F；保持Halt。包络内反馈变化允许，绝不自动解除Halt。']
        if action == 'go':
            lines += ['使能后重写冻结607A并读回，再002F→003F→002F；新ACK 0→1→0触发。',
                      '002F起允许包络内正常移动；持续监测故障/通信/参数，目标不重算。']
        lines += ['6040h计划：' + ' → '.join('0x{:04X}'.format(w) for w in controls),
                  '软件X快停不能替代独立急停；q退出执行有界Shutdown。']
        review = dict(action=action, node=node, target=target, start=start, args=a,
                      context=self.context, settings=copy.deepcopy(self.settings), velocity=self.manual_velocity,
                      od=copy.deepcopy(self.od), od_reason=self.od_reason, calibration=copy.deepcopy(cal),
                      before=copy.deepcopy(v), controls=controls, lines=lines,
                      token='MANUAL {} {} {} {}'.format(action.upper(), self.context[0], node, target), hold=_hold)
        if self.args.motion_log and action not in ('disable', 'stop'):
            path = Path(self.args.motion_log)
            a.motion_log = str(path.with_name(path.stem + '-' + action + '-' + uuid.uuid4().hex + '.jsonl'))
        if check_gate:
            self.freeze_prepare(review)
        return review

    def _cleanup_failure(self, node, session, exc, out):
        # A failed/uncertain action permanently blocks this target for this run.
        # Never create a new client or retry an uncertain request for cleanup.
        owned = node in self.proofs or node in self.enabled_sessions or session.needs_stop
        self.proofs.pop(node, None)
        session.cancel_event = None
        self._latch(session.client, node, '手动动作', exc, out)
        confirmed = None
        if owned and not getattr(session.client, 'quarantined', False):
            confirmed = session.stop_after_failure()
        first_failure = self.failure_diagnostics.get(node) or {}
        raw = first_failure.get('raw_statusword')
        word = first_failure.get('controlword')
        detail = ('CW=0x{:04X} 6041=0x{:04X} {}；'.format(word,raw,first_failure.get('decoded_state',''))
                  if word is not None and raw is not None else '')
        reason = '手动保护锁定：{}{}；停止确认={}'.format(detail,str(exc) or first_failure.get('reason','动作已中止'),confirmed)
        if confirmed is not True:
            reason += '；停止未确认，请使用独立急停'
        self.blocked[(self.context[0], node)] = reason
        row = self.row(node)
        if row:
            row.update(lost=True, quarantined=True, quarantine_reason=reason,
                       failure_diagnostic=copy.deepcopy(self.failure_diagnostics.get(node)))
            self._put(row)
        out.bad(reason)
        out.event('bench_result', dict(action='manual_protection', node=node, success=False,
                  disabled_confirmed=confirmed, state_unknown=confirmed is not True,
                  mechanical_stop_confirmed=False))
        return confirmed

    def _refresh_locked(self, out):
        # Once a manual session exists, all telemetry and protection use its
        # exact client. No alternate refresh client may mask lost responses.
        for row in self.rows:
            node = row['node']
            if node is None or (self.context[0], node) in self.blocked:
                continue
            out.check_cancel()
            session = self._session(node, out)
            try:
                values, cal = self._snapshot(session)
                if node in self.proofs:
                    self._proof_guard(node, values, cal)
                row.update(values=values, calibration=cal, updated=time.monotonic(), online=True, lost=False)
                if row['name'] == '未分配':
                    row['name'] = session.call(session.deadline(), '读取名称', session.client.read_manufacturer_name)
                    row['firmware'] = session.call(session.deadline(), '读取固件版本', session.client.read_software_version)
                self._put(row)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                self._cleanup_failure(node, session, exc, out)

    def execute(self, review, out):
        with self.lock:
            frozen = self._manual_review
            self.discard_prepare()
            if frozen is None or frozen[0] is not review or frozen[1] != review:
                raise ValueError('手动审阅已失效、修改或复用；须重新审阅')
            node, action = review['node'], review['action']
            if action not in MANUAL_ACTIONS:
                raise ValueError('手动入口拒绝自动路径')
            self._allowed(node)
            self.failure_diagnostics.pop(node,None)
            if (self.settings != review['settings'] or self.manual_velocity != review['velocity']
                    or self.od_reason != review['od_reason']):
                raise ValueError('手动设置在审阅后已变化')
            self._configuration(out)
            if self.context != review['context'] or self.od != review['od']:
                raise ValueError('接口/OD在审阅后已变化')
            if not self.tool.refuse_motion(out, LABELS[action], review['args']):
                raise ValueError('OD闸门拒绝')
            session = self._session(node, out)
            session.cancel_event = out.cancel_event
            p = self.tool._canopen
            a = review['args']
            journal = None
            original_deadline = session.deadline
            end = time.monotonic() + min(a.move_timeout, 15)
            transition_end = None
            session.deadline = lambda: min(end, original_deadline(), transition_end or end)
            wrote = False
            last_diagnostic = {}

            def diagnostic(values, phase, word, allowed, state, point, reason):
                raw = values.get('statusword')
                decoded = None
                normalized = None
                if raw is not None:
                    normalized = (p.CIA402_DISABLED if raw & p.CIA402_DISABLED_MASK == p.CIA402_DISABLED
                                  else raw & p.CIA402_MASK)
                    decoded = {p.CIA402_DISABLED:'Switch on disabled',p.CIA402_READY:'Ready to switch on',
                        p.CIA402_SWITCHED_ON:'Switched on',p.CIA402_ENABLED:'Operation enabled',
                        p.CIA402_QUICK_STOP:'Quick stop active'}.get(normalized,'Other')
                return dict(phase=phase, controlword=word, raw_statusword=raw, decoded_state=decoded,
                    state=normalized, allowed_states=list(allowed), expected_state=state, point=point,
                    actual_position=values.get('actual_position'), actual_velocity=values.get('actual_velocity'),
                    fault_code=values.get('error_code_object'), observed_fields=list(values), reason=reason)

            def remember_failure(exc):
                # Preserve evidence before attempting any more journal I/O.
                # Cleanup telemetry must never replace the first failing raw.
                if node not in self.failure_diagnostics:
                    failure = copy.deepcopy(last_diagnostic)
                    failure['reason'] = str(exc) or ('等待期间已取消' if isinstance(exc,KeyboardInterrupt) else type(exc).__name__)
                    self.failure_diagnostics[node] = failure
                return self.failure_diagnostics[node]

            def transition_snapshot(phase, word, allowed, state):
                nonlocal last_diagnostic
                observed_values = {}
                status_count = 0
                def observe(key, value):
                    nonlocal status_count, last_diagnostic
                    observed_values[key] = value
                    if key == 'statusword':
                        status_count += 1
                        raw_state = (p.CIA402_DISABLED if value & p.CIA402_DISABLED_MASK == p.CIA402_DISABLED
                                     else value & p.CIA402_MASK)
                        reason = ('故障/PP跟随误差' if value & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR) else
                                  '非预期状态，禁止隐含使能' if raw_state not in allowed else
                                  '仍在前态，等待目标态' if raw_state != state else '已观察目标态，继续核对完整快照')
                        last_diagnostic = diagnostic(observed_values,phase,word,allowed,state,
                                                     'first' if status_count==1 else 'last',reason)
                        if value & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR) or raw_state not in allowed:
                            self.failure_diagnostics.setdefault(node,copy.deepcopy(last_diagnostic))
                        if journal:
                            journal.event('manual_transition_sample', **last_diagnostic)
                    else:
                        # Associate position/velocity/fault reads with this
                        # snapshot, rather than silently using prior telemetry.
                        for field in ('actual_position','actual_velocity','error_code_object'):
                            dest = 'fault_code' if field=='error_code_object' else field
                            last_diagnostic[dest] = observed_values.get(field)
                        last_diagnostic['observed_fields'] = list(observed_values)
                    # The ordinary snapshot brackets velocity twice. During
                    # this stationary transition, never let its final zero
                    # sample conceal an earlier nonzero velocity.
                    if key == 'actual_velocity' and value != 0:
                        raise ValueError('迁移样本实际速度非零，禁止继续')
                    if key == 'actual_position' and not expected['software_position_limit'] <= value <= expected['software_position_limit_max']:
                        raise ValueError('迁移样本实际位置越过设备607Dh行程')
                return self._snapshot(session,(a,review['start']),allowed,observer=observe)
            try:
                if action not in ('disable', 'stop'):
                    position.require_velocity_library()
                    journal = position.Journal(a.motion_log, 'manual', dict(node=node, action=action,
                        start=review['start'], target=review['target'], settings=review['settings'],
                        velocity=a.velocity, od_gaps_bypass=self.od_reason), observer=out.event)
                if action not in ('disable', 'stop'):
                    values, cal = self._snapshot(session,
                        envelope=(a, review['start']) if action in ('absolute', 'relative') else None)
                if action in ('absolute', 'relative'):
                    self._validate(values, cal, a, review['start'], stationary=True, allow_hold=review.get('hold', False))
                    if cal != review['calibration'] or any(values[k] != review['before'][k] for k in FIXED + ('target_position', 'position_demand', 'controlword')):
                        raise ValueError('标定/受保护参数在审阅后已变化')
                    if values['statusword'] & p.CIA402_DISABLED_MASK != p.CIA402_DISABLED:
                        raise ValueError('提交目标要求Switch on disabled；请先D')
                    # Seal before the first write: even lost ACKs owe the same
                    # client's bounded cleanup, never a replacement transaction.
                    self.enabled_sessions[node] = session
                    expected = copy.deepcopy(values)
                    def write(key, val):
                        nonlocal wrote
                        current, current_cal = self._snapshot(session, (a, review['start']), (p.CIA402_DISABLED,))
                        if (current_cal != cal or any(current[k] != expected[k] for k in FIXED)
                                or current['target_position'] != expected['target_position']
                                or current['controlword'] != expected['controlword']):
                            raise ValueError('提交写入前状态/受保护参数已变化')
                        wrote = True
                        session.needs_stop = True
                        spec = self.objects[key]
                        session.call(session.deadline(), '手动写入 '+key, session.client.write,
                                     spec['index_value'], spec['sub'], position.encode(spec, val))
                        actual = self._read(session.client, key, session)
                        if journal:
                            journal.event('write_readback', object=key, expected=val, actual=actual)
                        if actual != val:
                            raise ValueError(key+'写读回不一致')
                        expected[key] = val
                    write('profile_velocity', a.velocity)
                    write('target_position', a.target)
                    def guard(previous, state, ack=None, demand=False):
                        nonlocal last_diagnostic
                        allowed = tuple(dict.fromkeys((previous,state)))
                        current, current_cal = transition_snapshot('等待受控迁移',word,allowed,state)
                        self._validate(current, current_cal, a, review['start'], stationary=True, allow_hold=review.get('hold', False))
                        if (self.settings != review['settings'] or self.manual_velocity != review['velocity'] or
                                self.context != review['context'] or self.od != review['od'] or self.od_reason != review['od_reason']):
                            raise ValueError('受控迁移期间会话设置/OD已变化')
                        if current_cal != cal or any(current[k] != expected[k] for k in FIXED):
                            raise ValueError('提交期间标定/受保护参数变化')
                        if current['target_position'] != a.target:
                            raise ValueError('607A人工目标已变化')
                        if current['controlword'] != word:
                            raise ValueError('6040h/Halt控制字读回不一致')
                        last_diagnostic = diagnostic(current,'等待受控迁移',word,allowed,state,'complete',
                            '仍在前态，等待目标态' if current['statusword'] & p.CIA402_MASK != state else '完整快照已核对')
                        if journal:
                            journal.event('manual_transition_snapshot',**last_diagnostic)
                        if current['statusword'] & p.CIA402_MASK != state:
                            return None
                        if ack is not None and bool(current['statusword'] & p.CIA402_PP_SETPOINT_ACK) != ack:
                            return None
                        if demand and current['position_demand'] != a.target:
                            raise ValueError('6062不是本次人工目标')
                        return current
                    for word, previous, state in ((0x106,p.CIA402_DISABLED,p.CIA402_READY),
                                        (0x107,p.CIA402_READY,p.CIA402_SWITCHED_ON),
                                        (0x127,p.CIA402_SWITCHED_ON,p.CIA402_SWITCHED_ON),
                                        (0x137,p.CIA402_SWITCHED_ON,p.CIA402_SWITCHED_ON),
                                        (0x127,p.CIA402_SWITCHED_ON,p.CIA402_SWITCHED_ON)):
                        # Fresh guard before every control transition; bit10 is
                        # deliberately irrelevant while Halt=1 (E06 table6-9).
                        current, current_cal = transition_snapshot('控制字写入前',word,(previous,),previous)
                        self._validate(current, current_cal, a, review['start'], stationary=True, allow_hold=review.get('hold', False))
                        if (self.settings != review['settings'] or self.manual_velocity != review['velocity'] or
                                self.context != review['context'] or self.od != review['od'] or self.od_reason != review['od_reason']):
                            raise ValueError('控制字写入前会话设置/OD已变化')
                        if current_cal != cal or any(current[k] != expected[k] for k in FIXED) or current['target_position'] != a.target:
                            raise ValueError('提交控制字前受保护参数已变化')
                        if word == 0x137 and current['statusword'] & p.CIA402_PP_SETPOINT_ACK:
                            raise ValueError('新ACK触发前已经为1')
                        if journal:
                            journal.event('manual_controlword',**diagnostic(current,'控制字写入',word,
                                tuple(dict.fromkeys((previous,state))),state,'before_write','即将发送控制字，状态值来自写前采样'))
                        session.control(word, session.deadline())
                        if journal:
                            journal.event('manual_controlword',**diagnostic(current,'控制字写确认',word,
                                tuple(dict.fromkeys((previous,state))),state,'write_ack','已收到SDO写确认；6041仍须重新采样核对'))
                        deadline = session.deadline()
                        transition_end = deadline
                        while True:
                            if time.monotonic() >= deadline:
                                raise ValueError('受控状态迁移或新ACK握手超时')
                            observed = guard(previous,state, ack=(word == 0x137) if word in (0x127, 0x137) else None,
                                             demand=word == 0x137 or word == 0x127 and bool(current['statusword'] & p.CIA402_PP_SETPOINT_ACK))
                            if observed is not None:
                                break
                            if time.monotonic() >= deadline:
                                raise ValueError('受控状态迁移或新ACK握手超时')
                            session.pause(deadline)
                        transition_end = None
                    if observed['position_demand'] != a.target:
                        raise ValueError('6062不是本次人工目标')
                    self.proofs[node] = dict(session=session, target=a.target, start=review['start'], args=copy.copy(a),
                        calibration=copy.deepcopy(cal), expected=expected, phase='submitted',
                        context=self.context, od=copy.deepcopy(self.od), od_reason=self.od_reason,
                        velocity=self.manual_velocity, settings=copy.deepcopy(self.settings), hold=review.get('hold', False))
                    values = observed
                elif action == 'go':
                    proof = self._proof_guard(node, values, cal, stationary=True)
                    if proof['phase'] != 'enabled':
                        raise ValueError('阶段或单次目标证明失效')
                    if review['start'] != proof['start'] or review['target'] != proof['target']:
                        raise ValueError('原始起点/目标证明不符')
                    # E06 table6-10 updates the position command before the
                    # bit4 rising edge; §6.3.6 triggers after motor enable.
                    # Refresh executable data while still enabled with Halt.
                    # Re-download exactly the sealed absolute target: this is
                    # a protocol ordering step, never a feedback-based rebase
                    # or a claim about undocumented firmware invalidation.
                    spec = self.objects['target_position']
                    wrote = True  # Register responsibility BEFORE attempting.
                    session.needs_stop = True
                    deadline = session.deadline()
                    transition_end = deadline
                    if journal:
                        journal.event('manual_enabled_target',point='before_write',
                                      target=proof['target'],controlword=0x12f)
                    session.call(deadline,'使能Halt重写冻结607A',session.client.write,
                                 spec['index_value'],spec['sub'],position.encode(spec,proof['target']))
                    if journal:
                        journal.event('manual_enabled_target',point='write_ack',target=proof['target'])
                    actual = self._read(session.client,'target_position',session)
                    if journal:
                        journal.event('manual_enabled_target',point='readback',target=proof['target'],actual=actual)
                    if actual != proof['target']:
                        raise ValueError('使能Halt冻结607A写读回不一致')
                    values, cal = self._snapshot(session,states=(p.CIA402_ENABLED,))
                    self._proof_guard(node,values,cal,stationary=True)
                    transition_end = None
                    # E06 §6.3.6 says to trigger after enabling the motor;
                    # table6-10 accepts/executes a new bit4 rising edge. An
                    # ACK obtained in Switched on is only preload evidence,
                    # never proof that releasing Halt will start a trajectory.
                    # Keep the same absolute 607A: do not rebase on feedback.
                    # Go's initial zero-speed guard ends at its first 002F;
                    # subsequent ACK snapshots may legitimately show motion.
                    def go_snapshot(phase, word, ack=None):
                        nonlocal last_diagnostic
                        samples, ack_samples = {}, []
                        def observe(key, value):
                            nonlocal last_diagnostic
                            samples[key] = value
                            if key == 'statusword':
                                ack_samples.append(bool(value & p.CIA402_PP_SETPOINT_ACK))
                                last_diagnostic = diagnostic(samples,phase,word,(p.CIA402_ENABLED,),
                                    p.CIA402_ENABLED,'first' if len(ack_samples)==1 else 'last',
                                    'Operation enabled内新触发采样，允许正常移动')
                                if value & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR):
                                    self.failure_diagnostics.setdefault(node,copy.deepcopy(last_diagnostic))
                                if journal:
                                    journal.event('manual_transition_sample',**last_diagnostic)
                        current, current_cal = self._snapshot(session, states=(p.CIA402_ENABLED,), observer=observe)
                        self._proof_guard(node,current,current_cal,_controlword=word,_ack=None)
                        last_diagnostic = diagnostic(current,phase,word,(p.CIA402_ENABLED,),p.CIA402_ENABLED,
                            'complete','完整快照已核对；等待新ACK' if ack is not None and
                            not all(v == ack for v in ack_samples) else '完整快照及新ACK已核对')
                        if journal:
                            journal.event('manual_transition_snapshot',**last_diagnostic,
                                          expected_ack=ack,ack_samples=ack_samples)
                        return current, current_cal, all(v == ack for v in ack_samples)

                    previous_word, previous_ack = 0x12f, False
                    for word, ack in ((0x2f,False),(0x3f,True),(0x2f,False)):
                        values, cal, matched = go_snapshot('新触发写入前',previous_word,previous_ack)
                        if not matched:
                            raise ValueError('新触发写入前ACK已变化')
                        # The first release still requires zero speed; after
                        # it, movement is expected and guarded by the original
                        # envelope and all protected fields, not exact drift.
                        if previous_word == 0x12f and values['actual_velocity'] != 0:
                            raise ValueError('实际速度非零；请先用X/D独立停止')
                        if journal:
                            journal.event('manual_controlword',**diagnostic(values,'控制字写入',word,
                                (p.CIA402_ENABLED,),p.CIA402_ENABLED,'before_write','即将发送使能后新触发控制字'))
                        wrote = True
                        # One fixed deadline includes the download ACK and
                        # all ACK polling; no fresh budget on each sample.
                        deadline = session.deadline()
                        transition_end = deadline
                        session.control(word,deadline)
                        if journal:
                            journal.event('manual_controlword',**diagnostic(values,'控制字写确认',word,
                                (p.CIA402_ENABLED,),p.CIA402_ENABLED,'write_ack','已收到SDO写确认；6041仍须重新采样核对'))
                        while True:
                            if time.monotonic() >= deadline:
                                raise ValueError('Operation enabled新ACK握手超时')
                            values, cal, matched = go_snapshot('使能后新ACK核对',word,ack)
                            if matched:
                                break
                            session.pause(deadline)
                        transition_end = None
                        previous_word, previous_ack = word, ack
                    proof['phase'] = 'running'
                    self._proof_guard(node,values,cal)
                elif action == 'enable':
                    proof = self._proof_guard(node, values, cal, stationary=True)
                    if proof['phase'] != 'submitted':
                        raise ValueError('阶段或单次目标证明失效')
                    if review['start'] != proof['start'] or review['target'] != proof['target']:
                        raise ValueError('原始起点/目标证明不符')
                    wrote = True
                    word = 0x12f
                    allowed = (p.CIA402_SWITCHED_ON,p.CIA402_ENABLED)
                    if journal:
                        journal.event('manual_controlword',**diagnostic(values,'控制字写入',word,allowed,
                            p.CIA402_ENABLED,'before_write','即将发送控制字，状态值来自写前采样'))
                    session.control(word, session.deadline())
                    if journal:
                        journal.event('manual_controlword',**diagnostic(values,'控制字写确认',word,allowed,
                            p.CIA402_ENABLED,'write_ack','已收到SDO写确认；6041仍须重新采样核对'))
                    proof['phase'] = 'enabled'
                    samples = {}
                    sample_count = 0
                    def observe_proof(key,value):
                        nonlocal sample_count,last_diagnostic
                        samples[key] = value
                        if key == 'statusword':
                            sample_count += 1
                            last_diagnostic = diagnostic(samples,'Halt使能核对',
                                word,allowed,p.CIA402_ENABLED,'first' if sample_count==1 else 'last','新鲜状态采样，按同会话目标证明核对')
                            if value & (p.CIA402_FAULT | p.CIA402_PP_FOLLOWING_ERROR):
                                last_diagnostic['reason'] = '故障/PP跟随误差'
                                self.failure_diagnostics.setdefault(node,copy.deepcopy(last_diagnostic))
                            if journal:
                                journal.event('manual_transition_sample',**last_diagnostic)
                    # Keep the original Halt enable proof policy.
                    values, cal = self._snapshot(session,observer=observe_proof)
                    last_diagnostic = diagnostic(values,'Halt使能核对',
                        word,allowed,p.CIA402_ENABLED,'complete','完整快照按目标证明核对')
                    if journal:
                        journal.event('manual_transition_snapshot',**last_diagnostic)
                    self._proof_guard(node, values, cal)
                    if values['actual_velocity'] != 0:
                        raise ValueError('Halt使能后实际速度非零')
                else:
                    # X and D remain independent explicit user operations.
                    wrote = True
                    self.enabled_sessions[node] = session
                    getattr(session, 'stop' if action == 'stop' else 'disable')()
                    if action == 'stop' and node in self.proofs:
                        # Quick stop may retain torque. Keep the original
                        # target/envelope protected until explicit D or q;
                        # this phase can never authorize E/G resumption.
                        self.proofs[node]['phase'] = 'stopped'
                    else:
                        self.proofs.pop(node, None)
                    values, cal = self._snapshot(session)
                    if action == 'stop' and node in self.proofs:
                        self._proof_guard(node, values, cal)
                self._put(dict(self.row(node), values=values, calibration=cal, updated=time.monotonic(), lost=False))
                message = {'absolute': '人工目标已提交；保持Halt未使能，等待E',
                           'relative': '人工目标已提交；保持Halt未使能，等待E',
                           'enable': '已使能且保持Halt；等待G', 'go': '使能后新ACK 0→1→0已确认；人工目标执行中，请观察反馈',
                           'stop': '软件快停已确认；机械停止仍未知', 'disable': '失能已确认；机械停止仍未知'}[action]
                out.info(message)
                out.event('manual_result', dict(node=node, action=action, success=True, message=message,
                          position=values['actual_position'], target=review['target']))
                if journal:
                    journal.event('manual_stage_complete', message=message, values=values)
            except BaseException as exc:
                first_failure = remember_failure(exc)
                if journal:
                    try:
                        journal.event('exception', message=str(exc), write_attempted=wrote, first_failure=first_failure)
                    except BaseException:
                        pass  # Recording failure must never skip stop/disable.
                if wrote or node in self.proofs or getattr(session.client, 'quarantined', False):
                    session.deadline = original_deadline
                    confirmed = self._cleanup_failure(node, session, exc, out)
                    if journal:
                        try:
                            journal.event('manual_cleanup',disabled_confirmed=confirmed,
                                mechanical_stop_confirmed=False,first_failure=first_failure)
                        except BaseException:
                            pass
                raise
            finally:
                session.deadline = original_deadline
                session.cancel_event = None
                if journal:
                    try:
                        journal.close()
                    except BaseException as exc:
                        if (self.context[0], node) not in self.blocked and (wrote or node in self.proofs):
                            self._cleanup_failure(node, session, exc, out)
                        raise
            return 0


class ManualApp(PmpApp):
    title = 'PMP4010 PP手动测试：每步由操作者确认'

    def __init__(self, tool, args):
        super().__init__(tool, args)
        self.controller = ManualController(tool, args)
        self.help_log = LogView(max_lines=500)
        for line in (
            '手动入口仅支持PP1；已取消自动起步与零漂移检查，所有阶段由人类确认。',
            'F5启动确认后只扫描/读取；F2输入脚本行程和预算，另受1度硬顶。',
            '详情A/R：人工目标提交，不使能；新ACK 0→1→0并核6062后保持Halt。',
            '详情E：仅012F保持Halt使能；G：002F→003F→002F使能后新触发同一冻结目标。',
            'A/R、E、G均需F5审阅、输入完整确认串、再次F5执行。',
            'V：设置手动轮廓速度，默认1000U/s；审阅显示rpm并核设备上限。',
            'bit10在Halt=1时仅表示零速；Halt=0才作为目标到达证据之一。',
            '故障/跟随误差/越界/参数变化有界快停→失能并锁定；通信隔离不重试。',
            'X软件快停；D失能；q退出Shutdown。停止未确认时使用独立急停。',
            'Z/P/B及PV/CSV均未开放；没有自动复位、自动重提交或自动释放Halt。'):
            self.help_log.append(line)
        self.help_log.scroll(self.help_log.max_lines)
        self.log.append('手动PP：已取消自动起步与零漂移检查；A/R→E→G逐步确认。')

    def begin_action(self, action):
        if action not in MANUAL_ACTIONS:
            self.log.append('手动PP不开放此动作；Z/P/B/批量自动路径禁止', 'err')
            return
        super().begin_action(action)
        if action == 'absolute' and self.current_view == 'action':
            # Never supply the current position as an implicit manual target.
            self._field('人工绝对目标 U（必填）', '', 'number')

    def on_key(self, name, key):
        if self.current_view == 'manual_velocity' and name not in ('esc', 'q', '?', 'f1'):
            if name == 'f5':
                try:
                    speed = int(self.input_field.value)
                    if not 0 < speed <= 0xffffffff:
                        raise ValueError('手动速度须为正UInt32')
                    self.controller.manual_velocity = speed
                    self.controller.discard_prepare()
                    self.current_view = 'detail'
                    self.set_form(None)
                    self.log.append('手动速度设为{} U/s；须重新提交目标及审阅设备上限'.format(speed))
                except ValueError as exc:
                    self.log.append(str(exc), 'err')
            return False
        if not self.busy and self.current_view == 'detail':
            if name.lower() == 'g':
                self.begin_action('go')
                return False
            if name.lower() == 'v':
                if self.controller.proofs:
                    self.log.append('已有提交目标；先单独D失能后才能修改速度', 'err')
                    return False
                self.current_view = 'manual_velocity'
                self._field('手动轮廓速度 U/s（默认1000；F5保存）', self.controller.manual_velocity, 'number')
                return False
        return super().on_key(name, key)

    def _start_settings(self, resume=None):
        if self.controller.proofs:
            self.log.append('已有提交目标；先单独D失能后才能修改行程/预算', 'err')
            return
        return super()._start_settings(resume)

    def hints(self):
        if self.current_view == 'detail' and not self.busy:
            return ['A/R 提交目标', 'E 保持Halt使能', 'G 解除Halt', 'V 速度', 'X 软件快停', 'D 失能', 'F6 刷新', 'F2 设置', 'q 退出']
        if self.current_view == 'manual_velocity':
            return ['F5 保存速度', 'Esc 取消']
        if self.current_view == 'overview':
            return ['S 扫描', 'Enter 详情', 'R 刷新', 'F2 设置', '? 帮助', 'q 退出']
        return super().hints()

    def _detail_lines(self, row):
        lines = super()._detail_lines(row)
        # Replace the automatic-mode action explanation, preserving telemetry.
        if row.get('values'):
            lines = lines[:-2]
            v = row['values']
            halt = bool(v['controlword'] & 0x100)
            arrived = (not halt and bool(v['statusword'] & 0x400)
                       and v['actual_position'] == v['position_demand'] == v['target_position']
                       and v['actual_velocity'] == 0)
            phase = self.controller.proofs.get(row['node'], {}).get('phase')
            phase = {'submitted': '已提交', 'enabled': '保持Halt使能',
                     'running': '已解除Halt', 'stopped': '已快停'}.get(phase, '尚无本会话目标')
            lines += ['手动PP阶段：{}；Halt={}；目标到达={}'.format(phase, int(halt), '是' if arrived else '未确认'),
                      '人工目标607A={} U；内部目标6062={} U；手动速度{} U/s'.format(v['target_position'], v['position_demand'], self.controller.manual_velocity),
                      'A/R提交目标→E保持Halt使能→G解除Halt；X软件快停，D失能。',
                      '已取消自动起步与零漂移检查；Z/P/B禁止；V输入速度。']
        return lines

    def poll(self):
        # Let the existing worker/event lifecycle handle I/O; manual messages
        # deliberately describe a stage, never claim the target was reached.
        super().poll()
        blocked = self.controller.row(self.selected_row['node'])
        if blocked and blocked['quarantined']:
            self.progress = '手动保护已锁定；后续动作禁止'
            self.recovery = blocked['quarantine_reason']
            return
        if self.action in ('absolute', 'relative', 'enable', 'go') and not self.busy:
            phase = self.controller.proofs.get(self.selected_row['node'], {}).get('phase')
            if phase:
                self.recovery = {'submitted': '人工目标已提交；未使能，等待E',
                                 'enabled': '保持Halt使能；等待G',
                                 'running': '已解除Halt；按实际位置/目标反馈观察，X/D停止',
                                 'stopped': '软件快停已确认；持续原始包络保护，须D失能后重新提交目标'}[phase]
