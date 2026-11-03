#!/usr/bin/env python3
"""Manual brake listening aid using the existing guarded Halt-hold transaction.

Enable/disable are requests to the drive's automatic brake logic, not feedback
from its brake coil or mechanism. No raw DO, brake parameters or Go command is
available here. Construction, help and a missing terminal never open CAN.
"""
import argparse
import copy
from pathlib import Path
import sys

# Also support python3 -I and an arbitrary working directory, as on the board.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import pmp4010 as tool
from pmp4010_faults import format_fault
from pmp4010_simple import SimpleController, SimpleApp
from pmp4010_tui import Grid, Rect, LogView, draw_box, draw_hsep, fit


class BrakeController(SimpleController):
    """Expose hold/reset/stop/disable only; retain the original proof and lock.

    SimpleController.execute_action deliberately calls ManualController stages
    through super(), so the public stage API can reject all target/enable/Go
    submissions without changing the shared guarded hold implementation.
    """
    def __init__(self, module, args):
        if getattr(args, 'manual_linear', False) or getattr(args, 'manual_mm_per_rev', None) is not None:
            raise ValueError('抱闸入口不接受测程或换算设置')
        super().__init__(module, args)

    def authorize(self):
        super().authorize()
        self.od_reason = '操作者明确确认：本会话仅人工抱闸听声，接受所列OD未核对项及独立停机责任'

    def review_action(self, kind, out, text=None):
        with self.lock:
            self.cancel_review()
            if kind != 'hold' or text is not None:
                raise ValueError('抱闸入口仅允许e使能保持；不接受位置或运动动作')
            row = self.row(self.node)
            if self.node in self.proofs or (row and row['values'] and
                    self._state(row['values']) == self.tool._canopen.CIA402_ENABLED):
                raise ValueError('已使能或已有保持目标；不会重复切换，请先2/d失能')
            review = super().review_action(kind, out)
            # Human copy is part of the original immutable review seal.
            review['lines'] = [
                '松闸请求：使能并保持当前位置（Halt保持）。',
                '使能会产生保持力矩；请先确认机构与负载安全。',
                '冻结6064={} U，写607A={} U并读回。'.format(review['start'], review['target']),
                '沿用提交ACK核对，最终6040h=012F；不解除Halt。',
                '机械抱闸状态未知：请听声确认，软件使能不能证明松闸。',
                'Enter 仅确认这一次松闸请求；Esc取消；Space/x快停。']
            # Re-seal the presentation together with the same client identity.
            self._simple_review = (review, copy.deepcopy({k:v for k,v in review.items() if k != 'session'}), review['session'])
            return review

    def execute_action(self, review, out):
        with self.lock:
            if (not isinstance(review, dict) or review.get('kind') != 'hold' or
                    review.get('target') != review.get('start') or
                    getattr(review.get('args'), 'target', None) != review.get('start') or
                    self.node in self.proofs):
                self.cancel_review()
                raise ValueError('抱闸入口拒绝运动目标或重复使能；请重新审阅')
            return super().execute_action(review, out)

    def review(self, action, node, value, out, check_gate=True, *, _hold=False, _start=None):
        with self.lock:
            if action != 'disable' or node != self.node or value is not None or not check_gate or _hold or _start is not None:
                self.cancel_review()
                raise ValueError('抱闸入口只开放单节点失能审阅；目标/使能/Go均须拒绝')
            return super().review(action, node, value, out)

    def execute(self, review, out):
        with self.lock:
            if not isinstance(review, dict) or review.get('action') != 'disable' or review.get('node') != self.node:
                self.cancel_review()
                raise ValueError('抱闸入口只开放失能执行')
            return super().execute(review, out)

    def safety_action(self, kind, out):
        if kind not in ('stop', 'disable'):
            raise ValueError('抱闸入口只开放快停或失能')
        return super().safety_action(kind, out)

    def set_value(self, kind, text):
        raise ValueError('抱闸入口不开放速度、步长或范围设置')

    def set_coordinate(self, kind, out, text=None):
        raise ValueError('抱闸入口不开放零点或坐标设置')

    def review_batch(self, *args, **kwargs):
        raise ValueError('抱闸入口不开放批量动作')

    def execute_batch(self, *args, **kwargs):
        raise ValueError('抱闸入口不开放批量动作')


