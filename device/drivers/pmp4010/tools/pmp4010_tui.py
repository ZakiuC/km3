"""Interactive PMP4010 workflow. Rendering is pure; one worker owns bus I/O.

The CLI transaction functions remain the safety authority. This module only
collects inputs, reviews exact targets and reports their structured events.
"""
import copy
import json
from fractions import Fraction
import queue
import re
import sys
import threading

from tui import (Field, Form, Grid, LogView, Menu, Rect, Table, TuiApp,
                 _display_width, draw_box, draw_hsep, draw_vsep, fit)
import pmp4010_params as params
import pmp4010_position as position


# Internal view/field/event identifiers stay stable; only presentation is localized.
VIEW_LABELS = {
    'Status': '状态概览', 'Calibrate': '标定读取', 'Position': '位置运动',
    'Params': '参数维护', 'Raw frames': '原始帧监听',
}
FIELD_LABELS = {
    'node': '节点号', 'apply': '实际执行 yes/no', 'offline-calibration': '离线标定四项',
    'rt-ack': '确认实时占用 yes/no', 'target': '绝对目标 U', 'velocity': '速度 U/s',
    'accel': '加速度 U/s^2', 'decel': '减速度 U/s^2', 'soft-min': '脚本行程下限 U',
    'soft-max': '脚本行程上限 U', 'max-displacement': '单次位移上限 U',
    'move-timeout': '运动超时 s', 'motion-log': '运动日志路径', 'od-bypass': 'OD 旁路理由',
    'start-position': '离线起点 U', 'offline-limits': '离线设备限值四项',
    'action': '操作', 'backup': '备份路径', 'interpolation-readback': '断电后插补读回 yes/no',
    'set': '参数赋值 key=value', 'confirm': '输入确认串', 'confirm-save': '独立保存确认串',
}

# All help prose lives here. Adding a view changes data, not help rendering.
HELP = {
    'global': (
        '全局操作',
        'Up/Down 或 j/k：选择菜单或字段；Tab：下一个字段。',
        'Enter：进入视图；字段中开始编辑，再按 Enter 提交草稿。',
        '编辑中 Esc 丢弃草稿；除 Ctrl-C 外的快捷键均被字段消费，? 是普通字符。',
        'F5：概览刷新、监听启动，或审阅当前操作；输入确认串后再按 F5 执行。',
        'Esc：返回菜单；确认时取消；任务运行时请求中止并等待清理。',
        'q：退出；任务运行时请求中止，完成清理后退出。Ctrl-C 也等待清理。',
        'PgUp/PgDn：翻阅日志；空闲或确认时 g 回到末尾并恢复跟随。',
        '? 或 F1：打开帮助；帮助中 Esc 或 q 返回原视图和原焦点。',
        '帮助中 Up/Down 或 j/k 滚动；PgUp/PgDn 翻页；Home/End 到首尾。',
    ),
    'views': {
        'Status': ('状态概览：显示本地配置、接口状态与 OD 闸门；不发帧。', (
            'F5 刷新本地状态；不打开 CAN 总线，不发 SDO/NMT。',
            '内核接口名、ERROR-ACTIVE、UP/DOWN 等协议事实保留原文。',
        )),
        'Calibrate': ('标定读取：读取或离线检查 608F/6091 标定与实际位置。', (
            'F5 或 F6 审阅读取；输入确认串后按 F5，才执行 SDO 上传。',
            'apply=yes 读取设备；apply=no 使用明确填写的离线标定四项。',
            '标定四项按 608F inc/rev、6091 电机/输出转数顺序填写；不猜分辨率。',
            '读取不写参数、不发 NMT；实时控制器占用时仍需 rt-ack=yes。',
        )),
        'Position': ('位置运动：审阅 PP 绝对目标、脚本行程与电机轴 1度 位移预算。', (
            'F6 单独审阅标定和起点读取；执行前须有同一接口、节点的设备快照。',
            'F5 审阅位置操作；确认串包含接口、节点与绝对目标。',
            '修改 node、apply 或 offline-calibration 草稿即废弃旧快照。',
            'apply=no 需填写离线标定、起点和设备限值；这些值不是设备实测值。',
            'motion-log 必须是新 JSONL 路径；单次电机轴位移硬顶为 1度。',
            'OD 闸门、工具侧软行程、速度/加减速度与清理守卫始终有效。',
        )),
        'Params': ('参数维护：备份、插补、传动比、回滚与 EEPROM 保存。', (
            'action 可填 backup / interpolation / transmission / rollback / save。',
            'F5 审阅精确对象、值及编码；输入确认串后再按 F5 执行。',
            'backup 是显式 SDO 读取：需新备份路径、apply=no；仍会发上传请求。',
            '其余操作 apply=no 只生成离线计划；apply=yes 写入须完整备份。',
            'transmission 还须独立确认断电后插补参数读回。',
            'save 写 EEPROM，须两段各自输入确认串并按 F5；不会自动保存或重试。',
            '第二段自行构造 SAVE <iface> <node> <十进制序列号>，单空格分隔。',
        )),
        'Raw frames': ('原始帧监听：只接收 CAN 帧；零发送。', (
            'F5 启动只接收监听；Esc 请求停止并等待关闭总线。',
            'PgUp/PgDn 翻阅接收日志；不发 NMT/SDO/PDO。',
        )),
    },
    'safety': (
        '1. F5 才可能发帧；Enter 只选中或编辑。',
        '2. 行程保护仅在工具侧；607Dh 等于没有。',
        '3. 中止时状态字读取失败，电机可能仍使能；用独立急停。',
        '4. save 写 EEPROM 不可逆。',
    ),
}


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


