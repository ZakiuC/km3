"""Interactive PMP4010 workflow. Rendering is pure; one worker owns bus I/O.

The CLI transaction functions remain the safety authority. This module only
collects inputs, reviews exact targets and reports their structured events.
"""
import copy
import json
from fractions import Fraction
import queue
import threading

from tui import Field, Form, Menu, Rect, Table, TuiApp
import pmp4010_params as params
import pmp4010_position as position


def english(text):
    """CLI keeps its Chinese diagnostics; terminal summaries use ASCII only."""
    text = str(text)
    if text.isascii():
        return text
    translations = (
        ('超过1度', 'Displacement budget exceeds the 1 degree motor limit'),
        ('目标位移必须非零', 'Target displacement must be nonzero and within the budget'),
        ('目标超出脚本软行程', 'Target is outside soft travel or soft limits are invalid'),
        ('实际位置越过', 'Actual position is outside soft travel'),
        ('实际位移越过', 'Actual displacement exceeded the operator budget'),
        ('速度/加速度/减速度', 'Velocity, acceleration and deceleration must be positive integers'),
        ('经G换算', 'Target/soft travel exceeds encoder Int32 after gear conversion'),
        ('--motion-log', 'A new motion-log path is required; existing logs are never overwritten'),
        ('--move-timeout', 'move-timeout must be finite and positive'),
        ('未核对', 'OD entries remain unverified; explicit reason required for motion/parameter access'),
        ('SDO会话已隔离', 'SDO session quarantined; no further frames; state UNKNOWN'),
        ('未确认失能', 'Disable NOT confirmed; mechanical state UNKNOWN; use independent E-stop'),
        ('失能未确认', 'Disable NOT confirmed; mechanical state UNKNOWN; use independent E-stop'),
        ('已确认 Switch on disabled', 'Switch on disabled confirmed; mechanical stop/brake still unverified'),
        ('已自动回滚', 'Attempted RAM writes restored and readback confirmed; no save'),
        ('未知', 'State UNKNOWN; manual verification required'),
        ('超时', 'Operation timed out; inspect transaction result and cleanup status'),
        ('身份', 'Device identity must match the complete backup'),
        ('使能状态', 'Device is enabled; parameter write refused'),
        ('二次确认', 'EEPROM save requires the exact independent second confirmation'),
        ('先保存插补', 'Transmission requires independent post-power-cycle interpolation readback'),
        ('范围', 'Value is outside OD width/range'),
        ('四项标定值', 'All four calibration values must be explicit positive Uint32 integers'),
        ('读回宽度', 'SDO readback width does not match the OD'),
        ('实际接口状态', 'Interface state/bitrate does not match; bus open refused'),
        ('备份文件已存在', 'Backup already exists; overwrite refused'),
        ('拒绝发帧', 'Transmit refused; RT ownership requires explicit acknowledgement'),
    )
    for token, result in translations:
        if token in text:
            return result
    # Exact English object keys/numbers are emitted separately by review/events.
    return 'CLI diagnostic: inspect OD/input/RT gates and transaction result'


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
            self.logs.put_nowait(('log', level, english(text)))
            if not str(text).isascii():
                self.logs.put_nowait(('log', level, 'detail=' + json.dumps(str(text), ensure_ascii=True)))
        except queue.Full:
            self.dropped = getattr(self, 'dropped', 0) + 1

    info = plain = title = cmd = _line
    def ok(self, text): self._line(text, 'ok')
    def warn(self, text): self._line(text, 'warn')
    def bad(self, text): self._line(text, 'err')
    def field(self, key, value): self._line('{}: {}'.format(key, value))
    def c(self, name, text): return str(text)


def field(name, value='', kind='text'):
    return Field(name, '' if value is None else str(value), kind=kind)


def flag(text, name):
    if text not in ('yes', 'no'):
        raise ValueError('{} must be yes or no'.format(name))
    return text == 'yes'


def numbers(text, count, name):
    values = [int(v) for v in text.split()]
    if len(values) != count:
        raise ValueError('{} needs {} integers'.format(name, count))
    return values