class BrakeApp(SimpleApp):
    title = 'PMP4010 抱闸手动听声测试'

    def __init__(self, module, args):
        super().__init__(module, args)
        self.controller = BrakeController(module, args)
        self.log = LogView(max_lines=2000)
        self.log.append('抱闸机械状态需听声确认；e使能会产生保持力矩。')

    def on_key(self, name, key):
        # Reuse the existing asynchronous safety/confirmation lifecycle. Never
        # forward numeric editors, motion keys or stale synthetic edit views.
        if name in (' ', 'space', 'x', 'q') or key in (3, 32):
            return super().on_key(name, key)
        if name in ('2', 'd'):
            return self._safety('disable')
        if name == '1':
            name = 'e'
        if name not in ('enter', 'esc', '?', 'e', 'd', 't'):
            return False
        if self.current_view not in ('rt', 'connecting', 'authorize', 'main', 'confirm', 'help'):
            self._clear()
            return False
        if self.current_view == 'confirm' and self.action not in ('hold', 'disable', 'reset'):
            self._clear()
            self.progress = '抱闸入口拒绝运动审阅'
            return False
        result = super().on_key(name, key)
        if self.current_view == 'main' and self.progress.startswith('会话授权完成'):
            self.progress = '会话授权完成；1/e审阅松闸请求，2/d立即失能'
        return result

    def hints(self):
        return ['1/e 松闸请求', '2/d 立即失能', 't 手动复位', 'Space/x 快停', 'q 退出', '? 帮助']

    def build(self):
        grid = Grid(self.rows, self.cols, strict=(self.box != 'ascii'))
        draw_box(grid, Rect(0, 0, self.rows, self.cols), self.title, self.box)
        c = self.controller
        row = c.row(c.node)
        v = row['values'] if row else {}
        valid = bool(v and row['online'] and not row['lost'] and not row['quarantined'])
        state = '请求状态未知（尚无有效反馈）'
        if valid:
            p = c.tool._canopen
            if c._state(v) == p.CIA402_ENABLED and v['controlword'] & 0x100:
                state = '松闸请求：已使能保持'
            elif c._state(v) == p.CIA402_DISABLED:
                state = '抱闸请求：已失能'
            else:
                state = '其他电气状态；请核对CW/SW'
        word = lambda value: '0x{:04X}'.format(value) if value is not None else '未知'
        historical = bool(row and (row['lost'] or row['quarantined']))
        lines = ['节点 {}  {}'.format(c.node, '当前实读状态未知（保护锁定/通信丢失）' if historical else state),
                 '机械抱闸状态：未知，需听声确认。使能会产生保持力矩。']
        failure = (row or {}).get('failure_diagnostic')
        if historical and failure:
            # Never fill a partial failed attempt with the last good snapshot
            # or later cleanup samples. A missing 603F really remains unknown.
            lines += ['首次失败诊断（历史，{}）：'.format(
                '非完整快照' if failure.get('snapshot_complete') is False else '阶段采样'),
                '603F={} 6041={}'.format(format_fault(failure.get('fault_code')), word(failure.get('raw_statusword'))),
                'CW={}（{}）'.format(word(failure.get('controlword')), failure.get('controlword_source','来源未知'))]
        prefix = '最后有效采样（历史）：' if historical else ''
        lines += [prefix+'CW(6040)={} SW(6041)={}'.format(word(v.get('controlword')), word(v.get('statusword'))),
                  ('历史报警603F：' if historical else '报警603F：')+format_fault(v.get('error_code_object')),
                  ('历史位置' if historical else '位置')+'6064={} U  速度606C={}（原始）'.format(v.get('actual_position', '未知'), v.get('actual_velocity', '未知'))]
        cleanup = (row or {}).get('cleanup_diagnostic')
        if historical and cleanup:
            lines += ['电气停止：'+('已确认失能；机械停止/抱闸未知' if cleanup['disabled_confirmed'] is True else
                '已执行但失能未确认；须独立急停' if cleanup['stop_attempted'] else
                '未执行；机械停止/抱闸未知，须独立急停')]
        if self.current_view == 'rt':
            lines += ['', '连接授权：请先协调RT总线占用与现场安全。',
                      'Enter仅连接并读取；不会自动使能。q退出。']
        elif self.current_view == 'authorize':
            gaps = [e['key'] for e in (c.od or {}).get('entries', []) if e['status'] != 'verified']
            lines += ['', '本会话接受以下OD未核对项及独立停机责任：']
            lines += ['  '+', '.join(gaps[i:i+2]) for i in range(0, len(gaps), 2)]
            lines += ['Enter仅授权人工抱闸听声；不修改OD可信度，不使能。']
        elif self.current_view == 'confirm' and self.pending:
            if self.action == 'reset':
                lines = lines[:1]+self.pending['lines']
            elif self.action == 'disable':
                lines += ['', '抱闸请求：失能会撤销保持力矩，请确认负载安全。',
                          '电气失能不能证明机械抱闸已闭合；请听声确认。',
                          'Enter仅确认这一次失能；Esc取消；Space/x快停。']
            else:
                lines += ['']+self.pending['lines']
        elif self.current_view == 'help':
            lines += ['', 'e → Enter：冻结当前位置，提交保持目标后使能。',
                      '2/d：立即失能并撤销保持力矩；再次松闸须重新确认。',
                      '重复e不会自动失能再使能；没有自动切换或循环。',
                      't → Enter：仅沿用Warning 2312的受保护手动复位。',
                      'Space/x快停；q退出会对本会话已接管节点确认失能。',
                      '通信隔离后不重试；软件状态不能证明机械抱闸状态。',
                      'Enter/?返回；Esc取消未执行审阅。']
        else:
            lines += ['', '1/e松闸须Enter；2/d立即失能并撤销保持力矩。',
                      '请一次一次切换并听声；机械抱闸结果由现场判断。']
        bottom = max(1, self.rows-6)
        for i, line in enumerate(lines[:max(0, bottom-1)]):
            grid.put(i+1, 2, fit(line, max(0, self.cols-4)))
        if self.rows >= 9:
            draw_hsep(grid, bottom, 1, max(0, self.cols-2), self.box, '日志')
            self.log.render(grid, Rect(bottom+1, 2, 2, max(0, self.cols-4)), self.box)
            grid.put(self.rows-3, 2, fit(self.progress, max(0, self.cols-4)))
            grid.put(self.rows-2, 2, fit('1/e松闸 Enter确认 2/d立即抱闸请求 Space/x快停 q退出 ?帮助', max(0, self.cols-4)))
        return grid


