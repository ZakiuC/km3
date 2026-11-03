"""One-screen PP jog UI; every confirmed action uses ManualController's proof.

No connection, approval, target write or enable is implicit in construction.
The session origin and the reviewed absolute target never follow live feedback.
"""
import copy
from fractions import Fraction
import queue
import threading
import time

import pmp4010_position as position
from pmp4010_manual import ManualController, FIXED
from pmp4010_tui import PmpApp, Grid, Rect, LogView, draw_box, draw_hsep, fit


def positive(text):
    try:
        value = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        raise ValueError('请输入有限正数') from None
    if value <= 0:
        raise ValueError('请输入有限正数')
    return value


def number(value):
    return '{:.8g}'.format(float(value))


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
        self.session_calibration = None
        self.range_degrees = Fraction(1)
        self.step_degrees = Fraction('0.1')
        self.requested_rpm = None  # Preserve explicit legacy --manual-velocity U/s.
        self.next_velocity = self.manual_velocity
        self._simple_review = None

    def cancel_review(self):
        self._simple_review = None
        self.discard_prepare()

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
            self._set_range(self.range_degrees, row)
            self.od_reason = '操作者明确确认：本会话小角度人工测试，接受所列OD未核对项及独立停机责任'

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
                units = int(value / Fraction(row['calibration']['motor_degrees_per_user_unit']))
                if units < 1:
                    raise ValueError('步长不足1个用户单位，无法执行')
                if value > 1 or units > self.settings['max_displacement']:
                    raise ValueError('步长超过本会话单动作范围或1度硬顶')
                self.step_degrees = value
            else:
                raise ValueError('未知设置')
            self.cancel_review()

    def quantities(self, row):
        cal = row['calibration']
        degree_unit = Fraction(cal['motor_degrees_per_user_unit'])
        step = int(self.step_degrees / degree_unit)
        return step, step*degree_unit, position.unit_to_velocity(self.next_velocity, cal)

    def review_action(self, kind, out):
        with self.lock:
            self.cancel_review()
            if kind not in ('forward', 'backward', 'hold') or self.origin is None or not self.od_reason:
                raise ValueError('请先确认本会话运动授权')
            row = self._fresh(self.node)
            v, cal = row['values'], row['calibration']
            if cal != self.session_calibration:
                raise ValueError('标定与授权时不同；请退出重新核对')
            proof = self.proofs.get(self.node)
            if proof:
                self._proof_guard(self.node, v, cal, stationary=True)
            step, degrees, rpm = self.quantities(row)
            if step < 1:
                raise ValueError('步长不足1个用户单位，无法执行')
            start = v['actual_position']
            target = start + (step if kind == 'forward' else -step if kind == 'backward' else 0)
            a = self._args(row, target)
            a.velocity = self.next_velocity
            self._validate(v, cal, a, start, stationary=True, allow_hold=kind == 'hold')
            p = self.tool._canopen
            state = self._state(v)
            if state not in (p.CIA402_DISABLED,p.CIA402_READY,p.CIA402_SWITCHED_ON) and proof is None:
                raise ValueError('设备已在外部使能；请先Space/x停止并d失能')
            if not self.tool.refuse_motion(out, '人工小角度点动', a):
                raise ValueError('OD闸门关闭')
            review = dict(kind=kind, node=self.node, start=start, target=target, args=a,
                session=self._session(self.node, out), calibration=copy.deepcopy(cal), before=copy.deepcopy(v),
                settings=copy.deepcopy(self.settings), velocity=self.next_velocity, active_velocity=self.manual_velocity, od=copy.deepcopy(self.od),
                od_reason=self.od_reason, context=self.context, origin=self.origin,
                requested_rpm=self.requested_rpm, step_degrees=self.step_degrees,
                lines=['保持当前位置并使能（Halt保持）' if kind == 'hold' else '正向点动' if kind == 'forward' else '反向点动',
                    '冻结起点 {} U -> 终点 {} U；位移 {} U = {} 度'.format(start,target,target-start,number(abs(target-start)*Fraction(cal['motor_degrees_per_user_unit']))),
                    '请求步长 {} 度 -> 实际 {} 度（{} U）'.format(number(self.step_degrees),number(degrees),step),
                    '请求速度 {} -> 实际 {} rpm（{} U/s）'.format(number(self.requested_rpm)+' rpm' if self.requested_rpm is not None else str(self.next_velocity)+' U/s',number(rpm),self.next_velocity),
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
        self.initial_snapshot = False
        self.current_view = 'rt'
        self.edit_kind = None
        self.draft = ''
        self.urgent = None
        self._help_return = 'main'
        self.log = LogView(max_lines=2000)
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
                    self.controller.set_value(self.edit_kind,self.draft)
                    self._clear()
                elif name in ('backspace','delete') or key in (8,127):
                    self.draft = self.draft[:-1]
                elif len(name)==1 and name in '0123456789./+-':
                    self.draft += name
            elif self.current_view == 'main':
                if name in ('s','a','r'):
                    self.edit_kind = {'s':'speed','a':'step','r':'range'}[name]
                    self.draft = ''
                    self.current_view = 'edit'
                elif name in ('f','b','e'):
                    kind = {'f':'forward','b':'backward','e':'hold'}[name]
                    self.action = kind
                    self._launch('review',lambda out:self.controller.review_action(kind,out))
                elif name == 'd':
                    self.action = 'disable'
                    self._launch('review',lambda out:self.controller.review('disable',self.controller.node,None,out))
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
                elif label == 'review':
                    self.controller.cancel_review()
                elif label == 'shutdown':
                    self._shutdown_recovery()
                elif label != 'refresh':
                    self.progress = '操作完成；请观察实时反馈'
            elif event[0] == 'event' and event[1] in ('manual_result','bench_result'):
                self.log.append(str(event[2]))
        for _ in range(128):
            try:
                _,level,line = self.logs.get_nowait()
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
        return ['s 速度','a 步长','r 范围','e 使能保持','f/b 点动','Space/x 快停','d 失能','q 退出','? 帮助']

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
        lines = ['节点 {}  {}  位置 {} U'.format(c.node,online,v.get('actual_position','-')),
            '实际速度 {}  故障 {}'.format(speed,'0x{:04X}'.format(v['error_code_object']) if v else '未知')]
        if row and row['calibration']:
            try:
                step,degrees,rpm = c.quantities(row)
                lines += ['下次步长 请求 {} -> 实际 {} 电机轴度（{} U）'.format(number(c.step_degrees),number(degrees),step),
                    '下次速度 请求 {} -> 实际 {} rpm（{} U/s）'.format(number(c.requested_rpm)+' rpm' if c.requested_rpm is not None else str(c.next_velocity)+' U/s',number(rpm),c.next_velocity)]
            except ValueError as exc:
                lines += [str(exc)]
        if c.origin is not None:
            lines += ['会话原点 {} U；固定范围 [{}，{}] U（请求+/-{}度，与设备行程取交）'.format(c.origin,c.settings['soft_min'],c.settings['soft_max'],number(c.range_degrees))]
        lines += ['状态：'+self.progress]
        if self.current_view == 'rt':
            lines += ['', '连接授权：现场人工小角度测试；RT控制器可能占用总线。',
                '请先协调总线占用与现场安全。Enter 同意本会话连接；q 退出。',
                '连接仅扫描节点并读取，不会自动使能或发送运动。']
        elif self.current_view == 'authorize':
            gaps = [e['key'] for e in (c.od or {}).get('entries',[]) if e['status']!='verified']
            lines += ['', '运动会话授权：接受以下OD未核对项及独立停机责任：']
            # Wrap by item, retaining identifiers and making every gap visible.
            lines += ['  '+', '.join(gaps[i:i+2]) for i in range(0,len(gaps),2)]
            if v:
                units = int(Fraction(1)/Fraction(row['calibration']['motor_degrees_per_user_unit']))
                lines += ['Enter 冻结当前原点 {} U；测试范围 [{}，{}] U（+/-1度与607Dh取交）。'.format(v['actual_position'],max(v['actual_position']-units,v['software_position_limit']),min(v['actual_position']+units,v['software_position_limit_max'])),
                    '确认仅限本会话人工点动；不修改OD可信度。q 退出。']
        elif self.current_view == 'confirm' and self.pending:
            lines += ['']+(['失能：确认后撤销当前使能/目标证明；机械停止仍未知。',
                'Enter 确认失能；Esc 取消；Space/x 随时快停'] if self.action == 'disable' else self.pending['lines'])
        elif self.current_view == 'edit':
            label = {'speed':'速度 rpm','step':'步长 电机轴度','range':'原点两侧测试半范围 电机轴度（<=1）'}[self.edit_kind]
            lines += ['',label+'：'+self.draft+'_','Enter 提交；Esc 取消；量化向零取整，实际值将在同屏与动作预览显示。']
        elif self.current_view == 'help':
            lines += ['', 's：设置电机轴rpm；a：点动步长（默认0.1度）；r：修改固定原点两侧范围（<=1度）。',
                's/a仅改下次动作草稿；确认后先安全停止/失能再应用。',
                'r仅在d失能且无目标证明时生效，原点不会重置。',
                'e：冻结当前位置，确认后提交保持目标并使能，Halt保持。',
                'f/b：冻结正/反向终点，Enter只执行这一次；Esc取消。非零速先Space。',
                '方向动作内部完成安全提交、新ACK核证、保持Halt使能、解除Halt。',
                'Space/x：任何页面立即请求快停；d失能；q安全退出。',
                '忙时停止会取消事务并有界收尾，取消的动作不会排队重放。',
                '故障/跟随误差/越界立即锁定；通信隔离后不重试。',
                '软件停止不能替代独立急停；机械停止/抱闸仍须现场确认。',
                'Enter/? 返回；Space/x/q始终可用。']
        elif self.current_view == 'main':
            proof = c.proofs.get(c.node)
            lines += ['','可操作：'+('保护已锁定，请退出并外部恢复' if c.blocked else
                '实际速度非零；请先Space/x停止' if v.get('actual_velocity') else
                '先按f/b/e审阅，再Enter确认；到边界请反向点动' if c.origin is not None else '等待连接/会话授权')]
        bottom = max(1,self.rows-7)
        for i,line in enumerate(lines[:max(0,bottom-1)]):
            grid.put(i+1,2,fit(line,max(0,self.cols-4)))
        if self.rows>=9:
            draw_hsep(grid,bottom,1,max(0,self.cols-2),self.box,'日志')
            self.log.render(grid,Rect(bottom+1,2,2,max(0,self.cols-4)),self.box)
            grid.put(self.rows-3,2,fit('f/b 点动  s 速度  a 步长  e 使能保持  d 失能',max(0,self.cols-4)))
            grid.put(self.rows-2,2,fit('Space/x 快停  q 安全退出  ? 帮助  r 范围',max(0,self.cols-4)))
        return grid
