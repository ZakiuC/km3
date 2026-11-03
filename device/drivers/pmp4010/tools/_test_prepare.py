"""Disabled PP preparation: isolated MockBus and independent literal SDO peer."""
import copy
import json
from pathlib import Path
import time
from unittest import mock
import unittest

import _test_bench as bench_tests
BenchFixture = bench_tests.BenchFixture
from _test_bench_support import requests, tool, position
from _test_position import Peer as IndependentPeer, Clock
import pmp4010_tui as ui


class PrepareTests(BenchFixture):
    def prepared(self):
        self.authorized()
        self.node.set_value(0x6041, 0x0260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.node.set_value(0x607a, 7800)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        self.node.set_value(0x6081, 500000)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        self.ctl.refresh(self.out)
        self.bus.sent.clear()
        return self.ctl.review('prepare_pp', 1, 999999, self.out)

    def records(self):
        return [json.loads(line) for line in Path(self.args.motion_log).read_text().splitlines()]

    def test_bit10_zero_success_exact_two_writes_and_no_exit_control(self):
        review = self.prepared()
        self.assertEqual([w['value'] for w in review['writes']], [-1464, 100])
        self.assertEqual(review['target'], -1464)
        self.assertIn('不保证bit10', '\n'.join(review['lines']))
        before = time.monotonic()
        self.ctl.execute(review, self.out)
        self.assertGreaterEqual(time.monotonic() - before, .8)
        self.assertEqual(requests(self.bus, True), [(0x607a, 0, 0xfffffa48), (0x6081, 0, 100)])  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        samples = [r for r in self.records() if r['event'] == 'prepare_sample']
        self.assertEqual(len(samples), 9)
        result = self.records()[-1]
        self.assertTrue(result['success'] and result['disabled_confirmed'])
        self.assertFalse(result['target_reached'])
        self.assertFalse(self.ctl.enabled_sessions)
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent), count)
        self.assertTrue(all(f.arbitration_id != 0 for f in self.bus.sent))
        print('PREPARE MockBus: 5 stable samples >=0.8s; 607A=start,6081=100 only; bit10=0 retained; exit controls=0')

    def test_review_wrong_state_mode_offset_speed_and_limits_are_refused(self):
        self.prepared()
        cases = [(0x6041, 0x427, 0), (0x6041, 0x268, 0), (0x6060, 3, 0), (0x6061, 3, 0),
                 (0x2002, 1, 1), (0x606c, 1, 0), (0x607c, 1, 0), (0x6081, 99, 0),
                 (0x607f, 99, 0), (0x6080, 0, 0)]  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3/6.3.2/10.3
        for index, value, sub in cases:
            original = self.node.od[index, sub].data
            with self.subTest(index=hex(index), value=value):
                self.node.set_value(index, value, sub)
                self.ctl.refresh(self.out)
                with self.assertRaises(ValueError):
                    self.ctl.review('prepare_pp', 1, None, self.out)
                self.assertFalse(requests(self.bus, True))
            self.node.od[index, sub].data = original

    def test_enabled_movements_and_zero_still_require_target_reached(self):
        self.prepared()
        self.node.set_value(0x6041, 0x27)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.ctl.refresh(self.out)
        for action, value in [('relative', 36), ('absolute', -1428), ('zero', None)]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'target reached'):
                self.ctl.review(action, 1, value, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_prepare_keeps_od_positive_budget_envelope_and_one_degree_guards(self):
        self.prepared()
        for settings in [dict(max_displacement=0), dict(max_displacement=365),
                         dict(soft_min=-1400), dict(max_displacement=None)]:
            old = self.ctl.settings.copy()
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self.ctl.settings.update(settings)
                self.ctl.review('prepare_pp', 1, None, self.out)
            self.ctl.settings = old
        self.ctl.od_reason = None
        with self.assertRaises(ValueError):
            self.ctl.review('prepare_pp', 1, None, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_execute_rechecks_each_frozen_critical_value(self):
        changes = [(0x6041, 0x427, 0), (0x6060, 3, 0), (0x6061, 3, 0), (0x2002, 1, 1),
                   (0x606c, 1, 0), (0x6064, -1463, 0), (0x607c, 1, 0), (0x608f, 131073, 1),
                   (0x6091, 2, 1), (0x607f, 99, 0), (0x6080, 0, 0), (0x6081, 499999, 0)]  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3/5.1/6.3.2/10.3
        for n, (index, value, sub) in enumerate(changes):
            with self.subTest(index=hex(index)):
                review = self.prepared()
                self.ctl.args.motion_log = str(Path(self.tmp.name) / ('change-{}.jsonl'.format(n)))
                review = self.ctl.review('prepare_pp', 1, None, self.out)
                original = self.node.od[index, sub].data
                self.node.set_value(index, value, sub)
                self.bus.sent.clear()
                with self.assertRaises(ValueError):
                    self.ctl.execute(review, self.out)
                self.assertFalse(requests(self.bus, True))
                self.node.od[index, sub].data = original

    def test_settings_changed_after_review_refused(self):
        review = self.prepared()
        self.ctl.settings['max_displacement'] = 36
        with self.assertRaisesRegex(ValueError, '设置'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_review_tampering_rejected_before_io(self):
        mutations = [lambda r: r.update(target=999), lambda r: r['writes'][0].update(value=999),
                     lambda r: r['writes'].append(dict(key='controlword', value=15)),
                     lambda r: setattr(r['args'], 'velocity', 999), lambda r: r.update(action='relative'),
                     lambda r: r.update(start=999), lambda r: r['prepare_before'].update(actual_position=999),
                     lambda r: r.update(token='OTHER'), lambda r: r.pop('prepare_id')]
        for mutate in mutations:
            review = self.prepared()
            mutate(review)
            count = len(self.bus.sent)
            with self.assertRaisesRegex(ValueError, '审阅'):
                self.ctl.execute(review, self.out)
            self.assertEqual(len(self.bus.sent), count)

    def test_review_copy_forgery_and_superseded_review_rejected(self):
        review = self.prepared()
        for forged in [copy.deepcopy(review), dict(action='prepare_pp')]:
            with self.assertRaises(ValueError):
                self.ctl.execute(forged, self.out)
        old = self.prepared()
        self.ctl.review('prepare_pp', 1, None, self.out)
        with self.assertRaises(ValueError):
            self.ctl.execute(old, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_cancelled_consumed_or_superseded_prepare_cannot_become_enable(self):
        for state in ('cancelled', 'consumed', 'superseded'):
            with self.subTest(state=state):
                review = self.prepared()
                if state == 'cancelled':
                    self.ctl.discard_prepare()
                elif state == 'consumed':
                    self.ctl.execute(review, self.out)
                else:
                    self.ctl.review('prepare_pp', 1, None, self.out)
                review.pop('prepare_id')
                review['action'] = 'enable'
                count = len(self.bus.sent)
                with self.assertRaisesRegex(ValueError, '审阅'):
                    self.ctl.execute(review, self.out)
                self.assertEqual(len(self.bus.sent), count)
                self.assertFalse(self.ctl.enabled_sessions)
                self.ctl.args.motion_log = str(Path(self.tmp.name) / (state + '.jsonl'))

    def test_prepare_plain_dict_copy_cannot_become_enable(self):
        review = dict(self.prepared())
        review.pop('prepare_id')
        review['action'] = 'enable'
        count = len(self.bus.sent)
        with self.assertRaisesRegex(ValueError, '审阅'):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(self.bus.sent), count)

    def test_fault_observed_at_either_status_read_is_not_overwritten(self):
        for fault_at in (1, 2):
            self.ctl.args.motion_log = str(Path(self.tmp.name) / ('transient-fault-{}.jsonl'.format(fault_at)))
            review = self.prepared()
            original = self.bus.send
            counts = [0]
            def send(frame):
                if frame.data[:4] == bytes.fromhex('40416000'):
                    counts[0] += 1
                    self.node.set_value(0x6041, 0x268 if counts[0] == fault_at else 0x260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
                return original(frame)
            with self.subTest(fault_at=fault_at), mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertFalse(requests(self.bus, True))

    def test_only_target_reached_may_change_between_bracket_status_reads(self):
        review = self.prepared()
        original = self.bus.send
        counts = [0]
        def send(frame):
            if frame.data[:4] == bytes.fromhex('40416000'):
                counts[0] += 1
                self.node.set_value(0x6041, 0x260 if counts[0] % 2 else 0x660)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
            return original(frame)
        with mock.patch.object(self.bus, 'send', side_effect=send):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 2)
        self.assertTrue(self.records()[-1]['target_reached'])

    def test_single_use_success_and_failure(self):
        review = self.prepared()
        self.ctl.execute(review, self.out)
        count = len(self.bus.sent)
        with self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(self.bus.sent), count)

    def test_readback_mismatch_stops_after_first_write_keeps_evidence(self):
        review = self.prepared()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('237a6000'):
                self.node.set_value(0x607a, 123)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaisesRegex(ValueError, '写读回不一致'):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 1)
        self.assertEqual(self.records()[-1]['attempts'][0]['readback'], 123)
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(count, len(self.bus.sent))

    def test_first_write_timeout_quarantines_without_second_write_or_retry(self):
        review = self.prepared()
        self.node.drop_write_ack.add((0x607a, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 1)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        count = len(self.bus.sent)
        self.node.drop_write_ack.clear()
        self.ctl.scan(self.out)
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        self.ctl.close()
        self.assertEqual(count, len(self.bus.sent))
        self.assertIsNone(self.records()[-1]['disabled_confirmed'])
        print('PREPARE lost ACK: first download attempted once; quarantine sticky; next write/cleanup/exit/retry=0')

    def test_target_readback_timeout_never_sends_second_write(self):
        review = self.prepared()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('237a6000'):
                self.node.drop_reads.add((0x607a, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 1)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        self.assertIsNone(self.records()[-1]['attempts'][0]['readback'])

    def test_second_write_mismatch_preserves_both_attempts_without_cleanup(self):
        review = self.prepared()
        original = self.bus.send
        def send(frame):
            result = original(frame)
            if frame.data[:4] == bytes.fromhex('23816000'):
                self.node.set_value(0x6081, 99)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
            return result
        with mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 2)
        self.assertEqual([a['readback'] for a in self.records()[-1]['attempts']], [-1464, 99])
        self.assertFalse(self.ctl.enabled_sessions)

    def test_position_drift_during_five_sample_window_refuses_all_writes(self):
        review = self.prepared()
        original = self.out.event
        def event(name, data):
            original(name, data)
            if name == 'prepare_sample' and data['phase'] == '稳定采样2/5':
                self.node.set_value(0x6064, -1463)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        self.out.event = event
        with self.assertRaisesRegex(ValueError, '漂移'):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_read_timeout_before_writes_quarantines_once(self):
        review = self.prepared()
        self.node.drop_reads.add((0x607f, 0))  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        with self.assertRaises(Exception):
            self.ctl.execute(review, self.out)
        self.assertFalse(requests(self.bus, True))
        reads = [r for r in requests(self.bus, False) if r[:2] == (0x607f, 0)]  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:6.3.2
        self.assertEqual(len(reads), 1)

    def test_drift_enable_mode_or_offset_after_first_write_blocks_second(self):
        changes = [(0x6064, -1463), (0x6041, 0x427), (0x6060, 3), (0x607c, 1), (0x608f, 131073)]  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3/5.1/6.3.2
        for n, (index, value) in enumerate(changes):
            self.ctl.args.motion_log = str(Path(self.tmp.name) / ('postwrite-{}.jsonl'.format(n)))
            review = self.prepared()
            original = self.bus.send
            saved = self.node.od[index, 1 if index == 0x608f else 0].data  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:5.1
            def send(frame):
                result = original(frame)
                if frame.data[:4] == bytes.fromhex('237a6000'):
                    self.node.set_value(index, value, 1 if index == 0x608f else 0)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:5.1
                return result
            with self.subTest(index=hex(index)), mock.patch.object(self.bus, 'send', side_effect=send), self.assertRaises(ValueError):
                self.ctl.execute(review, self.out)
            self.assertEqual(len(requests(self.bus, True)), 1)
            self.node.od[index, 1 if index == 0x608f else 0].data = saved  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:5.1

    def test_cancel_after_first_write_does_not_send_cleanup(self):
        review = self.prepared()
        original = self.out.event
        def event(name, data):
            original(name, data)
            if name == 'write_readback':
                self.out.cancel_event.set()
        self.out.event = event
        with self.assertRaises(KeyboardInterrupt):
            self.ctl.execute(review, self.out)
        self.assertEqual(len(requests(self.bus, True)), 1)
        count = len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(count, len(self.bus.sent))

    def test_independent_peer_matches_complete_mockbus_wire(self):
        review = self.prepared()
        self.ctl.execute(review, self.out)
        expected = [(f.arbitration_id, bytes(f.data)) for f in self.bus.sent]
        peer = IndependentPeer(Clock())
        peer.values = {k: bytes(v.data) for k, v in self.node.od.items()}
        peer.values[self.ctl.specs['target_position']['index_value'], 0] = (7800).to_bytes(4, 'little', signed=True)
        peer.values[self.ctl.specs['profile_velocity']['index_value'], 0] = (500000).to_bytes(4, 'little')
        peer.sw = 0x0260  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.ctl.bus = peer
        self.ctl.args.motion_log = str(Path(self.tmp.name) / 'independent.jsonl')
        row = self.ctl.row(1)
        row.update(updated=time.monotonic(), lost=False)
        row['values']['target_position'] = 7800
        row['values']['profile_velocity'] = 500000
        self.ctl._put(row)
        review = self.ctl.review('prepare_pp', 1, None, self.out)
        self.ctl.execute(review, self.out)
        self.assertEqual([(f.arbitration_id, bytes(f.data)) for f in peer.sent], expected)
        self.assertEqual(peer.controls(), [])
        print('PREPARE independent literal SDO peer: complete {} request frames match MockBus; controlword=0'.format(len(expected)))


class PrepareInteractionTests(BenchFixture):
    press = staticmethod(bench_tests.BenchInteractionTests.press)

    def app(self):
        self.authorized()
        self.node.set_value(0x6041, 0x0260)  # docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md:4.1.3
        self.ctl.refresh(self.out)
        self.bus.sent.clear()
        app = ui.PmpApp(tool, self.args)
        app.controller = self.ctl
        app.initial_snapshot = False
        app.next_refresh = float('inf')
        app.current_view = 'detail'
        return app

    def test_new_help_starts_at_first_line_before_any_render(self):
        app = self.app()
        self.press(app, '?')
        text = '\n'.join(app.build().to_lines())
        self.assertIn('测试台：固定四席', text)
        self.assertIn('P 失能准备', text)

    def test_ui_wrong_token_and_cancel_do_not_write(self):
        app = self.app()
        self.press(app, 'p')
        self.assertEqual(app.action, 'prepare_pp')
        self.assertIsNone(app.input_field)
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        frozen = app.pending
        self.assertFalse(requests(self.bus, True))
        app.input_field._draft = 'PREPARE_PP wrong 1 -1464'
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        self.assertFalse(requests(self.bus, True))
        self.press(app, 'esc')
        self.assertIsNone(app.pending)
        with self.assertRaises(ValueError):
            self.ctl.execute(frozen, self.out)
        self.assertFalse(requests(self.bus, True))

    def test_ui_real_two_stage_success(self):
        app = self.app()
        self.press(app, 'p')
        self.press(app, 'f5')
        self.assertEqual(app.current_view, 'confirm')
        self.assertFalse(requests(self.bus, True))
        self.assertIn('PREPARE_PP mock-bench 1 -1464', app.pending['token'])
        app.input_field._draft = app.pending['token']
        self.press(app, 'f5')
        app.worker.join(3)
        app.poll()
        self.assertEqual(len(requests(self.bus, True)), 2)
        self.assertIn('失能确认=True', app.recovery)
        self.assertFalse(self.ctl.enabled_sessions)
        print('PREPARE UI: P -> F5 freeze -> exact token -> F5 background execution; two downloads only')


if __name__ == '__main__':
    unittest.main()