def build_parser():
    parser = argparse.ArgumentParser(description='PMP4010抱闸手动听声TUI：1/e使能保持须Enter确认，2/d立即失能。使能会产生保持力矩；机械抱闸需听声确认。')
    parser.add_argument('--node', type=int, default=1, help='单节点1..127，默认1')
    parser.add_argument('--motion-log', required=False, help='新建JSONL日志路径，动作必须记录，不覆盖已有文件')
    parser.add_argument('--rows', type=int, default=None, help='终端行数')
    parser.add_argument('--cols', type=int, default=None, help='终端列数')
    parser.add_argument('--ascii', action='store_true', help='使用ASCII边框')
    return parser


def main(argv=None):
    parser = build_parser()
    options = parser.parse_args(argv)
    if not 1 <= options.node <= 127:
        parser.error('--node须为1..127')
    if any(value is not None and value < minimum for value, minimum in ((options.rows, 24), (options.cols, 80))):
        parser.error('--rows/--cols至少为24行、80列')
    # Shared defaults keep device.json resolution, OD, timeout and protocol
    # policy in their existing implementation. No config/interface override.
    args = tool.build_parser().parse_args(['--manual'])
    for key, value in vars(options).items():
        setattr(args, key, value)
    app = BrakeApp(tool, args)
    if not app._curses_usable():
        print('抱闸手动测试需要交互终端；未打开总线、未发送控制写入。', file=sys.stderr)
        return 2
    if not options.motion_log:
        parser.error('请指定新的--motion-log日志路径（或使用配套启动脚本）')
    return app.run()


if __name__ == '__main__':
    sys.exit(main())
