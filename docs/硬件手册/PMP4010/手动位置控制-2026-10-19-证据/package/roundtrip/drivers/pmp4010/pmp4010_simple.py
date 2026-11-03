"""One-screen PP jog UI; every confirmed action uses ManualController's proof.

No connection, approval, target write or enable is implicit in construction.
The session origin and the reviewed absolute target never follow live feedback.
"""
import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import math
import queue
import threading
import time

import pmp4010_position as position
from pmp4010_manual import ManualController, FIXED
from pmp4010_tui import PmpApp, Grid, Rect, LogView, draw_box, draw_hsep, fit


def positive(text):
    value = signed(text)
    if value <= 0:
        raise ValueError('请输入有限正数')
    return value


def signed(text):
    try:
        value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        raise ValueError('请输入有限数值，可用正负号或分数') from None
    return value


def number(value):
    value = Fraction(value)
    try:
        floating = float(value)
        if math.isfinite(floating) and (floating != 0 or value == 0):
            return '{:.6g}'.format(floating)
    except OverflowError:
        pass
    with localcontext() as context:
        context.prec = 6
        return format(Decimal(value.numerator)/Decimal(value.denominator), '.6g')


def angle(value):
    try:
        floating = float(value)
        if math.isfinite(floating):
            return '{:.4f}'.format(floating).rstrip('0').rstrip('.')
    except OverflowError:
        pass
    return number(value)


