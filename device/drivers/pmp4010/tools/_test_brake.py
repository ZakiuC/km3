"""Manual brake requests: production guarded stages over standard MockBus only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import time
from unittest import mock

from _test_bench import BenchFixture
from _test_bench_support import DelayedBus, requests, tool
from _test_manual import EnabledEdgeNode
from _test_simple import ResetNode
import pmp4010_brake as brake
from pmp4010_tui import _display_width


class BrakeTests(BenchFixture):
    def setUp(self):
        super().setUp()
        self.node = EnabledEdgeNode()
        self.node.enable_drift = 0
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        self.args.manual_velocity = 100
        self.ctl = brake.BrakeController(tool, self.args)
        self.addCleanup(self.ctl.close)

    def ready(self):
        self.online()
        self.ctl.authorize()
        self.bus.sent.clear()

    def controls(self):
        return [v for i, _, v in requests(self.bus, True) if i == 0x6040]

    def hold(self):
        review = self.ctl.review_action('hold', self.out)
        self.ctl.execute_action(review, self.out)
        return review

    def app(self, view='main'):
        app = brake.BrakeApp(tool, self.args)
        app.controller = self.ctl
        app.rows, app.cols = 24, 80
        app.current_view = view
        app.next_refresh = float('inf')
        self.addCleanup(lambda: self.drain(app))
        return app

    def drain(self, app):
        deadline = time.monotonic()+3
        while app.worker is not None or app.refresh_worker is not None or app.urgent or app.exit_after and app._running:
            if time.monotonic() > deadline:
                self.fail('manual UI worker did not complete')
            for worker in (app.worker, app.refresh_worker):
                if worker:
                    worker.join(.02)
            app.poll()

    def assert_no_motion(self):
        self.assertFalse(self.node.enabled_edges)
        self.assertFalse(self.node.movement_samples)
        self.assertNotIn(0x2f, self.controls())
        self.assertNotIn(0x3f, self.controls())
        self.assertTrue(all(word & 0x100 for word in self.controls() if word & 0xf == 0xf))
        self.assertTrue(all(i in (0x6040, 0x607a, 0x6081) for i, _, _ in requests(self.bus, True)))

    def test_startup_authorization_cancel_disable_quit_have_no_enable(self):
        self.assertFalse(self.bus.sent)
        for kind in ('stop', 'disable'):
            self.ctl.safety_action(kind, self.out)
        self.assertFalse(self.bus.sent)
        self.ready()
        review = self.ctl.review_action('hold', self.out)
        self.ctl.cancel_review()
        with self.assertRaises(ValueError):
            self.ctl.execute_action(review, self.out)
        self.assertFalse(requests(self.bus, True))
        self.ctl.close()
        self.assertFalse(requests(self.bus, True))
        print('BRAKE startup/authorization/cancel/unused close: uploads only, zero enable')

    def test_hold_freezes_current_replaces_old_target_with_halt_and_no_go(self):
        self.ready()
        self.node.set_value(0x6064, -1452)
        self.node.set_value(0x607a, 90000)
        self.ctl.refresh(self.out)
        review = self.ctl.review_action('hold', self.out)
        self.assertEqual((review['start'], review['target']), (-1452, -1452))
        self.assertFalse(requests(self.bus, True))
        self.ctl.execute_action(review, self.out)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 0x12f])
        self.assertEqual([v for i, _, v in requests(self.bus, True) if i == 0x607a], [(-1452)&0xffffffff])
        self.assertEqual(self.node.value(0x607a, signed=True), -1452)
        self.assertEqual(self.node.value(0x6064, signed=True), -1452)
        self.assertEqual(self.ctl.proofs[1]['phase'], 'enabled')
        self.assert_no_motion()
        print('BRAKE hold: old607A=90000 -> fresh6064=-1452 -> 607A readback; CW=0106/0107/0127/0137/0127/012F; no Go')

    def test_repeat_release_no_frames_and_second_cycle_requires_new_review(self):
        self.ready()
        review = self.hold()
        count = len(self.bus.sent)
        with self.assertRaisesRegex(ValueError, '不会重复切换'):
            self.ctl.review_action('hold', self.out)
        with self.assertRaises(ValueError):
            self.ctl.execute_action(review, self.out)
        self.assertEqual(len(self.bus.sent), count)
        self.ctl.safety_action('disable', self.out)
        self.assertEqual(self.controls()[-1], 0)
        self.assertEqual(self.controls().count(0x12f), 1)
        self.hold()
        self.assertEqual(self.controls().count(0x12f), 2)
        self.assert_no_motion()

    def test_public_api_blocks_all_movement_paths_and_forged_hold(self):
        self.ready()
        for kind in ('forward', 'backward', 'relative', 'absolute', 'enable', 'go', 'home'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.ctl.review_action(kind, self.out)
            with self.subTest(stage=kind), self.assertRaises(ValueError):
                self.ctl.review(kind, 1, 1, self.out)
            with self.assertRaises(ValueError):
                self.ctl.execute({'action': kind, 'node': 1}, self.out)
        for method, args in ((self.ctl.set_value, ('speed','1')),
                             (self.ctl.set_coordinate, ('zero',self.out)),
                             (self.ctl.review_batch, ('enable',self.out)),
                             (self.ctl.execute_batch, ({},self.out)),
                             (self.ctl.safety_action, ('enable',self.out))):
            with self.assertRaises(ValueError):
                method(*args)
        for field in ('target', 'kind', 'args'):
            review = self.ctl.review_action('hold', self.out)
            if field == 'args':
                review['args'].target += 1
            else:
                review[field] = review[field]+1 if field == 'target' else 'forward'
            with self.assertRaises(ValueError):
                self.ctl.execute_action(review, self.out)
        with self.assertRaises(ValueError):
            self.ctl.review_action('hold', self.out, '1')
        self.assertFalse(requests(self.bus, True))

    def test_od_gate_stays_closed_without_session_authorization(self):
        self.online()
        with self.assertRaises(ValueError):
            self.ctl.review_action('hold', self.out)
        self.assertFalse(requests(self.bus, True))

    def test_home_offset_soft_limit_and_fault_block_release(self):
        self.ready()
        for index, sub, value in ((0x607c,0,1), (0x607d,1,-1400), (0x6041,0,0x448)):
            old = self.node.value(index, sub, signed=index == 0x607d)
            self.node.set_value(index, value, sub)
            self.ctl.refresh(self.out)
            with self.subTest(index=hex(index)), self.assertRaises(ValueError):
                self.ctl.review_action('hold', self.out)
            self.assertNotIn(0x12f, self.controls())
            if self.ctl.blocked:
                break
            self.node.set_value(index, old, sub)

    def test_hold_proof_retains_limit_and_parameter_monitoring(self):
        self.ready()
        self.hold()
        self.node.set_value(0x607c, 1)
        self.ctl.refresh(self.out)
        self.assertTrue(self.ctl.blocked)
        self.assertNotIn(1, self.ctl.proofs)
        self.assertEqual(self.controls()[-2:], [2, 0])
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)

    def test_no_stale_target_readback_enable_on_mismatched_607a(self):
        self.ready()
        review = self.ctl.review_action('hold', self.out)
        original = self.node.handle
        def wrong(frame, now):
            original(frame, now)
            if frame.data[:4] == bytes.fromhex('237a6000'):
                self.node.set_value(0x607a, 90000)
        with mock.patch.object(self.node, 'handle', side_effect=wrong), self.assertRaises(ValueError):
            self.ctl.execute_action(review, self.out)
        self.assertNotIn(0x12f, self.controls())
        self.assertTrue(self.ctl.blocked)

    def assert_quarantined_no_following_requests(self):
        session = self.ctl.manual_sessions[1]
        self.assertTrue(session.client.quarantined)
        count = len(self.bus.sent)
        with mock.patch.object(tool._canopen, 'SdoClient', side_effect=AssertionError('no replacement')):
            self.ctl.refresh(self.out)
            with self.assertRaises(ValueError):
                self.ctl.review_action('hold', self.out)
            with self.assertRaises(ValueError):
                self.ctl.safety_action('disable', self.out)
            with self.assertRaises(ValueError):
                self.ctl.review_reset(self.out)
            self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)
        self.assertIs(self.ctl.manual_sessions[1], session)

    def test_missing_enable_ack_is_quarantined_without_any_retry_or_cleanup(self):
        self.ready()
        review = self.ctl.review_action('hold', self.out)
        original = self.node.handle
        def lost(frame, now):
            original(frame, now)
            if frame.data[:6] == bytes.fromhex('2b4060002f01'):
                self.node._outbox.clear()
        with mock.patch.object(self.node, 'handle', side_effect=lost), self.assertRaises(tool.MotionError):
            self.ctl.execute_action(review, self.out)
        self.assertEqual(self.controls().count(0x12f), 1)
        self.assertEqual(self.controls()[-1], 0x12f)
        self.assert_quarantined_no_following_requests()
        print('BRAKE enable ACK loss: last CW012F once; same-client quarantine; refresh/d/reset/close zero extra frames')

    def test_receive_protocol_error_has_no_cleanup_or_replacement_client(self):
        self.ready()
        self.hold()
        original = self.bus.recv
        def malformed(*args, **kwargs):
            frame = original(*args, **kwargs)
            if frame is None:
                return None
            data = bytearray(frame.data)
            data[1] ^= 1
            return type(frame)(frame.arbitration_id, bytes(data))
        before = list(self.controls())
        with mock.patch.object(self.bus, 'recv', side_effect=malformed):
            self.ctl.refresh(self.out)
        self.assertEqual(self.controls(), before)
        self.assert_quarantined_no_following_requests()

    def test_close_owned_hold_confirms_disable_without_implicit_enable(self):
        self.ready()
        self.hold()
        result = self.ctl.close()
        # Preserve the shared exit-only Shutdown contract, distinct from d=0.
        self.assertEqual(self.controls()[-1], 6)
        self.assertEqual(self.controls().count(0x12f), 1)
        self.assertTrue(result[0]['disabled_confirmed'])
        self.assertFalse(self.node.value(0x6041) & 4)
        self.assertIsNot(result[0].get('mechanical_stop_confirmed'), True)
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)

    def test_send_enqueued_then_error_does_not_retry_or_send_cleanup(self):
        self.ready()
        review = self.ctl.review_action('hold', self.out)
        original = self.bus.send
        def failed(frame):
            original(frame)
            if frame.data[:6] == bytes.fromhex('2b4060002f01'):
                raise tool._canbus.CanBusError('injected failure after enable enqueued')
        with mock.patch.object(self.bus, 'send', side_effect=failed), self.assertRaises(tool.MotionError):
            self.ctl.execute_action(review, self.out)
        self.assertEqual(self.controls()[-1], 0x12f)
        self.assertEqual(self.controls().count(0x12f), 1)
        self.assert_quarantined_no_following_requests()

    def test_refresh_timeout_does_not_retry_or_send_cleanup(self):
        self.ready()
        self.hold()
        self.node.drop_reads.add((0x6041, 0))
        before = len(self.bus.sent)
        self.ctl.refresh(self.out)
        self.assertEqual(len(self.bus.sent), before+1)
        self.assert_quarantined_no_following_requests()

    def test_ui_1_enter_2_and_blocked_movement_keys(self):
        self.ready()
        app = self.app()
        for key in 'fbjprzasc':
            app.on_key(key, ord(key))
            self.assertEqual(app.current_view, 'main')
            self.assertIsNone(app.worker)
        app.on_key('1', ord('1'))
        self.drain(app)
        self.assertEqual(app.current_view, 'confirm')
        self.assertFalse(requests(self.bus, True))
        app.on_key('enter', 10)
        self.drain(app)
        self.assertEqual(self.controls()[-1], 0x12f)
        app.on_key('2', ord('2'))
        self.drain(app)
        self.assertEqual(self.controls()[-1], 0)
        self.assertEqual(self.controls().count(0x12f), 1)
        self.assert_no_motion()

    def test_ui_d_before_connection_and_before_od_authorization(self):
        app = self.app('rt')
        app.on_key('d', ord('d'))
        self.drain(app)
        self.assertFalse(self.bus.sent)
        self.online()
        self.bus.sent.clear()
        app.current_view = 'authorize'
        app.on_key('d', ord('d'))
        self.drain(app)
        self.assertEqual(self.controls(), [0])
        self.assertIsNone(self.ctl.origin)

    def test_ui_esc_and_q_pending_hold_never_enable(self):
        self.ready()
        app = self.app()
        app.on_key('e', ord('e'))
        self.drain(app)
        app.on_key('esc', 27)
        app.on_key('enter', 10)
        self.assertFalse(requests(self.bus, True))
        app.on_key('e', ord('e'))
        self.drain(app)
        app.on_key('q', ord('q'))
        self.drain(app)
        self.assertFalse(requests(self.bus, True))

    def test_80x24_screen_distinguishes_electrical_request_and_mechanics(self):
        self.ready()
        app = self.app()
        screens = {}
        for view in ('rt', 'authorize', 'main', 'help'):
            app.current_view = view
            screens[view] = '\n'.join(app.build().to_lines())
        app.pending = self.ctl.review_action('hold', self.out)
        app.action, app.current_view = 'hold', 'confirm'
        screens['confirm'] = '\n'.join(app.build().to_lines())
        self.assertIn('使能会产生保持力矩', screens['confirm'])
        self.assertIn('Enter 仅确认这一次', screens['confirm'])
        self.ctl.execute_action(app.pending, self.out)
        app.current_view = 'main'
        screens['enabled'] = '\n'.join(app.build().to_lines())
        self.assertIn('松闸请求：已使能保持', screens['enabled'])
        self.assertIn('机械抱闸状态：未知，需听声确认', screens['enabled'])
        self.ctl.safety_action('disable', self.out)
        screens['disabled'] = '\n'.join(app.build().to_lines())
        self.assertIn('抱闸请求：已失能', screens['disabled'])
        for name, text in screens.items():
            self.assertEqual(len(text.splitlines()), 24)
            self.assertTrue(all(_display_width(line) <= 80 for line in text.splitlines()), name)
        print('BRAKE 80x24 screens: startup/OD/main/help/confirm/enabled/disabled; request state is not mechanical feedback')

    def test_warning_2312_reset_reuses_guard_and_never_enables(self):
        self.node = ResetNode()
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        self.online()
        self.assertEqual(self.ctl.action_locks[1]['kind'], 'alarm_2312')
        self.bus.sent.clear()
        review = self.ctl.review_reset(self.out)
        app = self.app('confirm')
        app.action, app.pending = 'reset', review
        text = '\n'.join(app.build().to_lines())
        self.assertIn('Enter 仅确认本次复位', text)
        self.ctl.execute_reset(review, self.out)
        self.assertEqual(self.controls(), [0, 0x80, 0])
        self.assertFalse(self.ctl.proofs)
        self.assertIsNone(self.ctl.origin)
        self.assertFalse(self.ctl.blocked)

    def test_initial_partial_603f_2312_renders_first_failure_with_unknown_cw(self):
        self.node.set_value(0x603f, 0x2312)
        self.ctl.rt_ack = True
        self.ctl.scan(self.out)
        app = self.app()
        count = len(self.bus.sent)
        text = '\n'.join(app.build().to_lines())
        self.assertIn('当前实读状态未知', text)
        self.assertIn('非完整快照', text)
        self.assertIn('603F=0x2312（电机堵转）', text)
        self.assertIn('CW=未知（未知（本轮尚未读到6040h））', text)
        self.assertIn('电气停止：未执行', text)
        self.assertIn('1/e松闸 Enter确认 2/d立即抱闸请求', text)
        self.assertEqual(len(self.bus.sent), count, 'render must not read SDO')
        self.assertFalse(requests(self.bus, True))

    def test_background_603f_replaces_old_zero_as_first_failure_not_current_zero(self):
        import copy
        self.ready()
        self.hold()
        previous = copy.deepcopy(self.ctl.row(1)['values'])
        self.node.set_value(0x603f, 0x2312)
        self.ctl.refresh(self.out)
        app = self.app()
        count = len(self.bus.sent)
        text = '\n'.join(app.build().to_lines())
        self.assertIn('当前实读状态未知', text)
        self.assertIn('603F=0x2312（电机堵转）', text)
        self.assertIn('最后有效采样（历史）', text)
        self.assertIn('历史报警603F：0x0000（无错误码）', text)
        self.assertNotIn('\n│ 报警603F：0x0000', text)
        self.assertIn('电气停止：已确认失能；机械停止/抱闸未知', text)
        self.assertNotIn('松闸请求：已使能保持', text)
        self.assertEqual(self.ctl.row(1)['values'], previous)
        self.assertEqual(len(self.bus.sent), count, 'render must not read SDO')

    def test_first_status_fault_leaves_unread_603f_unknown_and_old_zero_historical(self):
        self.ready()
        self.node.set_value(0x6041, 0x2f)
        self.node.set_value(0x603f, 0xbeef)
        self.ctl.refresh(self.out)
        app = self.app()
        count = len(self.bus.sent)
        text = '\n'.join(app.build().to_lines())
        self.assertIn('603F=未知 6041=0x002F', text)
        self.assertIn('历史报警603F：0x0000（无错误码）', text)
        self.assertNotIn('BEEF', text)
        self.assertIn('当前实读状态未知', text)
        self.assertEqual(len(self.bus.sent), count)

    def test_cli_help_bad_args_import_and_no_tty_do_not_open_can(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as result:
            brake.main(['--help'])
        self.assertEqual(result.exception.code, 0)
        for args in (['--node','0'], ['--position'], ['--target','100'], ['--rows','23'], ['--manual-linear']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                brake.main(args)
        with mock.patch.object(brake, 'tool', tool), mock.patch.object(brake.BrakeApp, '_curses_usable', return_value=False), redirect_stderr(output):
            self.assertEqual(brake.main([]), 2)
        self.assertFalse(self.bus.sent)
        tool._std_open_active.assert_not_called()


if __name__ == '__main__':
    import unittest
    unittest.main()
