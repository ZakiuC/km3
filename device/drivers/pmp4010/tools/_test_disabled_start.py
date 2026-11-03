"""Bench-only Switched-on probe: local independent MockNode behaviour, no CAN socket."""
import copy
import json
from pathlib import Path
import time
from unittest import mock

from _test_bench import BenchFixture
import _test_bench as bench_tests
from _test_bench_support import BenchNode, DelayedBus, mockbus, position, requests, tool
from _test_position import Clock
from pmp4010_bench import DisabledStartReview
import pmp4010_tui as ui


class HaltProbeNode(BenchNode):
    """607A alignment never synthesizes ACK, bit10, or a new internal 6062."""
    def __init__(self):
        super().__init__()
        for index, value in ((0x6041, 0x260), (0x6064, -1470), (0x607a, 7800),
                             (0x6062, 7800), (0x6081, 500000)):
            self.set_value(index, value)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3/6.3.2/10.3
        self.accepted = False
        self.ignore_setpoint = self.keep_old_demand = self.stuck_ack = False

    def handle(self, frame, now):
        data = frame.data
        key = (data[1] | data[2] << 8, data[3])
        if data[0] == 0x40 and key in self.drop_reads:
            return
        mockbus.MockNode.handle(self, frame, now)
        if data[0] not in (0x2f, 0x2b, 0x23):
            return
        if key == (0x6040, 0):
            word = self.value(0x6040)
            state = {0: 0x40, 2: 7, 0x106: 0x21, 0x107: 0x23,
                     0x127: 0x23, 0x137: 0x23}[word]
            if word == 0x137 and not self.ignore_setpoint:
                self.accepted = True
                state |= 0x1000
                if not self.keep_old_demand:
                    self.od[0x6062, 0].data = self.od[0x607a, 0].data
            if word == 0x127 and self.accepted:
                state |= 0x1000 if self.stuck_ack else 0x400
            self.set_value(0x6041, state)
        if key in self.drop_write_ack:
            self._outbox.clear()