class PmpApp(TuiApp):
    title = 'PMP4010 lift motor A'
    menu_width = 15
    log_rows = 4
    VIEWS = ('Status', 'Calibrate', 'Position', 'Params', 'Raw frames')

    def __init__(self, tool, args):
        super().__init__(rows=args.rows, cols=args.cols, box='ascii' if args.ascii else None)
        self.tool, self.args = tool, copy.copy(args)
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
        self.status_rows = [('status', '', 'pending snapshot', '')]
        self.context = None
        self.snapshot = None
        self.events = queue.Queue()
        self.logs = queue.Queue(maxsize=2000)
        self.cancel = threading.Event()
        self.worker = None
        self.pending = None
        self.confirm = None
        self.progress = 'idle; no bus opened'
        self.recovery = 'no transaction'
        self.exit_after = False
        self.initial_snapshot = True
        self.motion_specs = None
        self.active_review = None
        self.blocked_targets = set()
        self.log.append('Status sends no frames. apply=no uses offline inputs.')
        self.log.append('F5 reviews an action. Enter only edits fields.')

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
            raise ValueError('node must be explicit 1..127')
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
                raise ValueError('action: backup/interpolation/transmission/rollback/save')
        return a

    def _launch(self, label, function):
        if self.busy:
            return
        self.cancel = threading.Event()
        out = JobOutput(self.events, self.cancel, self.logs)
        self.progress = label
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
            raise ValueError('SDO target quarantined: external recovery REQUIRED; restarting this app does not attest recovery')
        od = self.tool.load_od()
        a._tui_expected_target, a._tui_expected_od = target, od
        lines = ['iface={} bitrate={} node={}'.format(*target)]
        backup = None
        if view == 'Position':
            specs = position.specifications(od)
            position.validate(a, specs)
            if not self.tool.refuse_motion(out, 'position', a):
                raise ValueError('OD gate refused; no bus opened')
            if a.apply:
                s = self.snapshot
                if not s or s['context'] != target or s['source'] != 'device':
                    raise ValueError('F6 read current calibration/start for this exact target first')
                cal, start = s['calibration'], s['calibration']['start_position']
            else:
                cal, start = position.calibration(a.offline_calibration), a.start_position
            position.check_start(a, start, cal)
            position.check_envelope(a, start, start)
            lines += ['absolute target={} velocity={} accel={} decel={}'.format(
                a.target, a.velocity, a.acceleration, a.deceleration),
                'soft=[{},{}] max-displacement={} timeout={}s'.format(
                    a.soft_min, a.soft_max, a.max_displacement, a.move_timeout), 'JSONL=' + a.motion_log]
        elif view == 'Params':
            if not self.tool.refuse_motion(out, 'params ' + a.params, a):
                raise ValueError('OD gate refused; no bus opened')
            specs = params.specifications(od)
            if a.backup and a.params != 'backup':
                backup = params.load_backup(a.backup, specs)
                params.check_target(backup, *target)
            writes = params.plan(a.params, specs, backup, cfg.get('network', {}).get('sync_period_us'), a.param_set)
            a._tui_expected_writes, a._tui_expected_backup = writes, backup
            if a.params == 'backup' and (not a.backup or a.apply):
                raise ValueError('backup needs new path and apply=no (explicit read action)')
            if a.apply and backup is None:
                raise ValueError('apply requires complete backup')
            lines += ['{} = {} bytes={}'.format(k, value, params.encode(specs[k], value).hex()) for k, value in writes]
            lines += ['backup=' + str(a.backup), 'No automatic save. Transmission requires post-power-cycle readback.']
        elif view == 'Calibrate':
            lines += ['SDO uploads only: calibration and actual position; zero writes/NMT' if a.apply
                      else 'Explicit offline calibration; no device read']
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
                self.log.append(str(exc), 'err')

    def _snapshot(self):
        try:
            v = self._values(self.current_view)
            a = copy.copy(self.args)
            a.node = int(v['node'])
            if not 1 <= a.node <= 127:
                raise ValueError('node must be explicit 1..127')
            a.apply, a.dry_run = flag(v['apply'], 'apply'), False
            a.i_know_rt_is_running = flag(v['rt-ack'], 'rt-ack')
            a.offline_calibration = (None if a.apply else numbers(v['offline-calibration'], 4, 'offline-calibration'))
            a.offline_limits = a.start_position = None
            a.position, a.calibrate, a._tui_snapshot = False, True, True
            # Snapshot is an explicitly confirmed read, never a motion action.
            self._launch('review', lambda out: self._prepare('Calibrate', a, out))
        except (ValueError, TypeError) as exc:
            self.log.append(str(exc), 'err')

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
        self.set_form(self.forms.get(self.current_view))
        self.recovery = ('DEVICE STATE UNKNOWN until result/readback' if a.apply else
                         'OFFLINE; no device state measured')
        if view == 'Calibrate':
            self.recovery = 'Read-only calibration; zero writes/NMT'
        self._launch(view + ' running', callback)

    def _confirm_key(self, name):
        if name == 'esc':
            self.confirm = None
            self.set_form(self.forms.get(self.current_view))
            return
        if name != 'f5':
            return
        review = self.confirm['review']
        _, view, a, target, lines, backup = review
        typed = self.confirm['form'].fields[0].value
        if typed != self.confirm['expected']:
            self.log.append('Confirmation mismatch; nothing executed', 'err')
            return
        if view == 'Params' and a.params == 'save' and a.apply and self.confirm['stage'] == 1:
            expected = params.confirmation(backup)
            self.confirm.update(stage=2, expected=expected,
                form=Form([field('confirm-save')]))
            self.set_form(self.confirm['form'])
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
                self.log.append('Dropped {} display log lines; transaction events retained'.format(event[1]), 'warn')
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
                    self.recovery = ('DEVICE disabled confirmed={}; mechanical stop UNKNOWN'.format(data['disabled_confirmed']) if applied else
                        'OFFLINE simulated disabled={}; device state UNKNOWN'.format(data['disabled_confirmed']))
                elif name == 'parameter_recovery':
                    self.recovery = ('RAM restored and readback confirmed; no save' if data['recovery'] == 'restored_confirmed'
                        else 'No writes attempted' if data['recovery'] == 'no_writes'
                        else 'STATE UNKNOWN; manual verification required')
                elif name == 'session_state' and data['quarantined'] and data['source'] == 'device':
                    target = self.active_review[3] if self.active_review else None
                    if target is not None:
                        self.blocked_targets.add((target[0], target[2]))
                    self.recovery = 'SDO QUARANTINED; external recovery REQUIRED; state UNKNOWN'
                    self.snapshot = None
                self.progress = name + ': ' + json.dumps(data, ensure_ascii=True, sort_keys=True)
                self.log.append(self.progress)
            elif kind == 'done':
                _, label, result, error = event
                self.worker.join()
                self.worker = None
                self.progress = ('cancelled; inspect recovery status' if isinstance(error, KeyboardInterrupt)
                                 else 'failed: ' + english(error) if error else label + ' finished')
                self.log.append(self.progress, 'err' if error else 'info')
                if error and not str(error).isascii():
                    self.log.append('detail=' + json.dumps(str(error), ensure_ascii=True), 'err')
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
                    self.set_form(self.confirm['form'])
                elif not error and isinstance(result, int) and result != 0:
                    self.log.append('Command refused, rc={}; no success claimed'.format(result), 'err')
                elif not error and result == 0 and label == 'Params running':
                    a = self.active_review[2]
                    self.recovery = ('backup read completed; zero writes' if a.params == 'backup' else
                        'batch readback confirmed; power-cycle effect unverified' if a.apply else
                        'offline plan only; zero bus reads/writes')
                elif not error and result == 0 and label == 'Calibrate running':
                    self.recovery = ('Device calibration read; zero writes/NMT' if self.active_review[2].apply else
                                     'OFFLINE calibration; no device read')
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

    def hints(self):
        if self.confirm: return ['Esc cancel', 'Enter edit/commit', 'F5 confirm', 'PgUp/PgDn log']
        if self.busy: return ['Esc cancel', 'q cancel+quit', 'PgUp/PgDn log']
        return ['q quit', 'Enter edit/select', 'Esc back', 'F5 review', 'F6 read start', 'PgUp/PgDn log']

    def dispatch_key(self, name, key):
        if self.busy and (name in ('q', 'esc') or key == 3):
            self.cancel.set()
            self.exit_after = self.exit_after or name == 'q' or key == 3
            self.log.append('Cancellation requested; waiting for cleanup; state not yet confirmed', 'warn')
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
            self.set_form(self.forms.get(self.current_view))
        return result

    def on_key(self, name, key):
        if self.confirm:
            # Form dispatch has already isolated text editing from action keys.
            if name in ('esc', 'f5'):
                self._confirm_key(name)
                return False
            if name == 'q' or key == 3:
                self.confirm = None
                self.set_form(self.forms.get(self.current_view))
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
                self._launch('listen only; zero transmit', self._raw)
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
                    return ['F6: read actual calibration + start for this target', 'No valid device snapshot; execute blocked']
                cal, start = s['calibration'], s['calibration']['start_position']
                source = 'device snapshot (rechecked at execute)'
            else:
                cal = position.calibration(numbers(v['offline-calibration'], 4, 'offline-calibration'))
                start = int(v['start-position'])
                source = 'OFFLINE explicit inputs; not device readings'
            scale = Fraction(cal['motor_degrees_per_user_unit'])
            budget = int(v['max-displacement']) * scale
            lines = [source, '1 deg={} U; start={} U'.format(float(1 / scale), start),
                     'budget={:.6g} deg; remaining={:+.6g} deg{}'.format(float(budget), float(1-budget),
                      ' EXCEEDED' if budget > 1 else '')]
            for key in ('target', 'soft-min', 'soft-max'):
                try:
                    value = int(v[key])
                    lines.append('{}={} U = {:.6g} deg; delta={:+} U/{:+.6g} deg'.format(
                        key, value, float(value*scale), value-start, float((value-start)*scale)))
                except (ValueError, TypeError):
                    lines.append(key + ': incomplete/invalid draft')
            try:
                a = self._args('Position', draft=True)
                if self.motion_specs is None:
                    raise ValueError('validation pending status/OD snapshot')
                position.validate(a, self.motion_specs)
                position.check_start(a, start, cal)
                position.check_envelope(a, start, start)
                lines.append('Input checks OK; F5 review still required')
            except (ValueError, TypeError) as exc:
                lines.append('BLOCKED: ' + english(exc))
            return lines
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            return ['Preview unavailable: ' + english(exc), 'Invalid draft; execute blocked']

    def build(self):
        grid, lay = self.shell_grid()
        self.menu.render(grid, lay.menu, self.box)
        rect = lay.content
        if self.confirm:
            _, view, a, target, lines, backup = self.confirm['review']
            title = 'CONFIRM: ' + ('REAL BUS ACTION' if a.apply or (view == 'Params' and a.params == 'backup') else 'OFFLINE')
            content = [title] + lines
            if view == 'Params' and a.params == 'save' and a.apply:
                content += ['PERSIST TO DEVICE EEPROM; cannot undo; no retry',
                    'serial={} ; stage {}/2'.format(backup['identity']['4'], self.confirm['stage'])]
            if self.confirm['stage'] == 1:
                content += ['Type: ' + self.confirm['expected']]
            else:
                content += ['Construct SAVE <iface> <node> <decimal serial>', 'Use single spaces; then F5 (second confirmation)']
            # Full exact target list is retained in the scrollable log at every size.
            limit = max(0, rect.h - 3)
            if len(content) > limit:
                content = content[:max(0, limit-4)] + content[-4:]
            for i, line in enumerate(content[:limit]): grid.put(rect.y+i, rect.x, line[:rect.w])
            self.confirm['form'].render(grid, Rect(rect.bottom-2, rect.x, 2, rect.w), self.box)
        elif self.current_view in (None, 'Status'):
            Table(headers=('field', 'value'), rows=[(k, value) for k, _, value, _ in self.status_rows]).render(grid, rect, self.box)
        elif self.current_view in self.forms:
            preview = self.preview_lines() if self.current_view == 'Position' else (
                ['apply=yes reads actual device; no writes/NMT', 'apply=no requires four explicit offline values',
                 'F5 review read. Position F6 reads calibration+start.'] if self.current_view == 'Calibrate' else
                ['F5 review exact objects/values; then typed confirm', 'apply=no = plan; backup = explicit SDO reads',
                 'save: two stages; EEPROM persistent; no undo'])
            if self.current_view == 'Calibrate' and self.snapshot:
                cal = self.snapshot['calibration']
                preview = ['source=' + self.snapshot['source'], 'context=' + str(self.snapshot['context']),
                    '608F inc/rev={} / {}'.format(*cal['values'][:2]),
                    '6091 motor/output rev={} / {}'.format(*cal['values'][2:]),
                    '1 motor deg = {:.9g} user units'.format(float(1/Fraction(cal['motor_degrees_per_user_unit']))),
                    'motor deg/U=' + cal['motor_degrees_per_user_unit'],
                    'start=' + str(cal.get('start_position', 'offline: not read'))]
            height = max(1, rect.h-len(preview)-1)
            self.forms[self.current_view].render(grid, Rect(rect.y, rect.x, height, rect.w), self.box)
            for i, line in enumerate(preview): grid.put(rect.y+height+i, rect.x, line[:rect.w])
        elif self.current_view == 'Raw frames':
            for i, line in enumerate(['F5 listen only; zero transmit', 'Esc cancels receive; no NMT/SDO/PDO', 'PgUp/PgDn scroll frames in log']):
                grid.put(rect.y+i, rect.x, line[:rect.w])
        # Keep cleanup truth visible in the log region even after returning to menu.
        if lay.log.valid:
            grid.put(lay.log.y, lay.log.x, self.recovery[:lay.log.w])
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
                print('\nPMP4010: {}\n{}'.format(english(self.progress), self.recovery), flush=True)