class SimpleController(ManualController):
    def __init__(self, tool, args):
        super().__init__(tool, args)
        # Aggregate operations call the same stage methods under one lock.
        self.lock = threading.RLock()
        self.node = args.node if args.node is not None else 1
        if not 1 <= self.node <= 127:
            raise ValueError('手动节点须为1..127')
        self.args.node_range = str(self.node)
        self.origin = None
        self.software_zero = None
        self.linear = bool(getattr(args, 'manual_linear', False))
        # This is an operator-supplied machine measurement, never inferred
        # from 6091 or a guessed screw pitch. None cannot authorize motion.
        supplied = getattr(args, 'manual_mm_per_rev', None)
        if supplied is not None and not self.linear:
            raise ValueError('直线换算参数仅用于--manual-linear')
        self.mm_per_motor_revolution = positive(supplied) if supplied is not None else None
        self.coordinate_revision = 0
        self.session_calibration = None
        self.range_degrees = Fraction(1)
        self.step_degrees = Fraction('0.1')
        self.step_mm = Fraction('0.1')
        self.requested_rpm = None  # Preserve explicit legacy --manual-velocity U/s.
        self.next_velocity = self.manual_velocity
        self._simple_review = None

    def cancel_review(self):
        self._simple_review = None
        self.discard_prepare()

    def _session(self, node, out):
        session = super()._session(node, out)
        # Refresh/scan use the same cancellable client as action stages. A
        # safety key must not wait for the rest of a multi-object snapshot.
        session.cancel_event = out.cancel_event
        return session

    def _state(self, values):
        p = self.tool._canopen
        return (p.CIA402_DISABLED if values['statusword'] & p.CIA402_DISABLED_MASK == p.CIA402_DISABLED
                else values['statusword'] & p.CIA402_MASK)

    def authorize(self):
        with self.lock:
            if self.origin is not None:
                raise ValueError('会话已经授权；原点不可重新冻结')
            row = self._fresh(self.node)
            self.origin = row['values']['actual_position']
            self.session_calibration = copy.deepcopy(row['calibration'])
            self.software_zero = self.origin
            if self.linear:
                self._linear_range(row)
            else:
                self._set_range(self.range_degrees, row)
            self.od_reason = '操作者明确确认：本会话{}人工测试，接受所列OD未核对项及独立停机责任'.format('直线机械测程' if self.linear else '小角度')

    def _linear_range(self, row):
        # No origin-relative host travel cap. Retain the device's actual
        # 607D range and both 607A/6063 signed representation boundaries.
        v, g = row['values'], Fraction(row['calibration']['gear_ratio'])
        low = max(v['software_position_limit'], -0x80000000, math.ceil(Fraction(-0x80000000)/g))
        high = min(v['software_position_limit_max'], 0x7fffffff, math.floor(Fraction(0x7fffffff)/g))
        if not low < high or not low <= v['actual_position'] <= high:
            raise ValueError('当前位置不在驱动607Dh/607A/6063 Int32范围内')
        self.settings.update(soft_min=low, soft_max=high, max_displacement=high-low)

    def _validate_arguments(self, args):
        if not self.linear:
            return super()._validate_arguments(args)
        # A raw absolute 607A endpoint is Int32; the mathematical distance
        # between two legal signed endpoints may require UInt32. Reuse every
        # ordinary argument check with only that host budget policy adapted.
        if not 1 <= args.max_displacement <= 0xffffffff:
            raise ValueError('直线测程位移预算超出合法Int32端点间距')
        checked = copy.copy(args)
        checked.max_displacement = min(args.max_displacement,0x7fffffff)
        position.validate(checked,self.specs)

    def _check_start(self, args, start, cal, allow_hold=False):
        if not self.linear:
            return super()._check_start(args, start, cal, allow_hold)
        # Explicit measuring mode only: remove the angular policy ceiling,
        # retaining the same start/envelope/nonzero/Int32 semantics.
        position.check_envelope(args, start, start)
        delta = abs(args.target-start)
        if not (0 < delta <= args.max_displacement or allow_hold and delta == 0):
            raise ValueError('目标位移必须非零且不超过驱动合法端点间距')
        g = Fraction(cal['gear_ratio'])
        if any(not -0x80000000 <= value*g <= 0x7fffffff for value in (args.target,args.soft_min,args.soft_max)):
            raise ValueError('行程/目标经G换算后超出6063编码器Int32范围')

    @property
    def unit(self):
        return 'mm' if self.linear else '电机轴度'

    @property
    def step(self):
        return self.step_mm if self.linear else self.step_degrees

    def coordinate_scale(self, cal):
        scale = Fraction(cal['motor_degrees_per_user_unit'])
        if self.linear:
            if self.mm_per_motor_revolution is None:
                raise ValueError('直线换算未设置；先d失能，再c设置每电机轴转对应mm')
            scale = scale/360*self.mm_per_motor_revolution
        return scale

    def set_coordinate(self, kind, out, text=None):
        """Software-only coordinates after a fresh guarded snapshot; no write.

        Zero may capture a stationary sample under an intact physical proof.
        Retain that proof and its original guard: disabling can let a loaded
        mechanism fall. Changing the conversion still requires disable.
        """
        with self.lock:
            self.cancel_review()
            if self.origin is None or kind not in ('zero','conversion'):
                raise ValueError('请先确认本会话运动授权')
            value = positive(text) if kind == 'conversion' else None
            if kind == 'conversion' and not self.linear:
                raise ValueError('仅直线测程模式可设置mm换算')
            self._fresh(self.node)
            self._configuration(out)
            session = self._session(self.node, out)
            try:
                try:
                    values, cal = self._snapshot(session)
                    if self.node in self.proofs:
                        self._proof_guard(self.node, values, cal)
                except BaseException as exc:
                    if not isinstance(exc,KeyboardInterrupt):
                        self._cleanup_failure(self.node,session,exc,out)
                    raise
                # Validate even with no conversion: software zero is allowed
                # before mm scale is known, but never in another drive mode.
                a = self._args(dict(node=self.node, values=values), values['actual_position'])
                a.velocity = self.next_velocity
                self._validate(values, cal, a, values['actual_position'], stationary=True, allow_hold=True)
                if cal != self.session_calibration:
                    raise ValueError('标定与授权时不同；请退出重新核对')
                proof = self.proofs.get(self.node)
                if ((kind == 'conversion' and proof is not None) or
                        (proof is None and self._state(values) != self.tool._canopen.CIA402_DISABLED)):
                    raise ValueError('请先d失能并确认，再设置软件零点或换算；设置不发送控制写入')
                out.check_cancel()
                if kind == 'zero':
                    self.software_zero = values['actual_position']
                else:
                    self.mm_per_motor_revolution = value
                self.coordinate_revision += 1
                self._put(dict(self.row(self.node),values=values,calibration=cal,updated=time.monotonic(),lost=False))
                out.info('软件零点采样已设为 {} U；未发运动下载、未写607Ch'.format(self.software_zero) if kind == 'zero' else
                         '直线换算已设置为 {} mm/电机轴转；软件零点与驱动目标未变'.format(number(value)))
            finally:
                session.cancel_event = None

    def _set_range(self, degrees, row):
        degrees = positive(degrees)
        if degrees > 1:
            raise ValueError('测试半范围须大于0且不超过1度电机轴角度')
        units = int(degrees / Fraction(row['calibration']['motor_degrees_per_user_unit']))
        if units < 1:
            raise ValueError('范围不足1个用户单位，无法执行')
        v = row['values']
        low = max(self.origin-units, v['software_position_limit'], -0x80000000)
        high = min(self.origin+units, v['software_position_limit_max'], 0x7fffffff)
        if not low < high or not low <= v['actual_position'] <= high:
            raise ValueError('当前位置不在新范围与设备607Dh的交集中')
        self.range_degrees = degrees
        self.settings.update(soft_min=low, soft_max=high, max_displacement=units)

    def set_value(self, kind, text):
        with self.lock:
            self.cancel_review()
            row = self._fresh(self.node)
            if kind == 'range' and (self.proofs or row['values']['statusword'] & self.tool._canopen.CIA402_DISABLED_MASK != self.tool._canopen.CIA402_DISABLED):
                raise ValueError('请先d失能；设置仅可在失能且无目标证明时修改')
            value = positive(text)
            if kind == 'range':
                self._set_range(value, row)
            elif kind == 'speed':
                units = int(position.velocity_to_unit(value, row['calibration']))
                if not 1 <= units <= 0xffffffff:
                    raise ValueError('速度量化后不足1 U/s或超过UInt32')
                if (units > row['values']['max_velocity'] or
                        position.unit_to_velocity(units, row['calibration']) > row['values']['max_motor_speed']):
                    raise ValueError('速度超过设备上限')
                # Draft only: do not mutate an active proof or the drive's
                # current profile. Apply after the next explicit confirmation
                # has safely stopped/disabled the old target.
                self.requested_rpm, self.next_velocity = value, units
            elif kind == 'step':
                units = int(value / self.coordinate_scale(row['calibration']))
                if units < 1:
                    raise ValueError('步长不足1个用户单位，无法执行')
                if (not self.linear and value > 1) or units > self.settings['max_displacement']:
                    raise ValueError('步长超过本会话单动作范围或1度硬顶')
                if self.linear:
                    self.step_mm = value
                else:
                    self.step_degrees = value
            else:
                raise ValueError('未知设置')
            self.cancel_review()

    def quantities(self, row):
        cal = row['calibration']
        degree_unit = self.coordinate_scale(cal)
        step = int(self.step / degree_unit)
        return step, step*degree_unit, position.unit_to_velocity(self.next_velocity, cal)

    def review_action(self, kind, out, text=None):
        with self.lock:
            self.cancel_review()
            if kind not in ('forward', 'backward', 'hold','relative','absolute') or self.origin is None or not self.od_reason:
                raise ValueError('请先确认本会话运动授权')
            row = self._fresh(self.node)
            v, cal = row['values'], row['calibration']
            if cal != self.session_calibration:
                raise ValueError('标定与授权时不同；请退出重新核对')
            proof = self.proofs.get(self.node)
            if proof:
                self._proof_guard(self.node, v, cal, stationary=True)
            step, degrees, rpm = self.quantities(row)
            if step < 1 and kind in ('forward','backward'):
                raise ValueError('步长不足1个用户单位，无法执行')
            start = v['actual_position']
            scale = self.coordinate_scale(cal)
            requested = signed(text) if kind in ('relative','absolute') else None
            if requested is not None:
                base = start if kind == 'relative' else self.software_zero
                ideal = base+requested/scale
                if not self.settings['soft_min'] <= ideal <= self.settings['soft_max']:
                    raise ValueError('请求目标超出允许行程；量化不能绕过边界')
                # Quantize the signed offset, before adding the integer raw
                # zero. int(zero + offset) rounds negative origins wrongly.
                target = base+int(requested/scale)
            else:
                target = start + (step if kind == 'forward' else -step if kind == 'backward' else 0)
            a = self._args(row, target)
            a.velocity = self.next_velocity
            self._validate(v, cal, a, start, stationary=True, allow_hold=kind == 'hold')
            p = self.tool._canopen
            state = self._state(v)
            if state not in (p.CIA402_DISABLED,p.CIA402_READY,p.CIA402_SWITCHED_ON) and proof is None:
                raise ValueError('设备已在外部使能；请先Space/x停止并d失能')
            if not self.tool.refuse_motion(out, '人工直线机械测程' if self.linear else '人工小角度点动', a):
                raise ValueError('OD闸门关闭')
            review = dict(kind=kind, node=self.node, start=start, target=target, args=a,
                session=self._session(self.node, out), calibration=copy.deepcopy(cal), before=copy.deepcopy(v),
                settings=copy.deepcopy(self.settings), velocity=self.next_velocity, active_velocity=self.manual_velocity, od=copy.deepcopy(self.od),
                od_reason=self.od_reason, context=self.context, origin=self.origin,
                software_zero=self.software_zero,coordinate_revision=self.coordinate_revision,
                linear=self.linear,conversion=self.mm_per_motor_revolution,
                requested=requested,requested_rpm=self.requested_rpm, step_degrees=self.step_degrees,step_mm=self.step_mm,
                lines=['保持当前位置并使能（Halt保持）' if kind == 'hold' else
                    {'forward':'正向点动','backward':'反向点动','relative':'相对位移','absolute':'软件绝对位置'}[kind],
                    '冻结起点 {} U -> 终点 {} U；位移 {} U = {} 电机轴度'.format(start,target,target-start,angle(abs(target-start)*Fraction(cal['motor_degrees_per_user_unit']))),
                    ('请求 {} {} -> 实际 {} {}；软件目标 {} {}'.format(number(requested),self.unit,
                        number((target-start if kind=='relative' else target-self.software_zero)*scale),self.unit,
                        number((target-self.software_zero)*scale),self.unit) if requested is not None else
                    '请求步长 {} -> 实际 {} {}（{} U）'.format(number(self.step),number(degrees),self.unit,step)),
                    '请求速度 {} -> 实际 {} rpm（{} U/s）'.format(number(self.requested_rpm)+' rpm' if self.requested_rpm is not None else str(self.next_velocity)+' U/s',number(rpm),self.next_velocity),
                    '提交冻结当前位置后012F保持Halt' if kind == 'hold' else
                    '使能后重写冻结目标并触发002F→003F→002F；核对ACK 0→1→0',
                    'Enter 确认这一次动作；Esc 取消；Space/x 随时快停'])
            # Session identity is retained separately; deep-copying it would
            # clone transport state rather than seal the actual SDO client.
            seal = copy.deepcopy({k:v for k,v in review.items() if k != 'session'})
            self._simple_review = (review, seal, review['session'])
            return review

    def execute_action(self, review, out):
        with self.lock:
            frozen, self._simple_review = self._simple_review, None
            if (frozen is None or frozen[0] is not review or frozen[2] is not review.get('session') or
                    frozen[1] != {k:v for k,v in review.items() if k != 'session'}):
                raise ValueError('动作审阅已取消、修改或使用；请重新按方向键')
            node, session = review['node'], review['session']
            self._allowed(node)
            if (session is not self.manual_sessions.get(node) or self.settings != review['settings'] or
                    self.next_velocity != review['velocity'] or self.manual_velocity != review['active_velocity'] or self.od_reason != review['od_reason'] or
                    self.origin != review['origin'] or self.requested_rpm != review['requested_rpm'] or
                    self.software_zero != review['software_zero'] or self.linear != review['linear'] or
                    self.coordinate_revision != review['coordinate_revision'] or
                    self.mm_per_motor_revolution != review['conversion'] or self.step_mm != review['step_mm'] or
                    self.step_degrees != review['step_degrees']):
                raise ValueError('会话/设置已变化；审阅失效')
            self._configuration(out)
            if self.context != review['context'] or self.od != review['od']:
                raise ValueError('接口/OD已变化')
            session.cancel_event = out.cancel_event
            try:
                values, cal = self._snapshot(session, (review['args'], review['start']))
                self._validate(values, cal, review['args'], review['start'], stationary=True,
                               allow_hold=review['kind'] == 'hold')
                if cal != review['calibration'] or any(values[k] != review['before'][k] for k in FIXED + ('target_position','position_demand','controlword')):
                    raise ValueError('标定/受保护参数/目标在审阅后变化')
                # The explicit confirmation includes disabling any prior
                # stopped/Halt proof; no old residual target can be enabled.
                if node in self.proofs:
                    self._proof_guard(node, values, cal, stationary=True)
                    if self.proofs[node]['phase'] == 'running':
                        self.safety_action('stop', out)
                        out.check_cancel()
                    self.safety_action('disable', out)
                else:
                    p = self.tool._canopen
                    state = self._state(values)
                    if state != self._state(review['before']) or state not in (p.CIA402_DISABLED,p.CIA402_READY,p.CIA402_SWITCHED_ON):
                        raise ValueError('审阅后使能状态变化；请Space/x停止并重新审阅')
                    if state != p.CIA402_DISABLED:
                        self.safety_action('disable',out)
                out.check_cancel()
                self.manual_velocity = review['velocity']
                # Reuse all original submission guards and ACK/6062 checks.
                # The target AND original start come from the sealed review,
                # not from the refreshed position after an internal stop.
                stage = super().review('absolute', node, review['target'], out,
                    _hold=review['kind']=='hold', _start=review['start'])
                if stage['calibration'] != review['calibration'] or any(stage['before'][k] != review['before'][k] for k in FIXED):
                    raise ValueError('内部提交前标定/受保护参数变化')
                super().execute(stage, out)
                out.check_cancel()
                stage = super().review('enable', node, None, out)
                super().execute(stage, out)
                if review['kind'] != 'hold':
                    out.check_cancel()
                    stage = super().review('go', node, None, out)
                    super().execute(stage, out)
                out.info('本次人工动作已执行；不会自动重复')
            except BaseException as exc:
                if (self.context[0],node) not in self.blocked and (node in self.proofs or session.needs_stop or getattr(session.client,'quarantined',False)):
                    self._cleanup_failure(node, session, exc, out)
                raise
            finally:
                session.cancel_event = None

    def safety_action(self, kind, out):
        """Independent stop/disable, without stale telemetry or motion consent.

        No connection is opened here. Quarantine forbids even cleanup retries.
        """
        with self.lock:
            self.cancel_review()
            if self.context is None or self.node not in self.manual_sessions:
                return
            self._allowed(self.node)
            session = self._session(self.node, out)
            session.cancel_event = None
            try:
                self.enabled_sessions[self.node] = session
                getattr(session, 'stop' if kind == 'stop' else 'disable')()
                if kind == 'stop' and self.node in self.proofs:
                    self.proofs[self.node]['phase'] = 'stopped'
                elif kind == 'disable':
                    self.proofs.pop(self.node, None)
                values, cal = self._snapshot(session)
                if self.node in self.proofs:
                    self._proof_guard(self.node, values, cal)
                self._put(dict(self.row(self.node), values=values, calibration=cal, updated=time.monotonic(),lost=False))
                out.info('软件快停已确认；机械停止仍未知' if kind == 'stop' else '失能已确认；机械停止仍未知')
            except BaseException as exc:
                self._cleanup_failure(self.node, session, exc, out)
                raise


