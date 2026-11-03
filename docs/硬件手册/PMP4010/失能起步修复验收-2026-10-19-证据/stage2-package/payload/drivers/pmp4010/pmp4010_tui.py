"""Chinese four-seat PMP4010 live bench; no I/O in rendering or key handling."""
import copy
import queue
import re
import threading
import time
from fractions import Fraction

from tui import (Field, Form, Grid, LogView, Rect, Table, TuiApp,
                 _display_width, draw_box, draw_hsep, fit)
from pmp4010_bench import BenchController, ACTIONS, action_policy, mode_text, state_text, velocity_unit

def chinese(text):
    """Preserve exact Chinese diagnostics and technical facts; translate known prose."""
    text = str(text)
    substitutions = (
        (' pre-read=', ' 写前读取='), (' readback=', ' 读回='),
        ('Backup or parameter plan changed since review; review again', '备份或参数计划在审阅后已变化；请重新审阅'),
        ('Target changed since review; review again', '目标在审阅后已变化；请重新审阅'),
        ('OD changed since review; review again', 'OD 在审阅后已变化；请重新审阅'),
        ('actual_position readback width mismatch', 'actual_position 读回宽度不符'),
        ('finite number required', '须填写有限数值'),
        ('invalid literal for int() with base 10:', '无法解析为十进制整数：'),
        ('could not convert string to float:', '无法解析为数值：'),
        ('int() argument must be a string, a bytes-like object or a real number, not',
         '整数输入类型无效：'),
    )
    for old, new in substitutions:
        text = text.replace(old, new)
    return text


class ChineseLog(LogView):
    def append(self, text, level='info'):
        text = re.sub(r'^started; terminal size resolved to (\d+x\d+)$',
                      r'已启动；终端尺寸 \1', chinese(text))
        super().append(text, level)


class ChineseField(Field):
    def commit(self):
        result = super().commit()
        if self.error:
            self.error = chinese(self.error)
        return result


class JobOutput:
    """Queue adapter: worker threads never touch curses or the display log."""
    def __init__(self, events, cancel, logs=None):
        self.events, self.cancel_event = events, cancel
        self.logs = events if logs is None else logs

    def event(self, name, data):
        self.events.put(('event', name, data))

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise KeyboardInterrupt()

    def _line(self, text, level='info'):
        # Raw receive streams may be faster than terminal input. Drop display
        # logs rather than block transport or grow memory without a bound.
        try:
            self.logs.put_nowait(('log', level, chinese(text)))
        except queue.Full:
            self.dropped = getattr(self, 'dropped', 0) + 1

    info = plain = title = cmd = _line
    def ok(self, text): self._line(text, 'ok')
    def warn(self, text): self._line(text, 'warn')
    def bad(self, text): self._line(text, 'err')
    def field(self, key, value): self._line('{}: {}'.format(key, value))
    def c(self, name, text): return str(text)


