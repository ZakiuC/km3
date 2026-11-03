"""Speed-mode credentials against an independent expedited-SDO wire peer."""
import threading
import unittest
from unittest import mock

import _test_pmp4010 as support
import pmp4010_velocity as velocity

tool = support.tool


class VelocityEnableTests(unittest.TestCase):
    def setUp(self):
        self.clock = support.MotionClock()
        self.bus = support.MotionBus(self.clock)
        self.bus.objects[0x6060, 0] = self.bus.objects[0x6061, 0] = b'\x03'
        patch = mock.patch.object(tool, 'time', self.clock)
        patch.start()
        self.addCleanup(patch.stop)
        self.client = support.canopen.SdoClient(self.bus, 1, timeout=.001)
        self.session = tool.MotionSession(self.client, mock.Mock(), .08)

    def prepare(self, value=0):
        self.session.set_target_velocity(value)

    def assert_unprepared(self):
        before = len(self.bus.downloads())
        with self.assertRaises(tool.MotionError):
            self.session.enable()
        self.assertEqual(len(self.bus.downloads()), before)
        self.assertIsNone(self.session._velocity_prepared)

    def test_unprepared_residual_and_already_enabled_refuse_every_enable_family(self):
        for mode in (3, 9):
            self.bus.objects[0x6061, 0] = bytes([mode])
            for status in (0x40, 0x27):
                self.bus.sw = status
                self.assert_unprepared()
                for word in (0x0f, 0x1f, 0x3f, 0x7f):
                    with self.assertRaises(tool.MotionError):
                        self.session.control(word, self.session.deadline())
        self.assertFalse(self.bus.downloads())
        self.assertEqual(int.from_bytes(self.bus.objects[0x60ff, 0], 'little'), 6553600)

    def test_zero_write_readback_before_first_enable_frame_and_one_use(self):
        for mode in (3, 9):
            self.bus.sent.clear()
            self.bus.sw = 0x40
            self.bus.objects[0x6061, 0] = bytes([mode])
            self.prepare()
            self.session.enable()
            self.assertEqual(self.bus.controls(), [6, 7, 15])
            first_enable = next(i for i, frame in enumerate(self.bus.sent)
                                if frame.data[:4] == b'\x2b\x40\x60\x00')
            before = [frame.data[:4] for frame in self.bus.sent[:first_enable]]
            self.assertGreaterEqual(before.count(b'\x23\xff\x60\x00'), 2)
            self.assertGreaterEqual(before.count(b'\x40\xff\x60\x00'), 3)
            self.assertEqual(self.bus.objects[0x60ff, 0], bytes(4))
            self.assert_unprepared()
        print('Python PV/CSV: explicit 60FFh=0 write/readback precedes 6/7/0F; credential consumed once')

    def test_all_generic_enable_words_require_and_consume_fresh_credential(self):
        for word in (0x0f, 0x1f, 0x3f, 0x7f):
            self.prepare()
            self.session.control(word, self.session.deadline())
            self.assertEqual(self.bus.controls()[-1], word)
            with self.assertRaises(tool.MotionError):
                self.session.control(word, self.session.deadline())

    def test_explicit_rpm_entry_and_two_signed_limits(self):
        self.session.set_target_velocity_rpm('46875/2048')
        self.assertEqual(int.from_bytes(self.bus.objects[0x60ff, 0], 'little', signed=True), 50000)
        with self.assertRaisesRegex(ValueError, '不是整数'):
            self.session.set_target_velocity_rpm('1')
        for target in (6553601, -6553601):
            with self.assertRaisesRegex(tool.MotionError, '双重上限'):
                self.prepare(target)
        self.bus.objects[0x607f, 0] = (9).to_bytes(4, 'little')
        for target in (10, -10):
            with self.assertRaisesRegex(tool.MotionError, '双重上限'):
                self.prepare(target)
        self.prepare(-9)

    def test_each_calibration_upload_failure_refuses_prepare_and_enable(self):
        for key in ((0x608f, 1), (0x608f, 2), (0x6091, 1), (0x6091, 2)):
            self.bus.abort = (0x40, key)
            before = len(self.bus.downloads())
            with self.assertRaises(tool.MotionError):
                self.prepare()
            self.assertEqual(len(self.bus.downloads()), before)
            self.bus.abort = None
            self.assert_unprepared()
            self.prepare()
            self.bus.abort = (0x40, key)
            before = len(self.bus.downloads())
            with self.assertRaises(tool.MotionError):
                self.session.enable()
            self.assertEqual(len(self.bus.downloads()), before)
            self.bus.abort = None

    def test_target_write_abort_read_abort_and_mismatch_never_create_credential(self):
        for cs in (0x23, 0x40):
            self.bus.abort = (cs, (0x60ff, 0))
            with self.assertRaises(tool.MotionError):
                self.prepare()
            self.assertTrue(self.session.needs_stop)
            self.assertIsNone(self.session._velocity_prepared)
            self.assertFalse(self.bus.controls())
        self.bus.abort = None
        original = self.bus.send
        def mismatch(frame):
            if frame.data[:4] == b'\x40\xff\x60\x00':
                self.bus.objects[0x60ff, 0] = (1).to_bytes(4, 'little')
            return original(frame)
        with mock.patch.object(self.bus, 'send', side_effect=mismatch):
            with self.assertRaisesRegex(tool.MotionError, '写读回不一致'):
                self.prepare()
        self.assertIsNone(self.session._velocity_prepared)

    def test_pre_enable_rewrite_failure_precedes_any_controlword(self):
        for cs in (0x23, 0x40):
            self.prepare()
            self.bus.sent.clear()
            self.bus.abort = (cs, (0x60ff, 0))
            with self.assertRaises(tool.MotionError):
                self.session.enable()
            self.assertFalse(self.bus.controls())
            self.assertIsNone(self.session._velocity_prepared)
            self.bus.abort = None

    def test_external_target_calibration_mode_and_limit_changes_reject_old_credential(self):
        changes = [((0x60ff, 0), (1).to_bytes(4, 'little')),
                   ((0x608f, 1), (131073).to_bytes(4, 'little')),
                   ((0x6091, 2), (2).to_bytes(4, 'little')),
                   ((0x6061, 0), b'\x09'), ((0x6061, 0), b'\x01'), ((0x2002, 1), b'\x04\x00')]
        for key, value in changes:
            self.prepare()
            original = self.bus.objects[key]
            self.bus.objects[key] = value
            self.assert_unprepared()
            self.bus.objects[key] = original
        self.prepare(100)
        self.bus.objects[0x607f, 0] = (99).to_bytes(4, 'little')
        self.assert_unprepared()
        self.assertFalse(self.bus.controls())

    def test_stop_disable_reset_public_shutdown_switch_on_and_mode_writes_invalidate(self):
        for word in (0, 2, 0x80, 6, 7):
            self.prepare()
            self.session.control(word, self.session.deadline())
            self.assert_unprepared()
        for index, value in ((0x6060, 3), (0x2002, 0)):
            self.prepare()
            method = self.client.write_uint8 if index == 0x6060 else self.client.write_uint16
            self.session.call(self.session.deadline(), '显式模式配置', method, index, 0 if index == 0x6060 else 1, value)
            self.assert_unprepared()

    def test_client_node_replacement_and_new_session_do_not_inherit_credential(self):
        self.prepare()
        new_session = tool.MotionSession(self.client, mock.Mock(), .08)
        with self.assertRaises(tool.MotionError):
            new_session.enable()
        self.session.client = support.canopen.SdoClient(self.bus, 1, timeout=.001)
        self.assert_unprepared()
        self.prepare()
        self.session.client.node_id = 2
        self.assert_unprepared()

    def test_timeout_quarantine_cancellation_and_fault_invalidate(self):
        self.prepare()
        self.bus.silence = (0x40, (0x6041, 0))
        with self.assertRaises(tool.MotionError):
            self.session.status(self.session.deadline())
        self.assertTrue(self.client.quarantined)
        before = len(self.bus.sent)
        self.assert_unprepared()
        self.assertEqual(len(self.bus.sent), before)
        self.client = support.canopen.SdoClient(self.bus, 1, timeout=.001)
        self.session = tool.MotionSession(self.client, mock.Mock(), .08)
        self.bus.silence = None
        self.prepare()
        self.session.cancel_event = threading.Event()
        self.session.cancel_event.set()
        with self.assertRaises(KeyboardInterrupt):
            self.session.enable()
        self.assertIsNone(self.session._velocity_prepared)
        self.session.cancel_event = None
        self.prepare()
        self.bus.sw = 8
        with self.assertRaisesRegex(tool.MotionError, '故障'):
            self.session.enable()
        self.assertNotIn(0x80, self.bus.controls())

    def test_velocity_mode_drift_during_sequence_cannot_become_pp_bypass(self):
        self.prepare()
        original = self.bus.send
        def change_mode(frame):
            result = original(frame)
            if frame.data[:4] == b'\x2b\x40\x60\x00' and frame.data[4] == 6:
                self.bus.objects[0x6061, 0] = b'\x01'
            return result
        with mock.patch.object(self.bus, 'send', side_effect=change_mode):
            with self.assertRaisesRegex(tool.MotionError, '模式已变化'):
                self.session.enable()
        self.assertEqual(self.bus.controls(), [6])

    def test_prepared_velocity_mode_to_torque_rejects_before_any_controlword(self):
        for original_mode in (3, 9):
            self.bus.objects[0x6061, 0] = bytes([original_mode])
            self.prepare()
            self.bus.sent.clear()
            self.bus.objects[0x6061, 0] = b'\x04'
            with self.assertRaisesRegex(tool.MotionError, '运行模式已变化'):
                self.session.enable()
            self.assertFalse(self.bus.downloads())
            self.assertIsNone(self.session._velocity_prepared)

    def test_missing_library_prepares_no_download_or_cleanup_responsibility(self):
        with mock.patch.object(velocity, 'require_velocity_library', side_effect=ValueError('缺少本地速度换算库')):
            with self.assertRaisesRegex(ValueError, '缺少本地'):
                self.prepare()
        self.assertFalse(self.bus.sent)
        self.assertFalse(self.session.needs_stop)

    def test_modes_two_and_seven_still_reject_every_generic_enable_word(self):
        for mode in (2, 7):
            self.bus.objects[0x6061, 0] = bytes([mode])
            for word in (0x0f, 0x1f, 0x3f, 0x7f):
                with self.assertRaises(tool.MotionError):
                    self.session.control(word, self.session.deadline())
            with self.assertRaises(tool.MotionError):
                self.session.enable()
        self.assertFalse(self.bus.downloads())

    def test_csv_zero_target_nonzero_offset_refuses_units_and_rpm_without_writes(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        for offset in (1, -1, 0x7fffffff):
            self.bus.objects[0x60b1, 0] = offset.to_bytes(4, 'little', signed=True)
            for setter in (self.session.set_target_velocity, self.session.set_target_velocity_rpm):
                with self.assertRaisesRegex(tool.MotionError, '60B1h'):
                    setter(0)
                self.assertIsNone(self.session._velocity_prepared)
                self.assertFalse(self.bus.downloads())
        self.assertFalse(self.session.needs_stop)

    def test_csv_offset_abort_and_bad_width_refuse_prepare_and_enable(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        for prepared in (False, True):
            for failure in ('abort', 'width'):
                self.bus.objects[0x60b1, 0] = bytes(4)
                if prepared:
                    self.prepare()
                self.bus.sent.clear()
                if failure == 'abort':
                    self.bus.abort = (0x40, (0x60b1, 0))
                else:
                    self.bus.objects[0x60b1, 0] = bytes(2)
                with self.assertRaises(tool.MotionError):
                    self.session.enable() if prepared else self.prepare()
                self.assertFalse(self.bus.downloads())
                self.assertIsNone(self.session._velocity_prepared)
                self.bus.abort = None
                self.bus.objects[0x60b1, 0] = bytes(4)
                self.assert_unprepared()

    def test_csv_offset_timeout_quarantines_and_never_retries(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        self.bus.silence = (0x40, (0x60b1, 0))
        with self.assertRaises(tool.MotionError):
            self.prepare()
        self.assertTrue(self.client.quarantined)
        self.assertFalse(self.bus.downloads())
        before = len(self.bus.sent)
        self.bus.silence = None
        for operation in (self.prepare, self.session.enable, self.session.enable_zero_velocity):
            with self.assertRaises(tool.MotionError):
                operation()
            self.assertEqual(len(self.bus.sent), before)

    def test_csv_offset_drift_refuses_all_generic_enable_words_and_zero_cli(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        for word in (0x0f, 0x1f, 0x3f, 0x7f):
            self.bus.objects[0x60b1, 0] = bytes(4)
            self.prepare()
            self.bus.sent.clear()
            self.bus.objects[0x60b1, 0] = (1).to_bytes(4, 'little')
            with self.assertRaisesRegex(tool.MotionError, '60B1h'):
                self.session.control(word, self.session.deadline())
            self.assertFalse(self.bus.downloads())
            self.bus.objects[0x60b1, 0] = bytes(4)
            self.assert_unprepared()
        self.bus.objects[0x60b1, 0] = (1).to_bytes(4, 'little')
        with self.assertRaisesRegex(tool.MotionError, '60B1h'):
            self.session.enable_zero_velocity()
        self.assertFalse(self.bus.controls())

    def test_csv_offset_drift_after_each_sequence_step_blocks_the_next(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        original = self.bus.send
        for stage in (0, 6, 7):
            self.bus.sw = 0x40
            self.bus.objects[0x60b1, 0] = bytes(4)
            self.prepare()
            self.bus.sent.clear()
            def drift(frame):
                result = original(frame)
                if ((stage == 0 and frame.data[:4] == b'\x40\x41\x60\x00') or
                        (frame.data[:4] == b'\x2b\x40\x60\x00' and frame.data[4] == stage)):
                    self.bus.objects[0x60b1, 0] = (1).to_bytes(4, 'little')
                return result
            with mock.patch.object(self.bus, 'send', side_effect=drift):
                with self.assertRaisesRegex(tool.MotionError, '60B1h'):
                    self.session.enable()
            self.assertEqual(self.bus.controls(), [] if stage == 0 else [6] if stage == 6 else [6, 7])
            self.assertIsNone(self.session._velocity_prepared)

    def test_csv_offset_drift_during_target_write_does_not_create_credential(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        original = self.bus.send
        def drift(frame):
            result = original(frame)
            if frame.data[:4] == b'\x23\xff\x60\x00':
                self.bus.objects[0x60b1, 0] = (1).to_bytes(4, 'little')
            return result
        with mock.patch.object(self.bus, 'send', side_effect=drift):
            with self.assertRaisesRegex(tool.MotionError, '60B1h'):
                self.prepare()
        self.assertFalse(self.bus.controls())
        self.bus.objects[0x60b1, 0] = bytes(4)
        self.assert_unprepared()

    def test_pv_does_not_require_or_read_csv_offset(self):
        del self.bus.objects[0x60b1, 0]
        self.bus.silence = (0x40, (0x60b1, 0))
        self.prepare()
        self.session.enable()
        self.assertEqual(self.bus.controls(), [6, 7, 15])
        self.assertFalse(any(f.data[1:4] == b'\xb1\x60\x00' for f in self.bus.sent))

    def test_csv_offset_writes_are_refused_and_observed_drift_consumes_credential(self):
        self.bus.objects[0x6061, 0] = b'\x09'
        self.prepare()
        self.bus.objects[0x60b1, 0] = (1).to_bytes(4, 'little')
        self.session.call(self.session.deadline(), '读取偏置', self.client.read, 0x60b1, 0)
        self.bus.objects[0x60b1, 0] = bytes(4)
        self.assert_unprepared()
        for status in (0x40, 0x27):
            self.bus.sw = status
            self.bus.sent.clear()
            for value in (0, 1):
                with self.assertRaisesRegex(tool.MotionError, '不支持写入'):
                    self.session.call(self.session.deadline(), '写偏置', self.client.write_uint32, 0x60b1, 0, value)
                self.assertFalse(self.bus.sent)


if __name__ == '__main__':
    unittest.main()