class SimpleApp(PmpApp):
    title = 'PMP4010 小角度手动测试'

    def __init__(self, tool, args):
        super().__init__(tool, args)
        self.controller = SimpleController(tool, args)
        if self.controller.linear:
            self.title = 'PMP4010 直线机械测程（mm）'
        self.initial_snapshot = False
        self.current_view = 'rt'
        self.edit_kind = None
        self.draft = ''
        self.urgent = None
        self._help_return = 'main'
        self.log = LogView(max_lines=2000)
        self.protocol_log = LogView(max_lines=2000)
        self.progress = '等待连接授权；尚未打开总线'

    def dispatch_key(self, name, key):
        # Safety keys own every view, including an unfinished numeric draft.
        return self.on_key(name, key)

    def _clear(self):
        self.pending = None
        self.controller.cancel_review()
        self.draft = ''
        self.edit_kind = None
        self.current_view = ('rt' if self.controller.context is None else
                             'authorize' if self.controller.origin is None else 'main')

    def _safety(self, kind):
        self._clear()
        self.cancel.set()
        self.refresh_cancel.set()
        if kind == 'quit':
            self.exit_after = True
            self.urgent = None
        else:
            self.urgent = kind
        self.progress = '已请求安全退出' if kind == 'quit' else '已请求快停' if kind == 'stop' else '已请求失能'
        return False

    def on_key(self, name, key):
        if name in (' ', 'space', 'x') or key == 32:
            return self._safety('stop')
        if name == 'q' or key == 3:
            return self._safety('quit')
        if self.exit_after or self.urgent or self.busy:
            return False
        try:
            if name == 'esc':
                self._clear()
            elif name == '?':
                if self.current_view == 'help':
                    self.current_view = self._help_return
                else:
                    self._help_return = self.current_view
                    self.current_view = 'help'
            elif self.current_view == 'help':
                if name == 'enter':
                    self.current_view = self._help_return
            elif self.current_view == 'rt' and name == 'enter':
                self.controller.rt_ack = True
                self.current_view = 'connecting'
                self._launch('scan', self.controller.scan)
            elif self.current_view == 'authorize' and name == 'enter':
                self.controller.authorize()
                self.current_view = 'main'
                self.progress = '会话授权完成；可s设速度、a设步长，再f/b预览'
            elif self.current_view == 'confirm' and name == 'enter':
                review = self.pending
                self.pending = None
                self.current_view = 'main'
                execute = self.controller.execute if self.action == 'disable' else self.controller.execute_action
                self._launch('execute', lambda out:execute(review,out))
            elif self.current_view == 'edit':
                if name == 'enter':
                    kind, text = self.edit_kind, self.draft
                    if kind in ('relative','absolute'):
                        self.action = kind
                        self._launch('review',lambda out:self.controller.review_action(kind,out,text))
                    elif kind == 'conversion':
                        self._clear()
                        self._launch('coordinate',lambda out:self.controller.set_coordinate(kind,out,text))
                    else:
                        self.controller.set_value(kind,text)
                        self._clear()
                elif name in ('backspace','delete') or key in (8,127):
                    self.draft = self.draft[:-1]
                elif len(name)==1 and name in '0123456789./+-':
                    self.draft += name
            elif self.current_view == 'main':
                if name in ('s','a','r','j','p') or name == 'c' and self.controller.linear:
                    self.controller.cancel_review()
                    self.edit_kind = {'s':'speed','a':'step','r':'relative' if self.controller.linear else 'range',
                                      'j':'relative','p':'absolute','c':'conversion'}[name]
                    self.draft = ''
                    self.current_view = 'edit'
                elif name in ('f','b','e'):
                    kind = {'f':'forward','b':'backward','e':'hold'}[name]
                    self.action = kind
                    self._launch('review',lambda out:self.controller.review_action(kind,out))
                elif name == 'd':
                    self.action = 'disable'
                    self._launch('review',lambda out:self.controller.review('disable',self.controller.node,None,out))
                elif name == 'z':
                    self.pending = None
                    self._launch('coordinate',lambda out:self.controller.set_coordinate('zero',out))
        except (ValueError,RuntimeError) as exc:
            self.progress = str(exc)
            self.log.append(str(exc),'err')
        return False

    def poll(self):
        for _ in range(256):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'done':
                _,slot,label,result,error = event
                worker = getattr(self,slot)
                if worker:
                    worker.join(0)
                setattr(self,slot,None)
                if error:
                    self.progress = '事务已取消；请核对停止状态' if isinstance(error,KeyboardInterrupt) else str(error)
                    self.log.append(self.progress,'err')
                elif label == 'scan':
                    self.current_view = 'authorize'
                    self.progress = '只读连接完成；等待会话授权'
                elif label == 'review' and not self.urgent and not self.exit_after:
                    self.pending = result
                    self.current_view = 'confirm'
                    self.progress = '请核对本次动作；Enter确认，Esc取消'
                elif label == 'review':
                    self.controller.cancel_review()
                elif label == 'shutdown':
                    self._shutdown_recovery()
                elif label == 'coordinate':
                    self.progress = '软件坐标设置完成；未发送运动或参数下载'
                elif label != 'refresh':
                    self.progress = '本次指令已提交；请观察实时反馈'
            elif event[0] == 'event' and event[1] in ('manual_result','bench_result'):
                self.log.append(event[2].get('message','动作保护已触发；请核对停止状态'))
        for _ in range(128):
            try:
                _,level,line = self.logs.get_nowait()
                self.protocol_log.append(line,level)
                if not line.startswith(('已放行 OD 闸门：','放行条目：','其中 assumed','放行理由：',
                                        '本次放行**','现场停机由独立安全设计负责')):
                    self.log.append(line,level)
            except queue.Empty:
                break
        if self.exit_after:
            if self.worker is None and self.refresh_worker is None:
                if self.controller.enabled_sessions and self.controller.shutdown_results is None and not self._finalizing:
                    self._launch('shutdown',self.controller.close)
                else:
                    self.controller.close()
                    self._running = False
            return
        if self.worker is None and self.refresh_worker is None and self.urgent:
            kind,self.urgent = self.urgent,None
            self._launch(kind,lambda out:self.controller.safety_action(kind,out))
            return
        row = self.controller.row(self.controller.node)
        if row and row['quarantined']:
            self.pending = None
            self.controller.cancel_review()
            self.current_view = 'main'
            self.progress = row['quarantine_reason']
        if (self.controller.context is not None and not self.busy and self.refresh_worker is None and
                time.monotonic() >= self.next_refresh and not self.controller.blocked):
            self.next_refresh = time.monotonic()+.5
            self._start_refresh()

    def hints(self):
        return ['s 速度','a 步长','r 相对' if self.controller.linear else 'j 相对',
                'p 绝对','z 软件零点','c 换算' if self.controller.linear else 'r 范围',
                'e 使能保持','f/b 点动','Space/x 快停','d 失能','q 退出','? 帮助']

    def build(self):
        grid = Grid(self.rows,self.cols,strict=(self.box!='ascii'))
        draw_box(grid,Rect(0,0,self.rows,self.cols),self.title,self.box)
        c = self.controller
        row = c.row(c.node)
        v = row['values'] if row else {}
        speed = '未知'
        if v.get('velocity_feedback_unit') == 0:
            speed = '{} rpm'.format(v['actual_velocity'])
        elif v.get('velocity_feedback_unit') == 1 and row.get('calibration'):
            try:
                speed = '{} rpm'.format(number(position.unit_to_velocity(v['actual_velocity'],row['calibration'])))
            except ValueError:
                speed = '换算不可用'
        online = '已隔离' if row and row['quarantined'] else '在线' if row and row['online'] and not row['lost'] else '未连接'
        state = '未知'
        if v and row and not row['lost'] and not row['quarantined']:
            p = c.tool._canopen
            state = {p.CIA402_DISABLED:'未使能',p.CIA402_READY:'就绪，未使能',
                p.CIA402_SWITCHED_ON:'上电，未使能',p.CIA402_QUICK_STOP:'快停状态',
                p.CIA402_ENABLED:'使能，Halt保持' if v['controlword'] & 0x100 else
                '使能，运动中' if v['actual_velocity'] else '使能，零速'}.get(c._state(v),'未知')
            if v['statusword'] & p.CIA402_FAULT or v['error_code_object']:
                state = '故障'
        historical = bool(row and (row['lost'] or row['quarantined']))
        lines = (['节点 {}  {}  当前实读状态未知'.format(c.node,online),
            '最后有效采样：位置 {} U  速度 {}  故障 {}'.format(v.get('actual_position','-'),speed,
                '0x{:04X}'.format(v['error_code_object']) if v else '未知')] if historical else
            ['节点 {}  {}  位置 {} U  实读：{}'.format(c.node,online,v.get('actual_position','-'),state),
             '实际速度 {}  故障 {}'.format(speed,'0x{:04X}'.format(v['error_code_object']) if v else '未知')])
        if row and row['calibration']:
            try:
                step,degrees,rpm = c.quantities(row)
                lines += ['下次步长 请求 {} -> 实际 {} {}（{} U）'.format(number(c.step),number(degrees),c.unit,step),
                    '下次速度 请求 {} -> 实际 {} rpm（{} U/s）'.format(number(c.requested_rpm)+' rpm' if c.requested_rpm is not None else str(c.next_velocity)+' U/s',number(rpm),c.next_velocity)]
            except ValueError as exc:
                lines += [str(exc)]
        if c.origin is not None:
            if c.linear:
                lines += ['软件行程未设限；驱动限位/Int32取交 [{}，{}] U'.format(c.settings['soft_min'],c.settings['soft_max'])]
            else:
                lines += ['会话原点 {} U；固定范围 [{}，{}] U（请求+/-{}度，与设备行程取交）'.format(c.origin,c.settings['soft_min'],c.settings['soft_max'],number(c.range_degrees))]
            try:
                scale = c.coordinate_scale(row['calibration'])
                lines += ['{}软件坐标 {} {}；零点 {} U{}'.format('历史' if historical else '当前',number((v['actual_position']-c.software_zero)*scale),c.unit,c.software_zero,
                        '；{} mm/电机轴转'.format(number(c.mm_per_motor_revolution)) if c.linear else ''),
                    '允许软件坐标 [{}，{}] {}'.format(number((c.settings['soft_min']-c.software_zero)*scale),number((c.settings['soft_max']-c.software_zero)*scale),c.unit)]
            except ValueError:
                lines += ['直线换算未设置；软件零点 {} U；c设置mm/电机轴转'.format(c.software_zero)]
        lines += ['提示：'+self.progress]
        failure = (row or {}).get('failure_diagnostic')
        if historical and failure and failure.get('raw_statusword') is not None:
            lines += ['诊断样本（历史）：CW=0x{:04X} 6041=0x{:04X} {}'.format(
                failure['controlword'],failure['raw_statusword'],failure.get('decoded_state',''))]
        if self.current_view == 'rt':
            lines += ['', '连接授权：现场人工直线机械测程；RT控制器可能占用总线。' if c.linear else
                '连接授权：现场人工小角度测试；RT控制器可能占用总线。',
                '请先协调总线占用与现场安全。Enter 同意本会话连接；q 退出。',
                '连接仅扫描节点并读取，不会自动使能或发送运动。']
        elif self.current_view == 'authorize':
            gaps = [e['key'] for e in (c.od or {}).get('entries',[]) if e['status']!='verified']
            lines += ['', '运动会话授权：接受以下OD未核对项及独立停机责任：']
            # Wrap by item, retaining identifiers and making every gap visible.
            lines += ['  '+', '.join(gaps[i:i+2]) for i in range(0,len(gaps),2)]
            if v:
                if c.linear:
                    lines += ['软件行程未设限；仅驱动607Dh、607A/6063 Int32保护。',
                        'Enter 授权人工测程并初始化软件零点；'+(
                            'c设置换算后才能动作。' if c.mm_per_motor_revolution is None else
                            '换算 {} mm/电机轴转。'.format(number(c.mm_per_motor_revolution)))]
                else:
                    units = int(Fraction(1)/Fraction(row['calibration']['motor_degrees_per_user_unit']))
                    lines += ['Enter 冻结当前原点 {} U；测试范围 [{}，{}] U（+/-1度与607Dh取交）。'.format(v['actual_position'],max(v['actual_position']-units,v['software_position_limit']),min(v['actual_position']+units,v['software_position_limit_max'])),
                        '确认仅限本会话人工点动；不修改OD可信度。q 退出。']
        elif self.current_view == 'confirm' and self.pending:
            lines += ['']+(['失能：确认后撤销当前使能/目标证明；机械停止仍未知。',
                'Enter 确认失能；Esc 取消；Space/x 随时快停'] if self.action == 'disable' else self.pending['lines'])
        elif self.current_view == 'edit':
            label = {'speed':'速度 rpm','step':'步长 '+c.unit,'range':'原点两侧测试半范围 电机轴度（<=1）',
                'relative':'相对位移 '+c.unit,'absolute':'相对软件零点的绝对位置 '+c.unit,
                'conversion':'每电机轴转对应直线mm（正数，可用分数；先d失能）'}[self.edit_kind]
            lines += ['',label+'：'+self.draft+'_','Enter 审阅目标' if self.edit_kind in ('relative','absolute') else 'Enter 提交设置',
                'Esc 取消；位移量化向零取整；Space/x 随时快停。']
            if self.edit_kind in ('relative','absolute') and self.draft:
                try:
                    requested = signed(self.draft)
                    scale = c.coordinate_scale(row['calibration'])
                    base = v['actual_position'] if self.edit_kind == 'relative' else c.software_zero
                    target = base+int(requested/scale)
                    lines += ['草稿607A={} U；软件目标 {} {}'.format(target,number((target-c.software_zero)*scale),c.unit)]
                    if not c.settings['soft_min'] <= base+requested/scale <= c.settings['soft_max']:
                        lines += ['草稿超出允许行程，禁止执行。']
                    elif target == v['actual_position']:
                        lines += ['量化后位移为零，禁止执行。']
                except ValueError as exc:
                    lines += [str(exc)]
        elif self.current_view == 'help':
            lines = lines[:2]+['', 's：电机轴rpm；a：点动步长（默认0.1'+c.unit+'）。',
                'r/j：相对位移；p：软件绝对位置（mm）。c：mm/电机轴转，先d失能。' if c.linear else
                'j：相对位移；p：软件绝对位置（电机轴度）；r：固定原点半范围<=1度。',
                'z：新鲜采样设本会话软件零点；不运动、不写607Ch、不回零/保存。',
                'z只改坐标；原物理目标/保护继续监控，不能绕过限位。',
                '位移输入Enter审阅冻结目标，再Enter执行一次；Esc取消。',
                's/a/c/z改变使旧审阅失效；量化向零，零位移拒绝。',
                'e：冻结当前位置，确认后提交保持目标并使能，Halt保持。',
                'f/b：冻结正/反向终点，Enter只执行这一次；Esc取消。非零速先Space。',
                '动作：提交→012F保持Halt→重写冻结607A→002F/003F/002F。',
                'Space/x：任何页面立即请求快停；d失能；q安全退出。',
                '故障/跟随误差/越界立即锁定；通信隔离后不重试。',
                '软件停止不能替代独立急停；机械停止/抱闸仍须现场确认。',
                'Enter/? 返回；Space/x/q始终可用。']
        elif self.current_view == 'main':
            proof = c.proofs.get(c.node)
            lines += ['','可操作：'+('保护已锁定，请退出并外部恢复' if c.blocked else
                '实际速度非零；请先Space/x停止' if v.get('actual_velocity') else
                '先按f/b/e或输入位置审阅，再Enter确认' if c.origin is not None else '等待连接/会话授权')]
        bottom = max(1,self.rows-7)
        for i,line in enumerate(lines[:max(0,bottom-1)]):
            grid.put(i+1,2,fit(line,max(0,self.cols-4)))
        if self.rows>=9:
            draw_hsep(grid,bottom,1,max(0,self.cols-2),self.box,'日志')
            self.log.render(grid,Rect(bottom+1,2,2,max(0,self.cols-4)),self.box)
            grid.put(self.rows-3,2,fit('f/b 点动  r/j 相对  p 绝对  z 软件零点  c 换算' if c.linear else
                'f/b 点动  j 相对  p 绝对  z 软件零点  r 范围',max(0,self.cols-4)))
            grid.put(self.rows-2,2,fit('s 速度 a 步长 e 保持 d 失能 Space/x 快停 q 退出 ? 帮助',max(0,self.cols-4)))
        return grid