HELP = (
    '测试台：固定四席，按实际扫描到的节点填入；未分配席位显示离线。本页为帮助。',
    '启动：F5 显式确认 RT 总线占用风险后，自动发 SDO 身份上传扫描一次。',
    '主界面：Up/Down 或 j/k 选电机；Enter 详情；S 扫描；R 刷新；D 全部失能；X 全部快停。',
    '批量仅处理当前在线未隔离节点；跳过项明示；须审阅并输入含动作与节点列表的确认串。',
    '详情：A 绝对；R 相对；Z 方法35设零；E 使能；D 失能；X 快停；P 失能准备；B 未使能起步核证。',
    '详情刷新用 F6；R 专用于相对位置，避免同键歧义。',
    'F2：逐项输入脚本行程下限、上限、人工单次位移预算，每屏只填一个数。',
    '动作：A/R先填位置；含P失能准备均F5审阅、精确确认串、F5执行；P后重新审阅运动。',
    'F5 自动提交当前草稿；Enter 也可提交；编辑中 ? 是普通字符。',
    '首次动作需显式确认本会话 OD 旁路；默认只读，不继承命令行旁路理由。',
    '相对位置＝静止快照6064＋偏移；只允许 target reached 且606C=0。',
    '执行会重新读取并核对基准；准备期间也监视漂移，绝不静默重算目标。',
    'PP轮廓实读并校验上限；P仅607A对齐起点、6081降100，仍失能且不保证bit10置位。',
    'B未使能起步核证：bit3=0、Halt=1，仅到Switched on；固件可能拒绝，不升级使能，不允许位置漂移。',
    '保持Halt；不解除暂停；结束失能。只核证当前位置握手，不代表真实位移验收。',
    '设零点只用方法35，不写607Ch/DI；DI配置需求未验证，607Ch非零则拒绝。',
    '设零点须新 bit10+bit12 完成与6064=0读回；bit15不作通用状态或成功判据。',
    '设零点后，旧坐标的脚本行程和预算失效，必须重新输入。',
    'PV/CSV 使能须审阅并预写零速度；非零速度操作未开放。',
    '转矩显示额定转矩百分比，非N·m。',
    '实时对象含6064用户单位、606C实际单位选择；刷新约2Hz，失败保留旧值。',
    'SDO超时即隔离该接口/节点；需外部恢复，本工具不自动重连、不重试绕过隔离。',
    'Esc中止；P仅停止并留部分写证据，其余等待快停/失能；q/Ctrl-C等后台结束退出。',
    'PgUp/PgDn 翻日志；g 回到末尾；? 或 F1 帮助；帮助中 Esc/q 返回原焦点。',
)
SAFETY = ('安全：工具侧软行程与电机轴1度硬顶；607Dh不能替代机械保护。',
          '中止清理若读状态失败，必须按状态未知处理；使用独立急停。')
SETTINGS = (('soft_min', '脚本行程下限 U'), ('soft_max', '脚本行程上限 U'),
            ('max_displacement', '人工单次位移上限 U（另限1度）'))