def field(name, value='', kind='text'):
    return ChineseField(name, '' if value is None else str(value),
                        label=FIELD_LABELS.get(name, name), kind=kind)


def flag(text, name):
    if text not in ('yes', 'no'):
        raise ValueError('{} 必须填写 yes 或 no'.format(name))
    return text == 'yes'


def numbers(text, count, name):
    values = [int(v) for v in text.split()]
    if len(values) != count:
        raise ValueError('{} 需要 {} 个整数'.format(name, count))
    return values


class PmpApp(TuiApp):
    title = 'PMP4010 机身抬降电机 A'
    menu_width = 15
    log_rows = 4
    VIEWS = ('Status', 'Calibrate', 'Position', 'Params', 'Raw frames')

    def __init__(self, tool, args):
        super().__init__(rows=args.rows, cols=args.cols, box='ascii' if args.ascii else None)
        self.tool, self.args = tool, copy.copy(args)
        self.log = ChineseLog(max_lines=2000)
        self.help_log = LogView(max_lines=500)
        self._help_return_view = None
        self._help_return_form = None
        self.set_views({name: name for name in self.VIEWS}, menu=Menu(self.VIEWS))
        node = args.node
        self.forms = {
            'Calibrate': Form([field('node', node, 'number'), field('apply', 'no'),
                field('offline-calibration', ''), field('rt-ack', 'no')]),
            'Position': Form([field('target', args.target, 'number'),
                field('velocity', args.velocity, 'number'), field('accel', args.acceleration, 'number'),
                field('decel', args.deceleration, 'number'), field('soft-min', args.soft_min, 'number'),
                field('soft-max', args.soft_max, 'number'), field('max-displacement', args.max_displacement, 'number'),
                field('move-timeout', args.move_timeout, 'number'), field('motion-log', args.motion_log),
                field('node', node, 'number'), field('apply', 'no'), field('od-bypass', args.od_gaps_bypass or ''),
                field('rt-ack', 'no'), field('offline-calibration'), field('start-position', None, 'number'),
                field('offline-limits')]),
            'Params': Form([field('action', 'backup'), field('node', node, 'number'), field('backup'),
                field('apply', 'no'), field('od-bypass', args.od_gaps_bypass or ''), field('rt-ack', 'no'),
                field('interpolation-readback', 'no'), field('set')])}
        self.status_rows = [('status', '状态', '等待读取快照', '')]
        self.context = None
        self.snapshot = None
        self.events = queue.Queue()
        self.logs = queue.Queue(maxsize=2000)
        self.cancel = threading.Event()
        self.worker = None
        self.pending = None
        self.confirm = None
        self.progress = '空闲；未打开总线'
        self.recovery = '尚无事务'
        self.exit_after = False
        self.initial_snapshot = True
        self.motion_specs = None
        self.active_review = None
        self.blocked_targets = set()
        self.log.append('状态概览不发帧。apply=no 使用离线输入。')
        self.log.append('F5 审阅操作；Enter 仅编辑字段。? / F1 查看帮助。')

    @property
    def busy(self):
        return self.worker is not None

    def _values(self, view, draft=False):
        return {f.name: f.text if draft else f.value for f in self.forms[view].fields}

    def _args(self, view, draft=False):
        v = self._values(view, draft=draft)
        a = copy.copy(self.args)
        a.node = int(v['node'])
        if not 1 <= a.node <= 127:
            raise ValueError('node 必须明确填写 1..127')
        a.apply = flag(v['apply'], 'apply')
        a.dry_run = False
        a.i_know_rt_is_running = flag(v['rt-ack'], 'rt-ack')
        a.od_gaps_bypass = v.get('od-bypass', '').strip() or None
        a.calibrate = view == 'Calibrate'
        a.position = view == 'Position'
        a.offline_calibration = a.offline_limits = a.start_position = None
        if view in ('Position', 'Calibrate') and not a.apply:
            a.offline_calibration = numbers(v['offline-calibration'], 4, 'offline-calibration')
        if view == 'Position':
            for key, dest in [('target', 'target'), ('velocity', 'velocity'), ('accel', 'acceleration'),
                              ('decel', 'deceleration'), ('soft-min', 'soft_min'), ('soft-max', 'soft_max'),
                              ('max-displacement', 'max_displacement')]:
                setattr(a, dest, int(v[key]))
            a.move_timeout = float(v['move-timeout'])
            a.motion_log = v['motion-log']
            if not a.apply:
                a.start_position = int(v['start-position'])
                a.offline_limits = numbers(v['offline-limits'], 4, 'offline-limits')
        if view == 'Params':
            a.params, a.backup = v['action'], v['backup'] or None
            a.param_set = v['set'].split()
            a.confirm_interpolation_readback = flag(v['interpolation-readback'], 'interpolation-readback')
            a.confirm_save = None
            if a.params not in ('backup', 'interpolation', 'transmission', 'rollback', 'save'):
                raise ValueError('action 可填 backup/interpolation/transmission/rollback/save')
        return a

    def _launch(self, label, function):
        if self.busy:
            return
        self.cancel = threading.Event()
        out = JobOutput(self.events, self.cancel, self.logs)
        self.progress = self._job_label(label)
        def job():
            try:
                result = function(out)
                if getattr(out, 'dropped', 0):
                    self.events.put(('dropped', out.dropped))
                self.events.put(('done', label, result, None))
            except BaseException as exc:
                self.events.put(('done', label, None, exc))
        self.worker = threading.Thread(target=job, name='pmp4010-io')
        self.worker.start()

    def _status(self, out):
        cfg, path = self.tool.load_device_json()
        item, _ = self.tool.find_can_bus(cfg, self.tool.LOGICAL_BUS)
        iface, bitrate = item.get('iface'), item.get('bitrate')
        rows = self.tool.panel_rows(iface, path, self.tool.read_iface_actual(iface) if iface else None,
                                    bitrate, self.tool.service_active(self.tool.RT_SERVICE))
        specs = position.specifications(self.tool.load_od())
        return ('status', rows, (iface, bitrate), specs)

    def _prepare(self, view, a, out):
        """Read local plans before confirmation, never open a bus here."""
        cfg, _ = self.tool.load_device_json()
        item, _ = self.tool.find_can_bus(cfg, self.tool.LOGICAL_BUS)
        target = (item.get('iface'), item.get('bitrate'), a.node)
        sends = a.apply or (view == 'Params' and a.params == 'backup')
        if sends and (target[0], target[2]) in self.blocked_targets:
            raise ValueError('SDO 目标已隔离：必须外部恢复；重启本工具不能证明已恢复')
        od = self.tool.load_od()
        a._tui_expected_target, a._tui_expected_od = target, od
        lines = ['接口={} 位速率={} 节点={}'.format(*target)]
        backup = None
        if view == 'Position':
            specs = position.specifications(od)
            position.validate(a, specs)
            if not self.tool.refuse_motion(out, 'position', a):
                raise ValueError('OD 闸门拒绝；未打开总线')
            if a.apply:
                s = self.snapshot
                if not s or s['context'] != target or s['source'] != 'device':
                    raise ValueError('请先按 F6 读取此目标的当前标定与起点')
                cal, start = s['calibration'], s['calibration']['start_position']
            else:
                cal, start = position.calibration(a.offline_calibration), a.start_position
            position.check_start(a, start, cal)
            position.check_envelope(a, start, start)
            lines += ['绝对目标={} 速度={} 加速度={} 减速度={}'.format(
                a.target, a.velocity, a.acceleration, a.deceleration),
                '脚本行程=[{},{}] 单次位移上限={} 超时={}s'.format(
                    a.soft_min, a.soft_max, a.max_displacement, a.move_timeout), 'JSONL=' + a.motion_log]
        elif view == 'Params':
            if not self.tool.refuse_motion(out, 'params ' + a.params, a):
                raise ValueError('OD 闸门拒绝；未打开总线')
            specs = params.specifications(od)
            if a.backup and a.params != 'backup':
                backup = params.load_backup(a.backup, specs)
                params.check_target(backup, *target)
            writes = params.plan(a.params, specs, backup, cfg.get('network', {}).get('sync_period_us'), a.param_set)
            a._tui_expected_writes, a._tui_expected_backup = writes, backup
            if a.params == 'backup' and (not a.backup or a.apply):
                raise ValueError('backup 需要新路径和 apply=no（显式读取操作）')
            if a.apply and backup is None:
                raise ValueError('apply=yes 需要完整备份')
            lines += ['{} = {} 编码字节={}'.format(k, value, params.encode(specs[k], value).hex()) for k, value in writes]
            lines += ['备份=' + str(a.backup), '不自动保存。transmission 须先独立确认断电后读回。']
        elif view == 'Calibrate':
            lines += ['仅 SDO 上传：标定与实际位置；零写入、零 NMT' if a.apply
                      else '明确填写的离线标定；不读取设备']
        for line in lines:
            out.info(line)
        return ('review', view, a, target, lines, backup)

    def _review(self):
        view = self.current_view
        if view in self.forms:
            try:
                a = self._args(view)
                self._launch('review', lambda out: self._prepare(view, a, out))
            except (ValueError, TypeError) as exc:
                self.log.append(chinese(exc), 'err')

    def _snapshot(self):
        try:
            v = self._values(self.current_view)
            a = copy.copy(self.args)
            a.node = int(v['node'])
            if not 1 <= a.node <= 127:
                raise ValueError('node 必须明确填写 1..127')
            a.apply, a.dry_run = flag(v['apply'], 'apply'), False
            a.i_know_rt_is_running = flag(v['rt-ack'], 'rt-ack')
            a.offline_calibration = (None if a.apply else numbers(v['offline-calibration'], 4, 'offline-calibration'))
            a.offline_limits = a.start_position = None
            a.position, a.calibrate, a._tui_snapshot = False, True, True
            # Snapshot is an explicitly confirmed read, never a motion action.
            self._launch('review', lambda out: self._prepare('Calibrate', a, out))
        except (ValueError, TypeError) as exc:
            self.log.append(chinese(exc), 'err')

    def _execute(self, review):
        _, view, a, target, lines, backup = review
        if view == 'Params':
            callback = lambda out: self.tool.cmd_params(a, out)
        else:
            a._tui_snapshot = view == 'Calibrate'
            callback = lambda out: self.tool.cmd_position(a, out)
        self.confirm = None
        self.active_review = review
        if view == 'Position':
            self.snapshot = None
        self._set_work_form(self.forms.get(self.current_view))
        self.recovery = ('结果与读回确认前，设备状态未知' if a.apply else
                         '离线；未测量设备状态')
        if view == 'Calibrate':
            self.recovery = '只读标定；零写入、零 NMT'
        self._launch(view + ' running', callback)

    def _confirm_key(self, name):
        if name == 'esc':
            self.confirm = None
            self._set_work_form(self.forms.get(self.current_view))
            return
        if name != 'f5':
            return
        review = self.confirm['review']
        _, view, a, target, lines, backup = review
        typed = self.confirm['form'].fields[0].value
        if typed != self.confirm['expected']:
            self.log.append('确认串不匹配；未执行任何操作', 'err')
            return
        if view == 'Params' and a.params == 'save' and a.apply and self.confirm['stage'] == 1:
            expected = params.confirmation(backup)
            self.confirm.update(stage=2, expected=expected,
                form=Form([field('confirm-save')]))
            self._set_work_form(self.confirm['form'])
            return
        if view == 'Params' and a.params == 'save' and a.apply:
            a.confirm_save = typed
        self._execute(review)

    def _raw(self, out):
        cfg, _ = self.tool.load_device_json()
        item, _ = self.tool.find_can_bus(cfg, self.tool.LOGICAL_BUS)
        bus = self.tool.connect_bus(item['iface'], out)
        count = 0
        try:
            while not self.cancel.is_set():
                frame = bus.recv(timeout=min(.1, self.args.timeout))
                if frame is not None:
                    count += 1
                    out.info(self.tool.format_frame(frame))
        finally:
            bus.close()
        return ('raw', count)

    def poll(self):
        if self.initial_snapshot:
            self.initial_snapshot = False
            self._launch('status snapshot', self._status)
        for _ in range(128):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == 'dropped':
                self.log.append('已丢弃 {} 行显示日志；事务事件仍保留'.format(event[1]), 'warn')
            elif kind == 'log':
                self.log.append(event[2], event[1])
            elif kind == 'event':
                _, name, data = event
                if name == 'calibration_snapshot':
                    # Captured context is the reviewed invocation, not current editable fields.
                    target = self.pending[3] if self.pending else None
                    self.snapshot = dict(data, context=target)
                elif name == 'result':
                    applied = self.active_review is not None and self.active_review[2].apply
                    self.recovery = ('设备失能已确认={}；机械停止状态未知'.format(data['disabled_confirmed']) if applied else
                        '离线模拟失能={}；设备状态未知'.format(data['disabled_confirmed']))
                elif name == 'parameter_recovery':
                    self.recovery = ('RAM 已回滚且读回确认；未保存' if data['recovery'] == 'restored_confirmed'
                        else '未尝试写入' if data['recovery'] == 'no_writes'
                        else '状态未知；须人工核查')
                elif name == 'session_state' and data['quarantined'] and data['source'] == 'device':
                    target = self.active_review[3] if self.active_review else None
                    if target is not None:
                        self.blocked_targets.add((target[0], target[2]))
                    self.recovery = 'SDO 已隔离；必须外部恢复；状态未知'
                    self.snapshot = None
                self.progress = {'calibration_snapshot': '标定快照', 'result': '操作结果',
                    'parameter_recovery': '参数恢复', 'session_state': '会话状态'}.get(name, '事件 ' + name) + '：' + json.dumps(data, ensure_ascii=False, sort_keys=True)
                self.log.append(self.progress)
            elif kind == 'done':
                _, label, result, error = event
                self.worker.join()
                self.worker = None
                self.progress = ('已中止；请检查恢复状态' if isinstance(error, KeyboardInterrupt)
                                 else '失败：' + chinese(error) if error else self._job_label(label) + ' 已完成')
                self.log.append(self.progress, 'err' if error else 'info')
                if error is None and isinstance(result, tuple) and result[0] == 'status':
                    _, self.status_rows, self.context, self.motion_specs = result
                elif error is None and isinstance(result, tuple) and result[0] == 'review':
                    self.pending = result
                    _, view, a, target, lines, backup = result
                    action = 'PERSIST' if view == 'Params' and a.params == 'save' and a.apply else (
                        'ROLLBACK' if view == 'Params' and a.params == 'rollback' and a.apply else
                        'MOVE' if view == 'Position' and a.apply else 'WRITE' if view == 'Params' and a.apply else 'RUN')
                    expected = '{} {} {}'.format(action, target[0], target[2])
                    if view == 'Position': expected += ' ' + str(a.target)
                    self.confirm = dict(review=result, expected=expected, stage=1, form=Form([field('confirm')]))
                    self._set_work_form(self.confirm['form'])
                elif not error and isinstance(result, int) and result != 0:
                    self.log.append('命令被拒绝，rc={}；不能判为成功'.format(result), 'err')
                elif not error and result == 0 and label == 'Params running':
                    a = self.active_review[2]
                    self.recovery = ('备份读取完成；零写入' if a.params == 'backup' else
                        '批量读回已确认；断电效果未验证' if a.apply else
                        '仅离线计划；零总线读写')
                elif not error and result == 0 and label == 'Calibrate running':
                    self.recovery = ('设备标定已读取；零写入、零 NMT' if self.active_review[2].apply else
                                     '离线标定；未读取设备')
                if label == 'Position running':
                    self.snapshot = None
                if self.exit_after:
                    self._running = False
        for _ in range(128):
            try:
                _, level, line = self.logs.get_nowait()
            except queue.Empty:
                break
            self.log.append(line, level)

    @staticmethod
    def _job_label(label):
        return {'review': '审阅', 'status snapshot': '状态快照',
                **{name + ' running': title + '执行中'
                   for name, title in VIEW_LABELS.items()}}.get(label, label)

    def _set_work_form(self, form):
        if self.current_view == 'Help':
            self._help_return_form = form
        else:
            self.set_form(form)

    def _open_help(self):
        self._help_return_view = self.current_view
        self._help_return_form = self.focused_form
        self.current_view = 'Help'
        self.focused_form = None
        self.help_log.clear()
        for line in self.help_lines(self._help_return_view):
            self.help_log.append(line)
        # LogView clamps this at render time, after the wrapped height is known.
        self.help_log.scroll(100000)

    @staticmethod
    def help_lines(view):
        entry = HELP['views'].get(view)
        lines = ['当前视图：' + VIEW_LABELS.get(view, '主菜单')]
        if entry:
            lines += [entry[0], '本视图操作'] + list(entry[1])
        lines += list(HELP['global']) + ['五视图用途']
        lines += [entry[0] for entry in HELP['views'].values()]
        return lines

    def _help_key(self, name, key):
        if key == 3:
            if self.busy:
                self.cancel.set()
                self.exit_after = True
                self.log.append('已请求中止；等待清理；状态尚未确认', 'warn')
                return False
            return True
        if name in ('esc', 'q'):
            self.current_view = self._help_return_view
            self.focused_form = self._help_return_form
            return False
        if name in ('up', 'k', 'down', 'j'):
            self.help_log.scroll(1 if name in ('up', 'k') else -1)
        elif name in ('pgup', 'pgdn'):
            self.help_log.page(1 if name == 'pgup' else -1)
        elif name == 'home':
            self.help_log.goto_start()
        elif name == 'end':
            self.help_log.goto_end()
        return False

    def _render_help(self, grid, lay):
        rect = lay.body
        if not rect.valid:
            return
        # Help uses the full body; safety stays outside the scrolling content.
        grid.fill_rect(rect)
        heading = '帮助：' + VIEW_LABELS.get(self._help_return_view, '主菜单')
        grid.put(rect.y, rect.x, fit(heading, rect.w))
        safety = list(HELP['safety'])
        # On normal 24x80 terminals all four warnings are always visible.
        safety_h = min(len(safety), max(0, rect.h - 2))
        scroll_h = max(0, rect.h - 1 - safety_h)
        self.help_log.render(grid, Rect(rect.y + 1, rect.x, scroll_h, rect.w), self.box)
        for row, line in enumerate(safety[:safety_h]):
            grid.put(rect.bottom - safety_h + row, rect.x, fit(line, rect.w))

    def hints(self):
        if self.current_view == 'Help':
            return ['Esc/q 返回', 'Up/Down 滚动', 'PgUp/PgDn 翻页', 'Home/End 首尾']
        if self.focused_form and self.focused_form.editing:
            return ['Enter 提交草稿', 'Esc 丢弃草稿', '? 输入字符']
        if self.confirm:
            return ['Esc 取消', 'Enter 编辑', 'F5 确认', '?/F1 帮助', 'PgUp/PgDn 日志']
        if self.busy:
            return ['Esc 中止', 'q 中止并退出', '?/F1 帮助', 'PgUp/PgDn 日志']
        action = {'Status': 'F5 刷新', 'Raw frames': 'F5 监听'}.get(self.current_view, 'F5 审阅')
        if self.current_view is None:
            action = 'F5 刷新'
        hints = ['q 退出', '?/F1 帮助', 'Enter 编辑/选择', 'Esc 返回', action]
        if self.current_view in ('Position', 'Calibrate'):
            hints.append('F6 读取起点')
        return hints + ['PgUp/PgDn 日志', 'g 跟随']

    def fit_hints(self, hints, mark, width):
        """Keep whole hints, with a Chinese overflow count."""
        if width <= 0:
            return ''
        suffix = '   [' + mark + ']' if mark else ''
        room = width - _display_width(suffix)
        for count in range(len(hints), -1, -1):
            text = '  '.join(hints[:count])
            rest = len(hints) - count
            if rest:
                text += ('  ' if text else '') + '+{} 项'.format(rest)
            if _display_width(text) <= room:
                return text + suffix
        return fit('+{} 项'.format(len(hints)), width)

    def shell_grid(self):
        """Use shared geometry/widgets with Chinese shell labels."""
        lay = self.layout()
        grid = Grid(self.rows, self.cols, strict=(self.box != 'ascii'))
        view = self._help_return_view if self.current_view == 'Help' else self.current_view
        title = self.title + ' / ' + VIEW_LABELS.get(view, '主菜单')
        draw_box(grid, lay.outer, title, self.box)
        if lay.body.valid:
            if lay.menu.valid:
                draw_vsep(grid, lay.menu.right, lay.body.y, lay.body.h, self.box)
            if lay.status.valid and lay.body.bottom < lay.status.y:
                draw_hsep(grid, lay.body.bottom, lay.body.x, lay.body.w, self.box,
                          '日志' if lay.log.valid else None)
        if lay.status.valid:
            mark = '暂停跟随' if not self.log.follow else ''
            hint = self.fit_hints(self.hints(), mark, max(0, lay.status.w - 2))
            grid.put(lay.status.y, lay.status.x, ' ' + hint)
        return grid, lay

    def _emit_plain(self, grid):
        sys.stderr.write('提示：非交互终端，仅绘制一帧 {}x{}；尺寸可用 --rows/--cols 指定。\n'.format(grid.rows, grid.cols))
        for line in grid.to_lines():
            sys.stdout.write(line.rstrip() + '\n')
        sys.stdout.flush()

    def dispatch_key(self, name, key):
        if self.current_view == 'Help':
            return self._help_key(name, key)
        # Editing retains first refusal over every help/action shortcut.
        editing = self.focused_form and self.focused_form.editing
        if name in ('?', 'f1') and not editing:
            self._open_help()
            return False
        if self.busy and (name in ('q', 'esc') or key == 3):
            self.cancel.set()
            self.exit_after = self.exit_after or name == 'q' or key == 3
            self.log.append('已请求中止；等待清理；状态尚未确认', 'warn')
            return False
        if self.busy:
            if name in ('pgup', 'pgdn'):
                self.log.page(1 if name == 'pgup' else -1)
            return False
        before = self.current_view
        binding = None
        if before in ('Position', 'Calibrate'):
            v = self._values(before, draft=True)
            binding = tuple(v[k] for k in ('node', 'apply', 'offline-calibration'))
        result = super().dispatch_key(name, key)
        if binding is not None and before == self.current_view:
            v = self._values(before, draft=True)
            if binding != tuple(v[k] for k in ('node', 'apply', 'offline-calibration')):
                self.snapshot = None
        if before != self.current_view:
            self._set_work_form(self.forms.get(self.current_view))
        return result

    def on_key(self, name, key):
        if self.confirm:
            # Form dispatch has already isolated text editing from action keys.
            if name in ('esc', 'f5'):
                self._confirm_key(name)
                return False
            if name == 'q' or key == 3:
                self.confirm = None
                self._set_work_form(self.forms.get(self.current_view))
                return key == 3
        if super().on_key(name, key):
            return True
        if name in ('pgup', 'pgdn'):
            self.log.page(1 if name == 'pgup' else -1)
            return False
        if name == 'g':
            self.log.goto_end()
            return False
        if self.busy:
            return False
        if name == 'f5':
            if self.current_view == 'Raw frames':
                self._launch('只接收监听；零发送', self._raw)
            elif self.current_view == 'Status' or self.current_view is None:
                self._launch('status snapshot', self._status)
            else:
                self._review()
        elif name == 'f6' and self.current_view in ('Position', 'Calibrate'):
            self._snapshot()
        return False

    def preview_lines(self):
        """Pure draft preview; never opens files or reads a bus."""
        v = self._values('Position', draft=True)
        try:
            applied = flag(v['apply'], 'apply')
            if applied:
                s = self.snapshot
                target = (self.context or (None, None)) + (int(v['node']),)
                if not s or s['context'] != target or s['source'] != 'device':
                    return ['F6：读取此目标的实际标定与起点', '无有效设备快照；执行已阻止']
                cal, start = s['calibration'], s['calibration']['start_position']
                source = '设备快照（执行时再次核查）'
            else:
                cal = position.calibration(numbers(v['offline-calibration'], 4, 'offline-calibration'))
                start = int(v['start-position'])
                source = '离线人工输入；并非设备读数'
            scale = Fraction(cal['motor_degrees_per_user_unit'])
            budget = int(v['max-displacement']) * scale
            lines = [source, '电机轴 1度={} U；起点={} U'.format(float(1 / scale), start),
                     '位移预算={:.6g}度；剩余={:+.6g}度{}'.format(float(budget), float(1-budget),
                      ' 已超限' if budget > 1 else '')]
            for key in ('target', 'soft-min', 'soft-max'):
                try:
                    value = int(v[key])
                    lines.append('{}={} U = {:.6g}度；位移={:+} U/{:+.6g}度'.format(
                        FIELD_LABELS[key], value, float(value*scale), value-start, float((value-start)*scale)))
                except (ValueError, TypeError):
                    lines.append(FIELD_LABELS[key] + '：草稿未填完整或无效')
            try:
                a = self._args('Position', draft=True)
                if self.motion_specs is None:
                    raise ValueError('等待状态与 OD 快照后校验')
                position.validate(a, self.motion_specs)
                position.check_start(a, start, cal)
                position.check_envelope(a, start, start)
                lines.append('输入检查通过；仍须 F5 审阅')
            except (ValueError, TypeError) as exc:
                lines.append('已阻止：' + chinese(exc))
            return lines
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            return ['预览不可用：' + chinese(exc), '草稿无效；执行已阻止']

    def build(self):
        grid, lay = self.shell_grid()
        display_menu = Menu([VIEW_LABELS.get(name, name) for name in self.menu.items],
                            selected=self.menu.selected)
        display_menu.offset = self.menu.offset
        display_menu.render(grid, lay.menu, self.box)
        self.menu.offset = display_menu.offset
        rect = lay.content
        if self.current_view == 'Help':
            self._render_help(grid, lay)
            if lay.log.valid:
                grid.put(lay.log.y, lay.log.x, fit(self.recovery, lay.log.w))
                self.log.render(grid, Rect(lay.log.y+1, lay.log.x, max(0, lay.log.h-1), lay.log.w), self.box)
            return grid
        if self.confirm:
            _, view, a, target, lines, backup = self.confirm['review']
            title = '确认：' + ('实际总线操作' if a.apply or (view == 'Params' and a.params == 'backup') else '离线')
            content = [title] + lines
            if view == 'Params' and a.params == 'save' and a.apply:
                content += ['写入设备 EEPROM；不可撤销；不重试',
                    '序列号={}；第 {}/2 阶段'.format(backup['identity']['4'], self.confirm['stage'])]
            if self.confirm['stage'] == 1:
                content += ['请输入：' + self.confirm['expected']]
            else:
                content += ['自行构造 SAVE <iface> <node> <十进制序列号>', '单空格分隔；输入后按 F5（独立二次确认）']
            # Full exact target list is retained in the scrollable log at every size.
            limit = max(0, rect.h - 3)
            if len(content) > limit:
                content = content[:max(0, limit-4)] + content[-4:]
            for i, line in enumerate(content[:limit]): grid.put(rect.y+i, rect.x, fit(line, rect.w))
            self.confirm['form'].render(grid, Rect(rect.bottom-2, rect.x, 2, rect.w), self.box)
        elif self.current_view in (None, 'Status'):
            Table(headers=('字段', '值'), rows=[(label, value) for _, label, value, _ in self.status_rows]).render(grid, rect, self.box)
        elif self.current_view in self.forms:
            preview = self.preview_lines() if self.current_view == 'Position' else (
                ['apply=yes 读取实际设备；零写入、零 NMT', 'apply=no 须明确填写离线标定四项',
                 'F5 审阅读取；位置视图 F6 读取标定与起点。'] if self.current_view == 'Calibrate' else
                ['F5 审阅精确对象与值；再输入确认串', 'apply=no 仅计划；backup 是显式 SDO 读取',
                 'save：两段确认；EEPROM 持久写入；不可撤销'])
            if self.current_view == 'Calibrate' and self.snapshot:
                cal = self.snapshot['calibration']
                preview = ['来源=' + {'device': '设备实读', 'offline_simulation': '离线模拟'}.get(self.snapshot['source'], self.snapshot['source']), '目标=' + str(self.snapshot['context']),
                    '608F 每转计数 inc/rev={} / {}'.format(*cal['values'][:2]),
                    '6091 电机/输出转数={} / {}'.format(*cal['values'][2:]),
                    '电机轴 1度 = {:.9g} 用户单位'.format(float(1/Fraction(cal['motor_degrees_per_user_unit']))),
                    '电机轴 度/U=' + cal['motor_degrees_per_user_unit'],
                    '起点=' + str(cal.get('start_position', '离线：未读取'))]
            height = max(1, rect.h-len(preview)-1)
            self.forms[self.current_view].render(grid, Rect(rect.y, rect.x, height, rect.w), self.box)
            for i, line in enumerate(preview): grid.put(rect.y+height+i, rect.x, fit(line, rect.w))
        elif self.current_view == 'Raw frames':
            for i, line in enumerate(['F5 启动只接收监听；零发送', 'Esc 中止接收；不发 NMT/SDO/PDO', 'PgUp/PgDn 翻阅日志中的帧']):
                grid.put(rect.y+i, rect.x, fit(line, rect.w))
        # Keep cleanup truth visible in the log region even after returning to menu.
        if lay.log.valid:
            grid.put(lay.log.y, lay.log.x, fit(self.recovery, lay.log.w))
            self.log.render(grid, Rect(lay.log.y+1, lay.log.x, max(0, lay.log.h-1), lay.log.w), self.box)
        return grid

    def run(self):
        interrupted = False
        try:
            return super().run()
        except KeyboardInterrupt:
            self.cancel.set()
            interrupted = True
            return 130
        finally:
            # Never abandon an active transaction when curses restores the terminal.
            # Transport/state deadlines bound every request; cleanup retains its client.
            self.cancel.set()
            if self.worker is not None:
                while self.worker is not None:
                    self.worker.join(.05)
                    self.poll()
            if interrupted or self.active_review is not None:
                # curses.wrapper has restored echo/cursor before this report.
                # Actual SIGINT is distinct from a decoded Ctrl-C key.
                print('\nPMP4010: {}\n{}'.format(chinese(self.progress), self.recovery), flush=True)