class DisabledStartPolicyTests(BenchFixture):
    def setUp(self):
        super().setUp()
        self.node = HaltProbeNode()
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        tool.connect_bus.return_value = self.bus
        self.clock = Clock()
        self.clock_patch = mock.patch('time.monotonic', self.clock.monotonic)
        self.sleep_patch = mock.patch('time.sleep', self.clock.sleep)
        self.clock_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.ctl.args.move_timeout = 4

    def probe_review(self, suffix='motion'):
        if self.ctl.context is None:
            self.authorized()
        else:
            self.ctl.refresh(self.out)
        self.ctl.args.motion_log = str(Path(self.tmp.name) / (suffix + '.jsonl'))
        self.bus.sent.clear()
        return self.ctl.review('probe_pp_start', 1, None, self.out)

    def records(self, suffix='motion'):
        return [json.loads(line) for line in (Path(self.tmp.name) / (suffix + '.jsonl')).read_text().splitlines()]

    def controls(self):
        return [value for index, _, value in requests(self.bus, True) if index == 0x6040]  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2

    def test_probe_success_keeps_halt_and_old_demand_until_new_ack(self):
        review = self.probe_review()
        self.assertIsInstance(review, DisabledStartReview)
        self.assertEqual(review['args'].velocity, 100)
        self.assertEqual(review['start'], -1470)
        self.assertEqual(review['target'], -1470)
        self.assertIn('保持Halt；不解除暂停；结束失能', '\n'.join(review['lines']))
        self.assertFalse(requests(self.bus, True))
        self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0])
        self.assertEqual(self.node.value(0x6064, signed=True), -1470)
        self.assertEqual(self.node.value(0x6062, signed=True), -1470)
        writes = requests(self.bus, True)
        self.assertEqual(writes[0], (0x607a, 0, (-1470) & 0xffffffff))
        self.assertFalse(any(i == 0x605d for i, _, _ in writes))
        self.assertTrue(all(w & 0x100 and not w & 8 for w in self.controls()[:-2]))
        self.assertEqual(sum(i == 0x607a for i, _, _ in writes), 1)
        records = self.records()
        samples = [r for r in records if r['event'] == 'disabled_start_sample']
        first_enable = next(i for i, r in enumerate(records) if r['event'] == 'probe_control_readback')
        self.assertTrue(all(r['values']['position_demand'] == 7800 for r in records[:first_enable] if r['event'] == 'disabled_start_sample'))
        self.assertEqual(len([r for r in samples if r['phase'].startswith('起步稳定采样')]), 5)
        self.assertEqual(len([r for r in samples if r['phase'].startswith('Halt保持核证采样')]), 5)
        result = next(r for r in reversed(records) if r['event'] == 'result')
        self.assertTrue(result['completed'] and result['disabled_confirmed'])
        self.assertFalse(result['halt_released'] or result['travel_executed'])
        print('HALT probe Mock: initial -1470/607A=6062=7800; align once; 106/107/127/137/127 (bit3=0,Halt=1); fresh ACK and demand; 5+5 samples; 2/0 cleanup; no travel')

    def test_enabled_absolute_relative_and_zero_still_require_startup_bit10(self):
        self.probe_review()
        self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.ctl.refresh(self.out)
        for action, value in [('relative', 36), ('absolute', -1434), ('zero', None)]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '目标到达标志'):
                self.ctl.review(action, 1, value, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_probe_never_raises_sub100_positive_velocity(self):
        self.probe_review()
        self.node.set_value(0x6081, 50)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        review = self.probe_review('speed50')
        self.assertEqual(review['args'].velocity, 50)
        self.ctl.execute(review, self.out)
        self.assertEqual(self.node.value(0x6081), 50)

    def test_probe_zero_velocity_profile_halt_initial_bit_or_option_invalid_refused(self):
        self.probe_review()
        for key, value in [('profile_velocity', 0), ('halt_option', 0), ('halt_option', 3),
                           ('controlword', 0x100), ('home_offset', 1), ('control_mode_select', 1),
                           ('modes_of_operation', 3), ('modes_of_operation_display', 3), ('actual_velocity', 1),
                           ('statusword', 0x268), ('statusword', 0x2260), ('statusword', 0x27),
                           ('software_position_limit', -1469), ('software_position_limit_max', -1471)]:
            spec = self.ctl.objects[key]
            addr = spec['index_value'], spec['sub']
            saved = self.node.od[addr].data
            self.node.set_value(addr[0], value, addr[1])
            self.ctl.refresh(self.out)
            self.bus.sent.clear()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.ctl.review('probe_pp_start', 1, None, self.out)
            self.assertFalse(requests(self.bus, True))
            self.node.od[addr].data = saved

    def test_review_tamper_cancel_copy_and_action_changes_do_zero_io(self):
        for number, mutate in enumerate((lambda r: r.update(target=1), lambda r: r['writes'][0].update(value=1),
                       lambda r: r.update(action='enable'), lambda r: r.pop('disabled_start_id'),
                       lambda r: r['disabled_start_before'].update(actual_position=1),
                       lambda r: setattr(r['args'], 'velocity', 101), lambda r: r.update(token='OTHER'))):
            review = self.probe_review('tamper' + str(number))
            mutate(review)
            count = len(self.bus.sent)
            with self.assertRaisesRegex(ValueError, '审阅'):
                self.ctl.execute(review, self.out)
            self.assertEqual(len(self.bus.sent), count)
        review = self.probe_review('cancel')
        self.ctl.discard_prepare()
        review.pop('disabled_start_id')
        review.pop('disabled_start_before')
        review['action'] = 'enable'
        for forged in (review, dict(review), copy.deepcopy(review)):
            count = len(self.bus.sent)
            with self.assertRaisesRegex(ValueError, '审阅'):
                self.ctl.execute(forged, self.out)
            self.assertEqual(len(self.bus.sent), count)

    def test_probe_action_without_seal_cannot_fall_through_to_other_execution(self):
        review = self.probe_review()
        forged = dict(review)
        forged.pop('disabled_start_id')
        forged.pop('disabled_start_before')
        del forged['args']._disabled_start_origin
        count = len(self.bus.sent)
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(forged, self.out)
        self.assertEqual(len(self.bus.sent), count)

    def test_success_and_failure_both_consume_seal(self):
        review = self.probe_review()
        self.ctl.execute(review, self.out)
        count = len(self.bus.sent)
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(self.bus.sent), count)
        self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        review = self.probe_review('failure')
        self.node.set_value(0x606c, 1)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
        with self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        count = len(self.bus.sent)
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(self.bus.sent), count)

    def test_superseding_review_cannot_replay_probe(self):
        review = self.probe_review()
        self.ctl.review('disable', 1, None, self.out)
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_frozen_settings_reason_and_od_changes_reject_writes(self):
        for field in ('settings', 'reason'):
            review = self.probe_review(field)
            oldsettings = self.ctl.settings.copy()
            oldreason = self.ctl.od_reason
            if field == 'settings':
                self.ctl.settings['max_displacement'] = 36
            else:
                self.ctl.od_reason = 'changed reason'
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertFalse(requests(self.bus, True))
            self.ctl.settings, self.ctl.od_reason = oldsettings, oldreason

    def test_each_frozen_critical_value_change_before_execute_rejects_zero_writes(self):
        for number, (key, value) in enumerate([('actual_position', -1469), ('actual_velocity', 1),
                   ('modes_of_operation', 3), ('modes_of_operation_display', 3), ('home_offset', 1),
                   ('control_mode_select', 1), ('profile_velocity', 499999), ('profile_end_velocity', 1),
                   ('profile_acceleration', 101), ('profile_deceleration', 101), ('halt_option', 2),
                   ('max_velocity', 101), ('max_motor_speed', 101), ('software_position_limit', -1469),
                   ('software_position_limit_max', -1471), ('encoder_increments', 131073)]):
            review = self.probe_review('changed' + str(number))
            spec = self.ctl.objects[key]
            addr = spec['index_value'], spec['sub']
            saved = self.node.od[addr].data
            self.node.set_value(addr[0], value, addr[1])
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertFalse(requests(self.bus, True))
            self.node.od[addr].data = saved

    def test_any_prewrite_five_sample_speed_position_mode_calibration_or_limit_change_refuses_writes(self):
        for number, key in enumerate(('actual_velocity', 'actual_position', 'modes_of_operation_display',
                                      'encoder_increments', 'home_offset', 'max_velocity')):
            review = self.probe_review('sample-change' + str(number))
            spec = self.ctl.objects[key]
            addr = spec['index_value'], spec['sub']
            saved = self.node.od[addr].data
            old = int.from_bytes(saved, 'little', signed=spec['signed'])
            event = self.out.event
            def observer(name, data):
                event(name, data)
                if name == 'disabled_start_sample' and data['phase'] == '起步稳定采样2/5':
                    self.node.set_value(addr[0], old + 1, addr[1])
            self.out.event = observer
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertFalse(requests(self.bus, True))
            self.out.event = event
            self.node.od[addr].data = saved

    def test_fault_first_status_read_is_not_hidden_by_later_normal_status(self):
        review = self.probe_review()
        original = self.bus.send
        counts = [0]
        def send(frame):
            if frame.data[:4] == bytes.fromhex('40416000'):
                counts[0] += 1
                self.node.set_value(0x6041, 0x268 if counts[0] == 1 else 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return original(frame)
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_switched_on_drift_fault_and_cancel_never_send_current_trigger(self):
        for number, failure in enumerate(('drift', 'fault', 'cancel')):
            review = self.probe_review(failure)
            event = self.out.event
            def observer(name, data):
                event(name, data)
                if name == 'probe_control_readback' and data['expected'] == 0x107:
                    if failure == 'drift':
                        self.node.set_value(0x6064, -1469)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
                    elif failure == 'fault':
                        self.node.set_value(0x6041, 0x28)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                    else:
                        self.out.cancel_event.set()
            self.out.event = observer
            with self.subTest(failure=failure), self.assertRaises((ValueError, KeyboardInterrupt)):
                self.ctl.execute(review, self.out)
            self.assertNotIn(0x137, self.controls())
            self.assertEqual(self.controls()[-2:], [2, 0])
            self.out.event = event
            self.out.cancel_event.clear()
            self.node.set_value(0x6064, -1470)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3

    def test_missing_ack_stuck_ack_or_old_6062_never_reports_success(self):
        for failure in ('ignore_setpoint', 'keep_old_demand', 'stuck_ack'):
            review = self.probe_review(failure)
            setattr(self.node, failure, True)
            self.node.accepted = False
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            result = next(r for r in reversed(self.records(failure)) if r['event'] == 'result')
            self.assertFalse(result['completed'])
            self.assertEqual(self.controls()[-2:], [2, 0])
            self.assertTrue(all(w & 0x100 and not w & 8 for w in self.controls()[:-2]))
            setattr(self.node, failure, False)
            self.node.set_value(0x6062, 7800)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3

    def test_ack_rising_before_current_trigger_is_rejected_at_first_or_last_status(self):
        for number, phase in enumerate(('before_target', 'before_trigger', '控制字前未使能Halt当前位置新命令')):
            for observed in (1, 2):
                self.node.accepted = False
                self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                review = self.probe_review('earlyack{}-{}'.format(number, observed))
                original_guard = self.ctl._disabled_start_guard
                original_send = self.bus.send
                active = [False]
                reads = [0]
                def factory(*args):
                    real_guard = original_guard(*args)
                    def guarded(session, current_phase, **kwargs):
                        active[0] = current_phase == phase
                        return real_guard(session, current_phase, **kwargs)
                    return guarded
                def send(frame):
                    if active[0] and frame.data[:4] == bytes.fromhex('40416000'):
                        reads[0] += 1
                        self.node.set_value(0x6041, 0x1023 if reads[0] == observed else 0x23)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.3
                    return original_send(frame)
                with self.subTest(phase=phase, observed=observed), mock.patch.object(self.ctl, '_disabled_start_guard', side_effect=factory), mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, 'ACK'):
                    self.ctl.execute(review, self.out)
                self.assertNotIn(0x137, self.controls())
                self.assertEqual(self.controls()[-2:], [2, 0])

    def test_final_halt_window_rechecks_zero_speed_position_demand_and_ack(self):
        for key, value in (('actual_velocity', 1), ('actual_position', -1469), ('position_demand', 7800), ('statusword', 0x1023)):
            review = self.probe_review(key)
            event = self.out.event
            def observer(name, data):
                event(name, data)
                if name == 'disabled_start_sample' and data['phase'] == 'Halt保持核证采样2/5':
                    spec = self.ctl.objects[key]
                    self.node.set_value(spec['index_value'], value, spec['sub'])
            self.out.event = observer
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertEqual(self.controls()[-2:], [2, 0])
            self.out.event = event
            self.node.set_value(0x606c, 0)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.set_value(0x6064, -1470)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            self.node.set_value(0x6062, 7800)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.accepted = False

    def test_target_mismatch_or_timeout_stops_before_switch_on(self):
        for failure in ('mismatch', 'timeout'):
            review = self.probe_review(failure)
            original = self.bus.send
            def send(frame):
                result = original(frame)
                if frame.data[:4] == bytes.fromhex('237a6000'):
                    if failure == 'mismatch':
                        self.node.set_value(0x607a, 123)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
                    else:
                        self.node.drop_reads.add((0x607a, 0))
                return result
            with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(Exception):
                self.ctl.execute(review, self.out)
            self.assertNotIn(0x106, self.controls())
            if failure == 'timeout':
                self.assertTrue(self.ctl.row(1)['quarantined'])
                self.assertEqual(len(requests(self.bus, True)), 1)
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3

    def test_controlword_readback_mismatch_never_reaches_current_trigger(self):
        review = self.probe_review()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6], 'little') == 0x106:
                self.node.set_value(0x6040, 6)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, '控制字读回'):
            self.ctl.execute(review, self.out)
        self.assertNotIn(0x137, self.controls())
        self.assertEqual(self.controls()[-2:], [2, 0])

    def test_lost_halt_shutdown_ack_quarantines_without_new_wire_or_replacement(self):
        review = self.probe_review()
        self.node.drop_write_ack.add((0x6040, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106])
        self.assertTrue(self.ctl.row(1)['quarantined'])
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)

    def test_cleanup_disable_failure_cannot_become_success(self):
        review = self.probe_review()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and frame.data[4:6] == b'\0\0':
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertFalse(result['completed'] or result['disabled_confirmed'])
        self.assertEqual(self.controls()[-2:], [2, 0])

    def test_lost_first_download_ack_quarantines_without_cleanup_wires_or_retry(self):
        review = self.probe_review()
        self.node.drop_write_ack.add((0x607a, 0))
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        self.assertEqual(len(requests(self.bus, True)), 1)
        count = len(self.bus.sent)
        self.node.drop_write_ack.clear()
        self.ctl.refresh(self.out)
        self.ctl.scan(self.out)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)

    def test_overall_deadline_includes_both_sampling_windows(self):
        self.ctl.args.move_timeout = .5
        review = self.probe_review()
        with self.assertRaisesRegex(ValueError, '整体时限'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_normal_probe_cannot_succeed_after_cleanup_velocity_or_position_change(self):
        review = self.probe_review()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and frame.data[4:6] == b'\0\0':
                self.node.set_value(0x606c, 1)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertFalse(result['completed'])
        self.assertEqual(self.controls()[-2:], [2, 0])

    def test_final_bench_read_following_error_cannot_override_inner_success(self):
        review = self.probe_review()
        event = self.out.event
        def observer(name, data):
            event(name, data)
            if name == 'result' and data['completed']:
                self.node.set_value(0x6041, 0x2040)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3/6.3.4
        self.out.event = observer
        with self.assertRaisesRegex(ValueError, '跟随误差'):
            self.ctl.execute(review, self.out)
        events = []
        while not self.out.events.empty():
            events.append(self.out.events.get_nowait())
        results = [e[2] for e in events if e[0] == 'event' and e[1] == 'bench_result']
        self.assertFalse(results[-1]['success'])
        self.assertEqual(self.controls()[-2:], [2, 0])

    def test_unenabled_word_policy_rejects_enable_and_halt_release_before_download(self):
        self.probe_review()
        for word in (0x10f, 0x12f, 0x13f, 0x2f, 0x3f, 6, 7, 0x27, 0x37):
            with self.subTest(word=hex(word)), self.assertRaisesRegex(ValueError, 'bit3|Halt'):
                position._require_unenabled_probe_word(word)
        for word in (0x106, 0x107, 0x127, 0x137):
            position._require_unenabled_probe_word(word)
        self.assertFalse(requests(self.bus, True))

    def test_127_readback_as_12f_cannot_upgrade_operation(self):
        review = self.probe_review()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6], 'little') == 0x127:
                self.node.set_value(0x6040, 0x12f)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, 'bit3/Halt'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 2, 0])
        self.assertTrue(all(not w & 8 for w in self.controls()))

    def test_first_or_last_initial_status_unexpected_enabled_owes_cleanup_without_alignment(self):
        for observed in (1, 2):
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            review = self.probe_review('unexpected' + str(observed))
            original = self.bus.send
            reads = [0]
            cleaning = [False]
            def send(frame):
                if frame.data[:4] == bytes.fromhex('2b406000'):
                    cleaning[0] = True
                if not cleaning[0] and frame.data[:4] == bytes.fromhex('40416000'):
                    reads[0] += 1
                    self.node.set_value(0x6041, 0x27 if reads[0] == observed else 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                return original(frame)
            with self.subTest(observed=observed), mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, 'Operation enabled'):
                self.ctl.execute(review, self.out)
            self.assertEqual(self.controls(), [2, 0])
            self.assertTrue(all(i == 0x6040 for i, _, _ in requests(self.bus, True)))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2
            result = next(r for r in reversed(self.records('unexpected' + str(observed))) if r['event'] == 'result')
            self.assertFalse(result['completed'])
            self.assertTrue(result['disabled_confirmed'] and result['position_unchanged'] and result['zero_velocity_confirmed'])
            self.assertTrue(result['operation_enabled_observed'])

    def test_unexpected_enabled_cleanup_failure_is_not_retried(self):
        review = self.probe_review()
        original = self.bus.send
        initial = [True]
        def send(frame):
            if initial[0] and frame.data[:4] == bytes.fromhex('40416000'):
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                initial[0] = False
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and frame.data[4:6] == b'\0\0':
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [2, 0])
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertFalse(result['completed'] or result['disabled_confirmed'])
        self.assertTrue(result['independent_estop_required'])

    def test_switched_on_handshake_cannot_tolerate_enabled_as_transition(self):
        review = self.probe_review()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6], 'little') == 0x127:
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 2, 0])
        self.assertNotIn(0x137, self.controls())

    def test_disabled_position_and_velocity_results_are_independent(self):
        for key, value in (('actual_position', -1466), ('actual_velocity', 1)):
            self.node.set_value(0x6041, 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            self.node.accepted = False
            review = self.probe_review('split-' + key)
            event = self.out.event
            def observer(name, data):
                event(name, data)
                if name == 'probe_control_readback' and data['expected'] == 0x107:
                    spec = self.ctl.objects[key]
                    self.node.set_value(spec['index_value'], value, spec['sub'])
            self.out.event = observer
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            result = next(r for r in reversed(self.records('split-' + key)) if r['event'] == 'result')
            self.assertTrue(result['disabled_confirmed'])
            self.assertEqual(result['position_unchanged'], key != 'actual_position')
            self.assertEqual(result['zero_velocity_confirmed'], key != 'actual_velocity')
            self.assertFalse(result['completed'])
            self.assertTrue(result['independent_estop_required'])
            events = []
            while not self.out.events.empty():
                events.append(self.out.events.get_nowait())
            outer = [e[2] for e in events if e[0] == 'event' and e[1] == 'bench_result'][-1]
            self.assertTrue(outer['disabled_confirmed'])
            self.assertEqual(outer['position_unchanged'], key != 'actual_position')
            self.assertEqual(outer['zero_velocity_confirmed'], key != 'actual_velocity')
            self.assertFalse(outer['success'])
            self.out.event = event
            self.node.set_value(0x6064, -1470)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3
            self.node.set_value(0x606c, 0)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:10.3

    def test_quarantine_result_uses_none_for_unread_cleanup_fields(self):
        review = self.probe_review()
        self.node.drop_write_ack.add((0x607a, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertIsNone(result['disabled_confirmed'])
        self.assertIsNone(result['position_unchanged'])
        self.assertIsNone(result['zero_velocity_confirmed'])
        self.assertFalse(result['completed'])
        self.assertTrue(result['independent_estop_required'])
        self.assertEqual(len(requests(self.bus, True)), 1)

    def test_total_probe_deadline_cannot_exceed_fifteen_seconds(self):
        self.ctl.args.move_timeout = 100
        review = self.probe_review()
        event = self.out.event
        def observer(name, data):
            event(name, data)
            if name == 'disabled_start_sample' and data['phase'] == '起步稳定采样1/5':
                self.clock.now += 15
        self.out.event = observer
        with self.assertRaisesRegex(ValueError, '整体时限|整体超时'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_newly_enabled_postflight_after_confirmed_cleanup_gets_new_bounded_stop(self):
        review = self.probe_review()
        event = self.out.event
        def observer(name, data):
            event(name, data)
            if name == 'result' and data['completed']:
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.out.event = observer
        with self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0, 2, 0])
        events = []
        while not self.out.events.empty():
            events.append(self.out.events.get_nowait())
        outer = [e[2] for e in events if e[0] == 'event' and e[1] == 'bench_result'][-1]
        self.assertTrue(outer['disabled_confirmed'])
        self.assertFalse(outer['success'])
        self.assertIsNone(outer['position_unchanged'])
        self.assertIsNone(outer['zero_velocity_confirmed'])

    def test_inner_postcleanup_new_enabled_gets_exactly_one_extra_stop(self):
        review = self.probe_review()
        original = tool.MotionSession.stop_after_failure
        stops = []
        def stop(session):
            confirmed = original(session)
            stops.append(confirmed)
            if len(stops) == 1:
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return confirmed
        with mock.patch.object(tool.MotionSession, 'stop_after_failure', stop), self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(stops, [True, True])
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0, 2, 0])
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertTrue(result['operation_enabled_observed'] and result['disabled_confirmed'])
        self.assertTrue(result['position_unchanged'] and result['zero_velocity_confirmed'])
        self.assertFalse(result['completed'])

    def test_repeated_new_enabled_observation_does_not_repeat_extra_stop(self):
        review = self.probe_review()
        original = tool.MotionSession.stop_after_failure
        stops = []
        def stop(session):
            confirmed = original(session)
            stops.append(confirmed)
            self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return confirmed
        with mock.patch.object(tool.MotionSession, 'stop_after_failure', stop), self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(stops, [True, True])
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0, 2, 0])
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertTrue(result['operation_enabled_observed'])
        self.assertFalse(result['disabled_confirmed'] or result['completed'])

    def test_extra_stop_quarantine_never_sends_disable_or_retries(self):
        review = self.probe_review()
        original = tool.MotionSession.stop_after_failure
        stops = []
        def stop(session):
            confirmed = original(session)
            stops.append(confirmed)
            if len(stops) == 1:
                self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                self.node.drop_write_ack.add((0x6040, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.2
            return confirmed
        with mock.patch.object(tool.MotionSession, 'stop_after_failure', stop), self.assertRaisesRegex(ValueError, 'Operation enabled'):
            self.ctl.execute(review, self.out)
        self.assertEqual(stops, [True, False])
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0, 2])
        result = next(r for r in reversed(self.records()) if r['event'] == 'result')
        self.assertTrue(result['operation_enabled_observed'])
        self.assertIsNone(result['disabled_confirmed'])
        self.assertIsNone(result['position_unchanged'])
        self.assertIsNone(result['zero_velocity_confirmed'])
        self.assertFalse(result['completed'])
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)

    def test_tui_result_reports_disabled_true_position_false_probe_failed(self):
        app = ui.PmpApp(tool, self.args)
        app.initial_snapshot = False
        app.next_refresh = float('inf')
        app.events.put(('event', 'result', dict(action='probe_pp_start', completed=False,
            disabled_confirmed=True, position_unchanged=False, zero_velocity_confirmed=True)))
        app.poll()
        self.assertIn('失能确认=True', app.recovery)
        self.assertIn('位置保持=False', app.recovery)
        self.assertIn('零速确认=True', app.recovery)
        self.assertIn('核证=未通过', app.recovery)

    def test_real_five_sample_window_uses_at_least_point8_seconds(self):
        self.clock_patch.stop()
        self.sleep_patch.stop()
        review = self.probe_review()
        client = tool._canopen.SdoClient(self.bus, 1, timeout=self.args.timeout)
        session = tool.MotionSession(client, self.out, self.args.state_timeout)
        session.cancel_event = self.out.cancel_event
        journal = position.Journal(str(Path(self.tmp.name) / 'real-samples.jsonl'), 'mock', {})
        self.addCleanup(journal.close)
        guard = self.ctl._disabled_start_guard(review, review['args'], client, self.out, journal)
        start = time.monotonic()
        self.ctl._disabled_start_samples(session, guard)
        self.assertGreaterEqual(time.monotonic() - start, .8)
        self.assertFalse(requests(self.bus, True))

    def app(self):
        self.probe_review()
        app = ui.PmpApp(tool, self.args)
        app.controller = self.ctl
        app.initial_snapshot = False
        app.next_refresh = float('inf')
        app.current_view = 'detail'
        return app

    def test_tui_B_review_exact_token_second_F5_then_probe(self):
        app = self.app()
        press = bench_tests.BenchInteractionTests.press
        press(app, 'b')
        self.assertEqual(app.action, 'probe_pp_start')
        press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        self.assertIn('PROBE_PP_START mock-bench 1 -1470', app.pending['token'])
        self.assertFalse(requests(self.bus, True))
        app.input_field._draft = app.pending['token']
        press(app, 'f5')
        app.worker.join(3)
        app.poll()
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127, 2, 0])
        self.assertIn('失能确认=True', app.recovery)

    def test_tui_cancel_invalidates_review_without_writes(self):
        app = self.app()
        press = bench_tests.BenchInteractionTests.press
        press(app, 'b')
        press(app, 'f5')
        frozen = app.pending
        app.input_field._draft = 'WRONG'
        press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        press(app, 'esc')
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(frozen, self.out)
        self.assertFalse(requests(self.bus, True))