class QuietOutput:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class PmpApp(TuiApp):
    title = 'PMP4010 测试台'
    log_rows = 4
    menu_width = 0
    VIEWS = ('overview', 'detail', 'action', 'confirm', 'rt', 'od', 'setting', 'help')

    def __init__(self, tool, args):
        super().__init__(rows=args.rows, cols=args.cols, box='ascii' if args.ascii else None, poll_ms=50)
        self.tool, self.args = tool, copy.copy(args)
        self.controller = BenchController(tool, args)
        self.log = ChineseLog(max_lines=2000)
        self.help_log = LogView(max_lines=500)
        for line in HELP:
            self.help_log.append(line)
        # Before first render LogView has no measured total for goto_start().
        self.help_log.scroll(self.help_log.max_lines)
        self.current_view = 'rt'
        self.selected = 0
        self.worker = self.refresh_worker = None
        self.events = queue.Queue()
        self.logs = queue.Queue(maxsize=2000)
        self.cancel = threading.Event()
        self.refresh_cancel = threading.Event()
        self.pending = None
        self.confirm = None
        self.input_field = None
        self.action = None
        self.progress = '默认只读；等待启动确认'
        self.recovery = '尚无动作；设备状态未测量'
        self.initial_snapshot = True
        self.exit_after = False
        self.next_refresh = 0
        self._setting_index = 0
        self._setting_draft = {}
        self._resume_action = None
        self._help_return = None
        self._result_seen = False
        self._finalizing = False
        self.log.append('启动需确认 RT 占用；只读扫描也会发 SDO 上传请求。')
        self.log.append('动作默认关闭；F2 人工设置行程与单次位移预算。')

    @property
    def busy(self):
        return self.worker is not None

    @property
    def selected_row(self):
        return self.controller.rows[self.selected]

    def _field(self, label, value='', kind='text'):
        self.input_field = ChineseField('value', str(value), label=label, kind=kind)
        self.set_form(Form([self.input_field]))
        self.input_field.begin_edit()

    def _launch(self, label, function, refresh=False):
        slot = 'refresh_worker' if refresh else 'worker'
        if getattr(self, slot) is not None:
            return
        cancel = threading.Event()
        if refresh:
            self.refresh_cancel = cancel
        else:
            self.cancel = cancel
            self.progress = {'scan': '扫描中；界面仍可操作', 'execute': '动作执行中', 'review': '审阅中', 'shutdown': '退出失能确认中'}.get(label, label)
        out = JobOutput(self.events, cancel, self.logs)
        def job():
            result = error = None
            try:
                result = function(out)
            except BaseException as exc:
                error = exc
            self.events.put(('done', slot, label, result, error))
        worker = threading.Thread(target=job, name='pmp4010-' + label)
        setattr(self, slot, worker)
        worker.start()

    def _start_scan(self):
        if not self.busy:
            self._launch('scan', self.controller.scan)

    def _start_refresh(self):
        if not self.busy:
            self._launch('refresh', self.controller.refresh, refresh=True)

    def _requirements(self, action):
        """The same data-driven prerequisites guard entry and review."""
        policy = action_policy(action)
        if not policy['confirm']:
            return False
        if policy['batch']:
            nodes, skipped = self.controller.batch_selection()
            if not nodes:
                for line in skipped:
                    self.log.append(line, 'warn')
                self.log.append('无在线节点；请先扫描', 'err')
                return False
        elif self.selected_row['node'] is None:
            self.log.append('该席位未分配节点；请先扫描', 'err')
            return False
        if policy['od'] and not self.controller.od_reason:
            self.current_view = 'od'
            self._field('输入 OD 会话确认串')
            return False
        if policy['settings'] and any(v is None for v in self.controller.settings.values()):
            self._start_settings(action)
            return False
        return True

    def _od_token(self):
        policy = action_policy(self._resume_action) if self._resume_action else None
        if policy and policy['batch']:
            nodes, _ = self.controller.batch_selection()
            target = 'ALL ' + ','.join(map(str, nodes))
        else:
            target = self.selected_row['node']
        return 'OD {} {}'.format(self.controller.context[0] if self.controller.context else '未知', target)

    def begin_action(self, action):
        if self.busy:
            return
        self.controller.discard_prepare()
        self.action = self._resume_action = action
        if not self._requirements(action):
            return
        policy = action_policy(action)
        self.current_view = 'action'
        self.pending = self.confirm = None
        if policy['input']:
            row = self.selected_row
            value = row['values'].get('actual_position', '') if action == 'absolute' else '0'
            self._field('绝对位置目标 U' if action == 'absolute' else '相对位置偏移 U（可负）', value, 'number')
        else:
            self.input_field = None
            self.set_form(None)

    def _start_settings(self, resume=None):
        if self.busy:
            return
        self._resume_action = resume
        self._setting_index = 0
        self._setting_draft = copy.copy(self.controller.settings)
        self.current_view = 'setting'
        self._show_setting()

    def _show_setting(self):
        key, label = SETTINGS[self._setting_index]
        value = self._setting_draft.get(key)
        self._field(label, '' if value is None else value, 'number')

    def _save_setting(self):
        key, _ = SETTINGS[self._setting_index]
        try:
            value = int(self.input_field.value)
            if key == 'max_displacement' and value <= 0:
                raise ValueError('人工单次位移上限必须为正整数')
            self._setting_draft[key] = value
            if self._setting_index == len(SETTINGS) - 1:
                if self._setting_draft['soft_min'] >= self._setting_draft['soft_max']:
                    raise ValueError('脚本行程下限必须小于上限；Esc 取消后重填')
                self.controller.settings.update(self._setting_draft)
                self.log.append('人工脚本行程与预算已记录；仍须实读标定核对1度硬顶')
                self.set_form(None)
                self.current_view = 'detail'
                if self._resume_action:
                    self.begin_action(self._resume_action)
            else:
                self._setting_index += 1
                self._show_setting()
        except ValueError as exc:
            self.log.append(chinese(exc), 'err')

    def _preview(self):
        try:
            if action_policy(self.action)['batch']:
                return self.controller.review_batch(self.action, QuietOutput(), check_gate=False), None
            value = self.input_field.text if self.input_field else 0
            return self.controller.review(self.action, self.selected_row['node'], value, QuietOutput(), check_gate=False), None
        except (ValueError, TypeError, KeyError) as exc:
            return None, chinese(exc)

    def review_action(self):
        if not self._requirements(self.action):
            return
        review, error = self._preview()
        if error:
            self.log.append('已阻止：' + error, 'err')
            return
        # Only this F5 freezes a single-use plan; rendering a preview never does.
        self.controller.freeze_prepare(review)
        self.pending = review
        self.confirm = dict(expected=review['token'], review=review)
        self.current_view = 'confirm'
        self._field('输入确认串')
        self.confirm['form'] = self.focused_form

    def _execute(self):
        if self.input_field.value != self.pending['token']:
            self.log.append('确认串不匹配；未执行', 'err')
            return
        review = self.pending
        self.pending = self.confirm = None
        self.set_form(None)
        self.input_field = None
        policy = action_policy(review['action'])
        self.current_view = 'overview' if policy['batch'] else 'detail'
        self._result_seen = False
        self.recovery = ('失能准备执行中；失败仅停止并保留部分写入证据' if review['action'] == 'prepare_pp'
                         else '执行中；结果读回与清理前状态未知')
        execute = self.controller.execute_batch if policy['batch'] else self.controller.execute
        self._launch('execute', lambda out: execute(review, out))

    def dispatch_key(self, name, key):
        # F5 is the documented single-key commit/review/execute path. Ordinary
        # action/help keys remain consumed by the active field draft.
        if name == 'f5' and self.focused_form and self.focused_form.editing:
            if not self.focused_form.current.commit():
                return False
        return super().dispatch_key(name, key)

    def poll(self):
        if not self.exit_after and self.initial_snapshot and self.controller.rt_ack:
            self.initial_snapshot = False
            self.current_view = 'overview'
            self._start_scan()
        for _ in range(256):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'done':
                _, slot, label, result, error = event
                worker = getattr(self, slot)
                if worker:
                    worker.join(0)
                setattr(self, slot, None)
                if label != 'refresh' or error:
                    self.progress = ('已中止；请核对清理状态' if isinstance(error, KeyboardInterrupt)
                                     else '失败：' + chinese(error) if error else '扫描完成' if label == 'scan' else '动作已完成；见结果读回')
                    self.log.append(self.progress, 'err' if error else 'info')
                if label == 'shutdown':
                    self._shutdown_recovery()
                if label == 'execute':
                    if not self._result_seen:
                        self.recovery = ('动作失败或中止；未确认的数据按状态未知处理' if error else
                                     '动作读回完成；机械停止与抱闸仍须独立确认')
                    self.next_refresh = 0
            elif event[0] == 'event':
                _, name, data = event
                if name in ('result', 'bench_result'):
                    self._result_seen = True
                    disabled = data.get('disabled_confirmed')
                    if data.get('action') == 'probe_pp_start':
                        self.recovery = '失能确认={}；位置保持={}；零速确认={}；核证={}；机械停止状态未知'.format(
                            disabled, data.get('position_unchanged'), data.get('zero_velocity_confirmed'),
                            '通过' if data.get('success', data.get('completed', False)) else '未通过')
                    else:
                        self.recovery = '失能确认={}；机械停止状态未知'.format(disabled)
                if name in ('result', 'bench_result', 'write_readback'):
                    self.log.append('结果事件 {}：{}'.format(name, data))
        for _ in range(128):
            try:
                _, level, line = self.logs.get_nowait()
                self.log.append(line, level)
            except queue.Empty:
                break
        if self.exit_after:
            if self.worker is None and self.refresh_worker is None:
                if self.controller.enabled_sessions and self.controller.shutdown_results is None and not self._finalizing:
                    self._launch('shutdown', self.controller.close)
                else:
                    self.controller.close()
                    self._running = False
            return
        if (self.controller.rt_ack and self.controller.context is not None
                and not self.busy and self.refresh_worker is None and time.monotonic() >= self.next_refresh):
            self.next_refresh = time.monotonic() + .5
            self._start_refresh()

    def _shutdown_recovery(self):
        results = self.controller.shutdown_results
        if results:
            confirmed = all(item['disabled_confirmed'] for item in results)
            self.recovery = ('退出失能确认=True；机械停止与抱闸仍须独立确认' if confirmed else
                             '退出失能确认=False；状态未知，须独立急停')

    def _quit(self):
        if self.worker or self.refresh_worker:
            self.cancel.set()
            self.refresh_cancel.set()
            self.exit_after = True
            self.log.append('已请求中止；等待后台事务结果，状态尚未知' if self.action == 'prepare_pp'
                            else '已请求中止；等待后台事务与失能清理，状态尚未知', 'warn')
            return False
        self.exit_after = True
        if self.controller.enabled_sessions:
            self._launch('shutdown', self.controller.close)
            return False
        self.controller.close()
        return True

    def on_key(self, name, key):
        if key == 3:
            return self._quit()
        if self.current_view == 'help':
            if name in ('esc', 'q'):
                self.current_view, form = self._help_return
                self.focused_form = form
            elif name in ('up', 'k', 'down', 'j'):
                self.help_log.scroll(1 if name in ('up', 'k') else -1)
            elif name in ('pgup', 'pgdn'):
                self.help_log.page(1 if name == 'pgup' else -1)
            elif name in ('home', 'end'):
                getattr(self.help_log, 'goto_start' if name == 'home' else 'goto_end')()
            return False
        if name in ('?', 'f1'):
            self._help_return = (self.current_view, self.focused_form)
            self.current_view, self.focused_form = 'help', None
            return False
        if name == 'q':
            return self._quit()
        if name in ('pgup', 'pgdn'):
            self.log.page(1 if name == 'pgup' else -1)
            return False
        if name == 'g':
            self.log.goto_end()
        if self.busy:
            if name == 'esc':
                self.cancel.set()
                self.log.append('请求中止失能准备；停止后保留部分写入证据，状态未知' if self.action == 'prepare_pp'
                                else '请求中止；等待快停/失能确认，状态未知', 'warn')
            return False
        if name == 'esc':
            self.controller.discard_prepare()
            self.current_view = 'overview' if self.current_view == 'detail' else 'detail' if self.current_view in ('action', 'confirm', 'setting', 'od') else 'rt' if not self.controller.rt_ack else 'overview'
            self.set_form(None)
            self.pending = self.confirm = None
            return False
        if self.current_view == 'rt':
            if name == 'f5':
                self.controller.rt_ack = True
                self.log.append('操作者已显式确认 RT 占用风险；本会话记住此确认')
            return False
        if self.current_view == 'od':
            if name == 'f5':
                expected = self._od_token()
                if self.input_field.value == expected:
                    self.controller.od_reason = '测试台会话显式确认：' + expected
                    self.log.append('已显式放行本会话 OD 闸门；未提高任何条目可信度', 'warn')
                    self.begin_action(self._resume_action)
                else:
                    self.log.append('OD 确认串不匹配；保持只读', 'err')
            return False
        if self.current_view == 'setting':
            if name == 'f5':
                self._save_setting()
            return False
        if self.current_view == 'action':
            if name == 'f5':
                self.review_action()
            return False
        if self.current_view == 'confirm':
            if name == 'f5':
                self._execute()
            return False
        if name == 'f2':
            self._start_settings()
        elif self.current_view == 'overview':
            if name in ('up', 'k', 'down', 'j'):
                self.selected = (self.selected + (-1 if name in ('up', 'k') else 1)) % 4
            elif name == 'enter':
                self.current_view = 'detail'
            elif name.lower() == 's':
                self._start_scan()
            elif name.lower() == 'r' or name == 'f6':
                self._start_refresh()
            elif name.lower() in ('d', 'x'):
                self.begin_action('disable_all' if name.lower() == 'd' else 'stop_all')
        elif self.current_view == 'detail':
            action = {'a': 'absolute', 'r': 'relative', 'z': 'zero', 'e': 'enable', 'd': 'disable', 'x': 'stop', 'p': 'prepare_pp', 'b': 'probe_pp_start'}.get(name.lower())
            if action:
                self.begin_action(action)
            elif name == 'f6':
                self._start_refresh()
        return False

    def hints(self):
        if self.current_view == 'help':
            return ['Esc/q 返回', 'Up/Down 滚动', 'PgUp/PgDn 翻页']
        if self.busy:
            return ['Esc 中止', 'q 清理后退出', '? 帮助']
        return {'rt': ['F5 确认并扫描', 'q 退出', '? 帮助'],
                'overview': ['S 扫描', 'Enter 详情', 'R 刷新', 'D 全部失能', 'X 全部快停', 'F2 安全设置', '? 帮助', 'q 退出'],
                'detail': ['Esc 返回', 'A 绝对', 'R 相对', 'P 失能准备', 'B 未使能核证', 'Z 设零', 'E 使能', 'D 失能', 'X 快停', 'F6 刷新', 'F2 设置', '? 帮助'],
                'action': ['F5 审阅', 'Esc 取消', '?/F1 帮助'],
                'confirm': ['F5 执行', 'Esc 取消', '?/F1 帮助'],
                'od': ['F5 确认', 'Esc 取消'], 'setting': ['F5 下一项', 'Esc 取消']}.get(self.current_view, [])

    def _detail_lines(self, row):
        if row['node'] is None:
            return ['未分配席位：离线；请返回主界面扫描。']
        v = row['values']
        lines = ['节点 {}  {}  固件 {}'.format(row['node'], row['name'], row['firmware']),
                 '实时约2Hz；上次更新 {}；{}'.format('{:.1f}s前'.format(time.monotonic()-row['updated']) if row['updated'] else '未取得', state_text(row))]
        if row['quarantined']:
            lines.append(row['quarantine_reason'])
        if not v:
            return lines + ['尚无完整实时数据；保持旧值，禁止动作。']
        cal = row['calibration']
        pos = v['actual_position']
        angle = ' = {:.3f}度 电机轴'.format(pos * float(Fraction(cal['motor_degrees_per_user_unit']))) if cal else ''
        lines += ['状态 {} (0x{:04X})'.format(state_text(row), v['statusword']),
                  '位置 {} U{}'.format(pos, angle),
                  '速度 {} {}'.format(v['actual_velocity'], velocity_unit(v)),
                  '转矩 {:.1f}% 额定转矩'.format(v['torque_feedback'] * .1),
                  '模式 {} ({})  报警 {}'.format(mode_text(v['modes_of_operation_display']), v['modes_of_operation_display'], '无' if not v['error_code_object'] else '0x{:04X}'.format(v['error_code_object'])),
                  '控制 [E]使能 [D]失能 [X]快停  [A]绝对 [R]相对 [Z]设零点 [P]失能准备',
                  '设零点：回零方式35；DI配置需求未验证。PV/CSV仅允许预写零速度后使能。']
        return lines

    def build(self):
        # Pure cached rendering. The shared render()/resolve_size() contract is unchanged.
        grid = Grid(self.rows, self.cols, strict=(self.box != 'ascii'))
        outer = Rect(0, 0, self.rows, self.cols)
        draw_box(grid, outer, self.title, self.box)
        width = max(0, self.cols - 4)
        bottom = max(1, self.rows - 7)
        body = Rect(1, 2, max(0, bottom - 1), width)
        lines = []
        if self.current_view == 'help':
            self.help_log.render(grid, Rect(body.y, body.x, max(0, body.h-2), body.w), self.box)
            for i, line in enumerate(SAFETY):
                grid.put(max(body.y, bottom-2)+i, 2, fit(line, width))
        else:
            if self.current_view == 'overview':
                table = Table(['节点', '状态', '位置 U', '速度', '模式', '报警'])
                rows = []
                for row in self.controller.rows:
                    v = row['values']
                    rows.append([str(row['node']) if row['node'] is not None else '未分配', state_text(row),
                        v.get('actual_position', '-'), '{} {}'.format(v['actual_velocity'], velocity_unit(v)) if v else '-',
                        mode_text(v['modes_of_operation_display']) if v else '-',
                        '无' if v and v['error_code_object'] == 0 else '0x{:04X}'.format(v['error_code_object']) if v else '-'])
                table.set_rows(rows)
                table.set_selected(self.selected)
                table.render(grid, Rect(1, 2, min(7, body.h), width), self.box)
                lines = ['', '', '', '', '', '', '总线：' + self.controller.bus_status,
                         'OD：本会话已显式放行' if self.controller.od_reason else 'OD：默认只读；运动前需显式确认',
                         '单次位移预算：{}'.format(self.controller.settings['max_displacement'] if self.controller.settings['max_displacement'] is not None else '未给定（F2）')]
            elif self.current_view == 'rt':
                lines = ['启动确认：RT 控制器可能正在占用总线，继续？',
                         '本测试台将自动扫描 SDO 身份，再以约2Hz读取实时数据。',
                         '即使只读也会发上传请求；本程序锁不能排除外部控制器。',
                         '请确认已协调总线占用与现场安全，然后按 F5。',
                         '此次确认仅对本会话有效；运动仍默认关闭。']
            elif self.current_view == 'od':
                count = len([e for e in (self.controller.od or {}).get('entries', []) if e['status'] != 'verified'])
                expected = self._od_token()
                lines = ['OD 闸门：{} 项未核对；当前默认只读。'.format(count),
                         '确认承担未核对对象与独立停机责任后，显式放行本会话。',
                         '不修改OD可信度；不会自动复位报警、不会开放速度模式。',
                         '请输入：' + expected]
            elif self.current_view == 'setting':
                lines = ['安全设置 {}/3：每一项都由操作者明确给定。'.format(self._setting_index+1),
                         '607Dh几乎全域，不能替代脚本行程；预算另受1度硬顶。',
                         'F5保存当前数并进入下一项；Esc取消整组修改。']
            elif self.current_view in ('action', 'confirm'):
                review, error = (self.pending, None) if self.current_view == 'confirm' else self._preview()
                lines = [ACTIONS.get(self.action, '') + ('：确认执行' if self.current_view == 'confirm' else '：实时预览')]
                lines += review['lines'] if review else ['已阻止：' + str(error)]
                if self.current_view == 'confirm':
                    lines += ['审阅目标已冻结；执行前重新读取基准，变化就拒绝。', '请输入：' + review['token']]
                else:
                    lines += ['F5审阅后还须输入含接口、节点、目标的确认串。']
            else:
                lines = self._detail_lines(self.selected_row)
            for i, line in enumerate(lines):
                if i >= body.h:
                    break
                if line:
                    grid.put(body.y+i, body.x, fit(line, width))
            if self.focused_form:
                y = min(bottom-2, max(5, len(lines)+2))
                self.focused_form.render(grid, Rect(y, 2, max(0, min(2, bottom-y)), width), self.box)
        if self.rows >= 9:
            draw_hsep(grid, bottom, 1, max(0, self.cols-2), self.box, '日志')
            self.log.render(grid, Rect(bottom+1, 2, 3, width), self.box)
            grid.put(self.rows-3, 2, fit(self.progress + '；' + self.recovery, width))
            grid.put(self.rows-2, 2, self.fit_hints(self.hints(), '', width))
        return grid

    def run(self):
        try:
            return super().run()
        finally:
            self.cancel.set()
            self.refresh_cancel.set()
            self.exit_after = True
            self._finalizing = True
            for worker in (self.worker, self.refresh_worker):
                if worker:
                    worker.join()
            self.poll()
            self.controller.close()
            self._shutdown_recovery()
            # curses is already restored here, including real SIGINT unwinds.
            # Print the latest cleanup events, so terminal teardown cannot hide
            # unconfirmed disable/quarantine results queued by the I/O worker.
            if self.controller.context is not None:
                for line in self.controller.shutdown_lines:
                    print(line, flush=True)
                print('测试台结束：' + self.recovery, flush=True)
                for row in self.controller.rows:
                    if row['quarantined']:
                        print('节点 {}：{}；状态未知'.format(row['node'], row['quarantine_reason']), flush=True)
