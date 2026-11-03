"""Independent manual PP steps against standard MockBus; no CAN socket or SSH."""
import copy
import json
from pathlib import Path
import queue
import threading
import time
from unittest import mock

from _test_bench import BenchFixture
import _test_bench as bench_tests
from _test_bench_support import DelayedBus, mockbus, requests, tool
from _test_disabled_move import DisabledMoveNode
from _test_position import Clock
from pmp4010_manual import ManualController, ManualApp


class ManualNode(DisabledMoveNode):
    def __init__(self):
        super().__init__()
        self.enable_drift = 13

    def handle(self, frame, now):
        if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6], 'little') == 6:
            mockbus.MockNode.handle(self, frame, now)
            self.set_value(0x6041, 0x21)
            return
        super().handle(frame, now)
        if frame.data[:4] == bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6], 'little') == 0x12f:
            self.set_value(0x6064, self.value(0x6064, signed=True) + self.enable_drift)


class DelayedManualNode(ManualNode):
    """SDO write ACK precedes 6041 state progress, as a real drive may do.

    Two old samples exercise both ends of a full guarded snapshot. States
    change only when an actual status SDO upload is serviced, never by a
    controller/test mock of the transaction itself.
    """
    def __init__(self):
        super().__init__()
        self.transition_sequences = {0x106:[0x260,0x260,0x231],0x107:[0x231,0x231,0x233]}
        self.pending_states = []
        self.status_reads = []
        self.last_word = None

    def handle(self, frame, now):
        data = frame.data
        if data[:4] == bytes.fromhex('40416000'):
            if self.pending_states:
                self.set_value(0x6041,self.pending_states.pop(0))
            self.status_reads.append((self.last_word,self.value(0x6041)))
        super().handle(frame,now)
        if data[:4] == bytes.fromhex('2b406000'):
            self.last_word = int.from_bytes(data[4:6],'little')
            self.pending_states = list(self.transition_sequences.get(self.last_word,[]))


class EnabledEdgeNode(DelayedManualNode):
    """A disabled set-point ACK does not start an enabled PP trajectory.

    This device model advances feedback on position uploads only after a new
    bit4 rising edge in Operation enabled. Lowering bit4 or releasing Halt
    alone cannot synthesize a move. SDO frames/replies use standard MockBus.
    """
    def __init__(self):
        super().__init__()
        self.trajectory_target = None
        self.enabled_edges = []
        self.movement_samples = []
        self.ack_samples = []
        self.missing_enabled_ack = False
        self.stuck_enabled_ack = False
        # Optional controlled protocol hypothesis, not a firmware claim:
        # enabling invalidates executable data until 607A is downloaded while
        # enabled with Halt. ACK/6062 alone can still look correct otherwise.
        self.require_enabled_target = False
        self.enabled_target_valid = False
        self.enabled_target_writes = []

    def handle(self, frame, now):
        data = frame.data
        word = int.from_bytes(data[4:6], 'little') if data[:4] == bytes.fromhex('2b406000') else None
        if data[:4] == bytes.fromhex('40646000') and self.trajectory_target is not None:
            current = self.value(0x6064, signed=True)
            delta = self.trajectory_target-current
            current += min(2, abs(delta)) * (1 if delta > 0 else -1)
            self.set_value(0x6064,current)
            self.set_value(0x606c,0 if current == self.trajectory_target else 100)
            self.movement_samples.append((current,self.value(0x606c,signed=True)))
        if word in (0x12f,0x2f,0x3f):
            old_word, old_state = self.value(0x6040), self.value(0x6041) & 0x6f
            mockbus.MockNode.handle(self,frame,now)
            self.last_word = word
            self.pending_states = list(self.transition_sequences.get(word,[]))
            rising = not old_word & 0x10 and bool(word & 0x10)
            if old_state == 0x27 and rising:
                target = self.value(0x607a,signed=True)
                self.enabled_edges.append((old_word,word,target))
                if not self.require_enabled_target or self.enabled_target_valid:
                    self.trajectory_target = target
            ack = word == 0x3f and not self.missing_enabled_ack
            ack = ack or word == 0x2f and self.trajectory_target is not None and self.stuck_enabled_ack
            self.set_value(0x6041,0x27 | (0x1000 if ack else 0))
            if word == 0x12f:
                self.enabled_target_valid = False
                self.set_value(0x6064,self.value(0x6064,signed=True)+self.enable_drift)
            if (0x6040,0) in self.drop_write_ack:
                self._outbox.clear()
            return
        super().handle(frame,now)
        if data[:4] == bytes.fromhex('237a6000') and self.value(0x6040)==0x12f and self.value(0x6041)&0x6f==0x27:
            self.enabled_target_valid = True
            self.enabled_target_writes.append(self.value(0x607a,signed=True))
        if data[:4] == bytes.fromhex('40416000'):
            self.ack_samples.append((self.last_word,bool(self.value(0x6041) & 0x1000)))
        if word in (0,2,6):
            self.trajectory_target = None
            self.set_value(0x606c,0)


class ManualTests(BenchFixture):
    def setUp(self):
        super().setUp()
        self.node = ManualNode()
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        self.args.node_range = '1'
        # Existing safety regressions intentionally exercise explicit 100U/s.
        self.args.manual_velocity = 100
        self.ctl = ManualController(tool, self.args)
        self.addCleanup(self.ctl.close)

    def controls(self):
        return [v for i, _, v in requests(self.bus, True) if i == 0x6040]

    def ready(self):
        self.authorized()
        self.bus.sent.clear()

    def submit(self, value=50):
        if self.ctl.context is None:
            self.ready()
        review = self.ctl.review('relative', 1, value, self.out)
        self.ctl.execute(review, self.out)
        return review

    def step(self, action):
        review = self.ctl.review(action, 1, None, self.out)
        self.ctl.execute(review, self.out)
        return review

    def delayed(self):
        self.node=DelayedManualNode()
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        self.ctl=ManualController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()

    def enabled_edge_node(self):
        self.node=EnabledEdgeNode()
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        self.ctl=ManualController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()

    def test_enabled_edge_behavior_moves_frozen_target_not_disabled_ack(self):
        self.enabled_edge_node()
        review=self.submit(50)
        self.step('enable')
        before=self.node.value(0x6064,signed=True)
        self.assertEqual(self.node.enabled_edges,[])
        self.assertEqual(before,review['start']+13)
        self.step('go')
        # A successful ACK while Switched on is insufficient. Feedback must
        # move because of a fresh enabled edge, not a fixture write counter.
        after=self.node.value(0x6064,signed=True)
        print('ENABLED EDGE behavior: before={}, after={}, target={}, edges={}, CW={}'.format(
            before,after,review['target'],self.node.enabled_edges,[hex(w) for w in self.controls()]))
        self.assertGreater(after,before,'no enabled bit4 edge: PP trajectory never started')
        self.assertEqual(self.node.enabled_edges,[(0x2f,0x3f,review['target'])])

        events=[r for r in self.diagnostic_records() if r['event']=='manual_enabled_target']
        self.assertEqual([r['point'] for r in events],['before_write','write_ack','readback'])
        self.assertTrue(all(r['target']==review['target'] for r in events))
        self.assertEqual(events[-1]['actual'],review['target'])
        self.assertTrue(any(velocity for _,velocity in self.node.movement_samples))
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),
                         (review['start'],review['target']))
        self.assertEqual([v for i,_,v in requests(self.bus,True) if i==0x607a],
                         [review['target'] & 0xffffffff]*2)
        self.assertEqual(self.controls()[-3:],[0x2f,0x3f,0x2f])
        self.assertEqual(self.ctl.proofs[1]['phase'],'running')
        go_samples=[r for r in self.diagnostic_records() if r['event']=='manual_transition_snapshot'
                    and r['phase']=='使能后新ACK核对']
        self.assertEqual([(r['controlword'],r['ack_samples']) for r in go_samples],
                         [(0x2f,[False,False]),(0x3f,[True,True]),(0x2f,[False,False])])
        self.assertTrue(all(r['actual_velocity'] != 0 for r in go_samples[1:]))
        # Advance actual SDO feedback to the frozen target, never teleport on
        # a controlword or assert motion solely from the written word chain.
        for _ in range(30):
            self.ctl.refresh(self.out)
            if self.node.value(0x6064,signed=True) == review['target']:
                break
        self.assertEqual(self.node.value(0x6064,signed=True),review['target'])
        self.assertEqual(len(self.node.enabled_edges),1)
        print('ENABLED EDGE actual SDO: ACK samples 0/0→1/1→0/0; moving during high/low handshake; fixed target reached, one edge only')

    def test_enabled_target_rewrite_hypothesis_moves_same_frozen_target(self):
        self.enabled_edge_node()
        self.node.require_enabled_target=True
        review=self.submit(50)
        self.step('enable')
        before=self.node.value(0x6064,signed=True)
        self.assertFalse(self.node.enabled_target_valid)
        self.step('go')
        after=self.node.value(0x6064,signed=True)
        print('ENABLED TARGET hypothesis (not firmware fact): before={}, after={}, frozen={}, enabled writes={}, edges={}'.format(
            before,after,review['target'],self.node.enabled_target_writes,self.node.enabled_edges))
        self.assertGreater(after,before,'enabled ACK alone did not establish executable target data')
        self.assertEqual(self.node.enabled_target_writes,[review['target']])
        self.assertEqual([v for i,_,v in requests(self.bus,True) if i==0x607a],
                         [review['target'] & 0xffffffff]*2)
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),
                         (review['start'],review['target']))
        self.assertEqual(self.node.enabled_edges,[(0x2f,0x3f,review['target'])])

    def test_enabled_target_lost_download_ack_quarantines_without_retry(self):
        self.enabled_edge_node()
        self.submit()
        self.step('enable')
        self.node.drop_write_ack.add((0x607a,0))
        with self.assertRaises(Exception):self.step('go')
        self.assertEqual(len(self.node.enabled_target_writes),1)
        self.assertEqual(self.node.enabled_edges,[])
        self.assertEqual(self.controls()[-1],0x12f)
        self.assert_locked_no_requests()
        print('ENABLED TARGET lost download ACK: one attempted target download; same-client quarantine, zero subsequent requests/edges')

    def test_enabled_target_readback_mismatch_cleanup_without_edge(self):
        self.enabled_edge_node()
        self.submit()
        self.step('enable')
        original=self.node.handle
        def corrupt(frame,now):
            original(frame,now)
            if frame.data[:4]==bytes.fromhex('237a6000'):
                self.node.set_value(0x607a,-1419)
        self.node.handle=corrupt
        with self.assertRaisesRegex(ValueError,'写读回不一致'):self.step('go')
        self.assertEqual(self.controls()[-2:],[2,0])
        self.assertEqual(self.node.enabled_edges,[])
        self.assertFalse(self.ctl.proofs)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        print('ENABLED TARGET readback mismatch: same-client bounded2/0; no new edge')

    def test_enabled_target_bad_readback_width_quarantines_without_cleanup_retry(self):
        self.enabled_edge_node()
        self.submit()
        self.step('enable')
        original=self.node.handle
        def corrupt(frame,now):
            original(frame,now)
            if frame.data[:4]==bytes.fromhex('237a6000'):
                self.node.od[0x607a,0].data=b'\0\0'
        self.node.handle=corrupt
        with self.assertRaises(ValueError):self.step('go')
        self.assertEqual(self.node.enabled_edges,[])
        self.assertEqual(self.controls()[-1],0x12f)
        self.assert_locked_no_requests()
        print('ENABLED TARGET malformed readback width: same-client quarantine and zero following requests')

    def test_enabled_edge_delayed_ack_samples_wait_for_fresh_generation(self):
        self.enabled_edge_node()
        self.submit(50)
        self.step('enable')
        self.node.transition_sequences[0x3f]=[0x27,0x27,0x1027]
        self.step('go')
        samples=[r for r in self.diagnostic_records() if r['event']=='manual_transition_snapshot'
                 and r['phase']=='使能后新ACK核对' and r['controlword']==0x3f]
        self.assertEqual([r['ack_samples'] for r in samples],[[False,False],[True,True]])
        self.assertTrue(all(r['actual_velocity'] for r in samples))
        self.assertEqual(len(self.node.enabled_edges),1)

    def test_enabled_edge_missing_or_stuck_ack_fixed_deadline_same_client_cleanup(self):
        for flag,tail in (('missing_enabled_ack',[0x2f,0x3f,2,0]),
                          ('stuck_enabled_ack',[0x2f,0x3f,0x2f,2,0])):
            with self.subTest(flag=flag):
                self.enabled_edge_node()
                self.submit()
                self.step('enable')
                self.ctl.manual_sessions[1].state_timeout=.04
                setattr(self.node,flag,True)
                client=self.ctl.manual_sessions[1].client
                begin=time.monotonic()
                with self.assertRaises(Exception):self.step('go')
                self.assertLess(time.monotonic()-begin,.4)
                self.assertEqual(self.controls()[-len(tail):],tail)
                self.assertIs(self.ctl.manual_sessions[1].client,client)
                self.assertFalse(self.ctl.proofs)
                self.assertTrue(self.ctl.row(1)['quarantined'])
        print('ENABLED EDGE missing high/stuck low ACK: fixed deadline; same-client bounded2/0; proof consumed and node blocked')

    def test_enabled_edge_moving_handshake_all_protected_guards_cleanup(self):
        # Thirteen retained MockBus histories can trigger a >15ms generation-2
        # GC during a normal immediate reply (measured 18.382ms). This case
        # tests value guards, not response deadlines. Keep every dedicated
        # timeout/quarantine case and production SDO timeout unchanged.
        self.args.timeout=.1
        cases=[(0x6060,3,0),(0x6061,3,0),(0x2002,1,1),(0x607c,1,0),
               (0x607a,-1419,0),(0x6062,-1419,0),(0x6040,0x2f,0),
               (0x608f,131073,1),(0x6081,101,0),(0x607d,-1460,2),
               (0x603f,1,0),(0x6041,0x2027,0),(0x6064,-1000,0)]
        for index,value,sub in cases:
            with self.subTest(index=hex(index)):
                self.enabled_edge_node()
                self.submit()
                self.step('enable')
                original=self.out.event
                def inject(name,data):
                    original(name,data)
                    if name=='manual_controlword' and data['point']=='write_ack' and data['controlword']==0x3f:
                        self.node.set_value(index,value,sub)
                        if index==0x6064:self.node.trajectory_target=None
                self.out.event=inject
                client=self.ctl.manual_sessions[1].client
                with self.assertRaises(ValueError):self.step('go')
                self.out.event=original
                if index == 0x2002:
                    # The shared stop guard refuses CiA402 writes after the
                    # selector changes; do not pretend this proves a stop.
                    self.assertEqual(self.controls()[-2:],[0x2f,0x3f])
                    self.assertIn('停止未确认',self.ctl.row(1)['quarantine_reason'])
                else:
                    self.assertEqual(self.controls()[-4:],[0x2f,0x3f,2,0])
                self.assertIs(self.ctl.manual_sessions[1].client,client)
                self.assertFalse(self.ctl.proofs)
        print('ENABLED EDGE moving phase: mode/CW/607A/6062/calibration/profile/607C/607D/fault/envelope changes stop2/disable0 before final release')

    def test_enabled_edge_go_initial_nonzero_speed_still_refused(self):
        self.enabled_edge_node()
        self.submit()
        self.step('enable')
        self.node.set_value(0x606c,1)
        self.ctl.refresh(self.out)
        before=list(self.controls())
        with self.assertRaisesRegex(ValueError,'非零'):
            self.ctl.review('go',1,None,self.out)
        self.assertEqual(self.controls(),before)
        self.node.set_value(0x606c,0)
        self.ctl.refresh(self.out)
        review=self.ctl.review('go',1,None,self.out)
        self.node.set_value(0x606c,1)
        with self.assertRaisesRegex(ValueError,'非零'):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls()[-2:],[2,0])
        self.assertNotIn(0x2f,self.controls())

    def test_enabled_edge_ack_read_timeout_quarantines_zero_retry(self):
        self.enabled_edge_node()
        self.submit()
        self.step('enable')
        original=self.out.event
        def inject(name,data):
            original(name,data)
            if name=='manual_controlword' and data['point']=='write_ack' and data['controlword']==0x3f:
                self.node.drop_reads.add((0x6041,0))
        self.out.event=inject
        with self.assertRaises(Exception):self.step('go')
        self.assertEqual(self.controls()[-2:],[0x2f,0x3f])
        self.node.drop_reads.clear()
        self.assert_locked_no_requests()
        print('ENABLED EDGE ACK read timeout after003F: original client quarantined; no cleanup/refresh/scan/exit requests')

    def test_controlled_106_107_accept_delayed_previous_first_and_last_samples(self):
        self.delayed()
        self.submit(36)
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127])
        self.assertEqual([raw for word,raw in self.node.status_reads if word==0x106][:3],[0x260,0x260,0x231])
        self.assertEqual([raw for word,raw in self.node.status_reads if word==0x107][:3],[0x231,0x231,0x233])
        self.assertEqual(self.ctl.proofs[1]['target'],-1434)
        print('DELAYED actual SDO: 0106 ACK then0260/0260/0231; 0107 ACK then0231/0231/0233; fixed607A=-1434; submit completes')

    def diagnostic_records(self):
        return [json.loads(line) for path in Path(self.tmp.name).glob('*.jsonl') for line in path.read_text().splitlines()]

    def failure_records(self):
        path = Path(self.ctl.diagnostic_paths[1])
        self.assertEqual(path.parent, Path(self.args.motion_log).parent)
        self.assertIn('-diagnostics-', path.name)
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_initial_603F_retains_partial_hex_without_control_or_extra_upload(self):
        self.node.set_value(0x603f, 0xabcd)
        self.ctl.rt_ack = True
        self.ctl.scan(self.out)
        self.assertEqual(requests(self.bus), [(i,0,0) for i in (0x1000,0x6041,0x6064,0x606c,0x6061,0x603f)])
        failure = self.ctl.failure_diagnostics[1]
        self.assertEqual(failure['fault_code_hex'], '0xABCD')
        self.assertFalse(failure['snapshot_complete'])
        self.assertIsNone(failure['controlword'])
        self.assertIn('未知', failure['controlword_source'])
        self.assertEqual(failure['raw_statusword'], self.node.value(0x6041))
        self.assertEqual(failure['actual_position'], self.node.value(0x6064,signed=True))
        self.assertEqual(failure['actual_velocity'], 0)
        self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)
        self.assertTrue(self.ctl.blocked)
        self.assertEqual(self.ctl.row(1)['values'], {})
        records = self.failure_records()
        self.assertEqual(records[1]['first_failure'], failure)
        self.assertEqual((records[2]['stop_attempted'],records[2]['skip_reason'],records[2]['disabled_confirmed']),
                         (False,'no_control_responsibility',None))
        self.assertIn('0xABCD',self.ctl.row(1)['quarantine_reason'])
        self.assertIn('未执行停止',self.ctl.row(1)['quarantine_reason'])
        count = len(self.bus.sent)
        self.ctl.refresh(self.out)
        self.ctl.scan(self.out)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)
        print('DIAGNOSTIC initial603F=ABCD: exact identity+5 uploads; CW unknown; partial persisted before enable; target lock retained, client communication healthy')

    def test_background_603F_preserves_history_and_exact_owned_cleanup(self):
        self.submit()
        self.step('enable')
        self.step('go')
        before = copy.deepcopy(self.ctl.row(1)['values'])
        self.node.set_value(0x603f, 0x3456)
        self.node.set_value(0x6064, before['actual_position']+1)
        self.bus.sent.clear()
        self.ctl.refresh(self.out)
        self.assertEqual(requests(self.bus), [(0x6041,0,0),(0x6064,0,0),(0x606c,0,0),
            (0x6061,0,0),(0x603f,0,0),(0x2002,1,0),(0x6040,0,2),(0x6041,0,0),
            (0x2002,1,0),(0x6040,0,0),(0x6041,0,0)])
        self.assertEqual(self.controls(), [2,0])
        self.assertEqual(self.ctl.row(1)['values'], before)
        self.assertEqual(self.ctl.failure_diagnostics[1]['actual_position'],before['actual_position']+1)
        self.assertIn('running',self.ctl.failure_diagnostics[1]['phase'])
        cleanup = self.failure_records()[2]
        self.assertEqual((cleanup['stop_attempted'],cleanup['skip_reason'],cleanup['disabled_confirmed']), (True,None,True))
        self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        self.assertFalse(cleanup['mechanical_stop_confirmed'])
        print('DIAGNOSTIC running603F=3456: exact5 uploads then mode/CW2/status/mode/CW0/status; no extra deviceIO; original historical values preserved')

    def test_first_6041_fault_does_not_read_603F_to_complete_diagnostic(self):
        self.ready()
        self.node.set_value(0x6041, 0x2f)
        self.node.set_value(0x603f, 0xbeef)
        self.ctl.refresh(self.out)
        self.assertEqual(requests(self.bus), [(0x6041,0,0)])
        failure = self.failure_records()[1]['first_failure']
        self.assertEqual(failure['raw_statusword'],0x2f)
        self.assertIsNone(failure['fault_code'])
        self.assertIsNone(failure['fault_code_hex'])
        self.assertEqual(failure['observed_fields'], ['statusword'])

    def test_quarantined_cleanup_reports_skip_and_adds_zero_sends(self):
        self.ready()
        session = self.ctl.manual_sessions[1]
        session.client.quarantined = True
        session.needs_stop = False  # Both skip conditions apply; quarantine wins.
        self._assert_quarantined_cleanup(session)

    def _assert_quarantined_cleanup(self, session):
        count = len(self.bus.sent)
        with mock.patch.object(session,'stop_after_failure',side_effect=AssertionError('must skip')):
            self.ctl._cleanup_failure(1,session,ValueError('original protocol uncertainty'),self.out)
        self.ctl.refresh(self.out)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)
        cleanup = self.failure_records()[2]
        self.assertEqual((cleanup['stop_attempted'],cleanup['skip_reason'],cleanup['disabled_confirmed']),
                         (False,'client_quarantined',None))

    def test_diagnostic_disk_failure_occurs_after_cleanup_and_keeps_original(self):
        self.submit()
        self.node.set_value(0x603f,0x4567)
        self.bus.sent.clear()
        def disk_failed(*args,**kwargs):
            self.assertEqual(self.controls(),[2,0])
            raise OSError('diagnostic storage unavailable')
        with mock.patch('pmp4010_manual.position.Journal',side_effect=disk_failed):
            self.ctl.refresh(self.out)
        self.assertEqual(self.controls(),[2,0])
        self.assertIn('0x4567',self.ctl.failure_diagnostics[1]['reason'])
        self.assertIn('0x4567',self.ctl.row(1)['quarantine_reason'])
        self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)
        messages = []
        while not self.out.events.empty():
            messages.append(str(self.out.events.get()))
        self.assertIn('诊断日志缺失或不完整','\n'.join(messages))

    def test_cleanup_exception_cannot_replace_original_603F(self):
        self.submit()
        session=self.ctl.manual_sessions[1]
        self.node.set_value(0x603f,0x5678)
        with mock.patch.object(session,'stop_after_failure',side_effect=RuntimeError('separate cleanup failure')):
            self.ctl.refresh(self.out)
        records=self.failure_records()
        self.assertIn('0x5678',records[1]['first_failure']['reason'])
        self.assertNotIn('cleanup failure',records[1]['first_failure']['reason'])
        self.assertEqual(records[2]['cleanup_error'],'separate cleanup failure')
        self.assertTrue(records[2]['stop_attempted'])
        self.assertFalse(records[2]['disabled_confirmed'])
        self.assertIn('独立急停',self.ctl.row(1)['quarantine_reason'])

    def test_successful_snapshot_exact_original_read_order_no_diagnostics(self):
        from pmp4010_bench import TELEMETRY
        import pmp4010_position as position
        self.ready()
        self.ctl.refresh(self.out)
        keys=list(TELEMETRY)+list(position.CALIBRATION)+['statusword','actual_position','actual_velocity']
        expected=[(self.ctl.objects[k]['index_value'],self.ctl.objects[k]['sub'],0) for k in keys]
        self.assertEqual(requests(self.bus),expected)
        self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)
        self.assertFalse(self.ctl.diagnostic_paths)

    def test_next_first_status_read_failure_never_reuses_previous_complete_raw(self):
        self.ready()
        original=self.out.event
        def after_complete(name,data):
            original(name,data)
            if (name=='manual_transition_snapshot' and data['phase']=='等待受控迁移'
                    and data['controlword']==0x106):
                self.node.drop_reads.add((0x6041,0))
        self.out.event=after_complete
        with self.assertRaises(Exception):
            self.submit(36)
        self.assertEqual(self.controls(),[0x106])
        failure=self.failure_records()[1]['first_failure']
        for key in ('raw_statusword','fault_code','actual_position','actual_velocity','controlword','observed_controlword'):
            self.assertIsNone(failure[key],key)
        self.assertEqual(failure['observed_fields'],[])
        self.assertFalse(failure['snapshot_complete'])
        self.assertEqual(failure['expected_controlword'],0x106)
        self.assertIn('前一阶段',failure['expected_controlword_source'])
        self.assertEqual(self.failure_records()[2]['skip_reason'],'client_quarantined')
        print('DIAGNOSTIC previous complete snapshot then next first6041 timeout: all current raw fields unknown; previous CW kept only as expected context')

    def test_controlled_previous_state_timeout_bounded_and_cleanup_logged(self):
        self.delayed()
        self.ctl.args.state_timeout=.04
        self.ctl.manual_sessions[1].state_timeout=.04
        self.node.transition_sequences[0x106]=[0x260]*10000
        begin=time.monotonic()
        with self.assertRaises(Exception):self.submit(36)
        self.assertLess(time.monotonic()-begin,.4)
        self.assertEqual(self.controls(),[0x106,2,0])
        records=self.diagnostic_records()
        failure=next(r['first_failure'] for r in records if r['event']=='exception')
        cleanup=next(r for r in records if r['event']=='manual_cleanup')
        self.assertEqual(failure['raw_statusword'],0x260)
        self.assertEqual(failure['controlword'],0x106)
        self.assertIn('超时',failure['reason'])
        self.assertEqual(cleanup['first_failure'],failure)
        self.assertTrue(cleanup['disabled_confirmed'])
        print('DELAYED persistent0260: state_timeout bounded; no0107/enable/release; first_failure and cleanup2/0 retained')

    def test_controlled_first_unexpected_enabled_then_normal_is_not_hidden(self):
        self.delayed()
        self.node.transition_sequences[0x106]=[0x237,0x231]
        with self.assertRaisesRegex(ValueError,'6041=0x0237'):self.submit(36)
        self.assertEqual([raw for word,raw in self.node.status_reads if word==0x106],[0x237])
        self.assertEqual(self.controls(),[0x106,2,0])
        failure=self.ctl.failure_diagnostics[1]
        self.assertEqual(failure['raw_statusword'],0x237)
        self.assertEqual(failure['decoded_state'],'Operation enabled')
        self.assertIn('阶段控制字',failure['controlword_source'])
        self.assertEqual(failure['expected_controlword'],0x106)
        self.assertIsNone(failure['observed_controlword'])
        self.assertEqual(self.ctl.row(1)['failure_diagnostic'],failure)
        self.assertIn('CW=0x0106 6041=0x0237',self.ctl.row(1)['quarantine_reason'])
        cleanup=next(r for r in self.diagnostic_records() if r['event']=='manual_cleanup')
        self.assertEqual(cleanup['first_failure']['raw_statusword'],0x237)
        print('DELAYED unexpected0237 then0231: first raw immediately rejected, no read of later normal, cleanup cannot replace root sample')

    def test_controlled_quickstop_is_never_ready(self):
        self.delayed()
        self.node.transition_sequences[0x106]=[0x207,0x231]
        with self.assertRaisesRegex(ValueError,'6041=0x0207'):self.submit(36)
        self.assertEqual(self.controls(),[0x106,2,0])
        self.assertEqual(self.ctl.failure_diagnostics[1]['decoded_state'],'Quick stop active')

    def test_controlled_following_fault_in_first_or_last_sample_refuses(self):
        for sequence in ([0x2260,0x231],[0x260,0x2260,0x231]):
            with self.subTest(sequence=sequence):
                self.delayed()
                self.node.transition_sequences[0x106]=sequence
                with self.assertRaisesRegex(ValueError,'故障/PP跟随误差'):self.submit(36)
                self.assertEqual(self.controls(),[0x106,2,0])
                self.assertEqual(self.ctl.failure_diagnostics[1]['raw_statusword'],0x2260)

    def test_controlled_wait_cancel_cleanup_no_later_controlword(self):
        self.delayed()
        self.node.transition_sequences[0x106]=[0x260]*1000
        original=self.out.event
        def event(name,data):
            original(name,data)
            if name=='manual_transition_snapshot' and data['phase']=='等待受控迁移':
                self.out.cancel_event.set()
        self.out.event=event
        with self.assertRaises(KeyboardInterrupt):self.submit(36)
        self.out.cancel_event.clear()
        self.assertEqual(self.controls(),[0x106,2,0])
        self.assertEqual(self.ctl.failure_diagnostics[1]['reason'],'等待期间已取消')
        print('DELAYED waiting cancel: immediate cancellation at pending snapshot, same-client bounded2/0, no later CW')

    def test_controlled_wait_timeout_quarantine_zero_subsequent_requests(self):
        self.delayed()
        original=self.node.handle
        def handle(frame,now):
            original(frame,now)
            if frame.data[:6]==bytes.fromhex('2b4060000601'):
                self.node.drop_reads.add((0x6041,0))
        self.node.handle=handle
        with self.assertRaises(Exception):self.submit(36)
        count=len(self.bus.sent)
        self.assertTrue(self.ctl.manual_sessions[1].client.quarantined)
        self.ctl.refresh(self.out)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)
        self.assertEqual(self.controls(),[0x106])

    def test_controlled_invalid_raw_survives_diagnostic_journal_failure(self):
        self.delayed()
        self.node.transition_sequences[0x106]=[0x237,0x231]
        from pmp4010_position import Journal
        original=Journal.event
        def event(journal,name,**data):
            if name=='manual_transition_sample' and data['raw_statusword']==0x237:
                raise OSError('diagnostic disk failure')
            return original(journal,name,**data)
        with mock.patch.object(Journal,'event',event),self.assertRaises(OSError):self.submit(36)
        self.assertEqual(self.controls(),[0x106,2,0])
        self.assertEqual(self.ctl.failure_diagnostics[1]['raw_statusword'],0x237)
        self.assertIn('非预期状态',self.ctl.failure_diagnostics[1]['reason'])

    def test_controlled_pending_fresh_velocity_position_calibration_and_settings_guards(self):
        cases = [('velocity',lambda:self.node.set_value(0x606c,1)),
                 ('position',lambda:self.node.set_value(0x6064,-1100)),
                 ('calibration',lambda:self.node.set_value(0x608f,131073,1)),
                 ('profile',lambda:self.node.set_value(0x6081,101)),
                 ('offset',lambda:self.node.set_value(0x607c,1)),
                 ('settings',lambda:self.ctl.settings.update(max_displacement=363))]
        for name,inject in cases:
            with self.subTest(name=name):
                self.delayed()
                original=self.node.handle
                injected=False
                def handle(frame,now):
                    nonlocal injected
                    original(frame,now)
                    if frame.data[:4]==bytes.fromhex('40416000') and self.node.last_word==0x106 and not injected:
                        injected=True
                        inject()
                self.node.handle=handle
                with self.assertRaises(ValueError):self.submit(36)
                self.assertEqual(self.controls(),[0x106,2,0])
                if name=='velocity':
                    self.assertEqual(self.ctl.failure_diagnostics[1]['actual_velocity'],1)
                    self.assertEqual([raw for word,raw in self.node.status_reads if word==0x106],[0x260])
        print('DELAYED pending guard injections: first velocity/position/calibration/profile/607C/settings each prevents next CW and cleans2/0')

    def test_startup_scan_and_refresh_zero_downloads(self):
        self.online()
        self.ctl.scan(self.out)
        self.assertFalse(requests(self.bus, True))
        self.assertEqual(len(self.ctl.manual_sessions), 1)
        self.assertFalse(self.ctl.enabled_sessions)

    def test_read_failure_before_actions_never_sends_cleanup_writes(self):
        self.node.drop_reads.add((0x6041, 0))
        self.online()
        self.assertFalse(requests(self.bus, True))
        self.assertTrue(self.ctl.row(1)['quarantined'])

    def test_bit10_zero_submit_only_artificial_target_fresh_ack_no_enable(self):
        self.ready()
        self.assertFalse(self.node.value(0x6041) & 0x400)
        review = self.submit()
        self.assertEqual(review['start'], -1470)
        self.assertEqual(review['target'], -1420)
        self.assertEqual(self.controls(), [0x106, 0x107, 0x127, 0x137, 0x127])
        self.assertEqual(self.node.value(0x6062, signed=True), -1420)
        self.assertEqual([v for i,_,v in requests(self.bus, True) if i == 0x607a], [(-1420)&0xffffffff])
        self.assertFalse(self.node.value(0x6041) & 4)
        self.assertEqual(self.ctl.proofs[1]['phase'], 'submitted')
        print('MANUAL submit: bit10=0, final human target -1420 only, fresh ACK0/1/0, CW106/107/127/137/127, no enable/release')

    def test_enable_plus13_feedback_allowed_original_envelope_no_auto_disable(self):
        self.submit()
        self.step('enable')
        self.assertEqual(self.controls(), [0x106,0x107,0x127,0x137,0x127,0x12f])
        self.ctl.refresh(self.out)
        proof = self.ctl.proofs[1]
        self.assertEqual((proof['start'], proof['target']), (-1470,-1420))
        self.assertEqual(self.node.value(0x6064,signed=True),-1457)
        self.assertEqual(proof['phase'], 'enabled')
        self.assertNotIn(0,self.controls())
        print('MANUAL E: CW12F only, +13U accepted inside original start/budget; no zero-drift cleanup, waits for G')

    def test_explicit_sequence_submit_E_G_X_D_exit_exact_controls(self):
        self.submit()
        self.step('enable')
        self.step('go')
        self.assertEqual(self.ctl.proofs[1]['phase'], 'running')
        self.step('stop')
        self.assertEqual(self.ctl.proofs[1]['phase'], 'stopped')
        for action in ('enable','go'):
            with self.assertRaisesRegex(ValueError,'阶段'):
                self.ctl.review(action,1,None,self.out)
        self.step('disable')
        self.ctl.close()
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f,2,0,6])
        print('MANUAL user sequence CW: 0106 0107 0127 0137 0127 | 012F | 002F 003F 002F | 0002 | 0000 | exit0006')

    def test_E_G_require_this_session_single_use_proof(self):
        self.ready()
        for action in ('enable','go'):
            with self.assertRaisesRegex(ValueError,'证明'):
                self.ctl.review(action,1,None,self.out)
        review=self.submit()
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):
            self.ctl.execute(review,self.out)
        self.assertEqual(len(self.bus.sent),count)
        with self.assertRaisesRegex(ValueError,'阶段'):
            self.ctl.review('go',1,None,self.out)
        self.step('enable')
        with self.assertRaisesRegex(ValueError,'阶段'):
            self.ctl.review('enable',1,None,self.out)

    def test_submitted_target_and_start_never_rebased_for_position_change(self):
        self.ready()
        review=self.ctl.review('relative',1,50,self.out)
        self.node.set_value(0x6064,-1467)
        self.ctl.execute(review,self.out)
        self.assertEqual(self.ctl.proofs[1]['start'],-1470)
        self.assertEqual(self.ctl.proofs[1]['target'],-1420)

    def test_modified_review_rejected_before_io(self):
        self.ready()
        review=self.ctl.review('relative',1,50,self.out)
        review['target']=-1419
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError,'修改'):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.bus.sent,[])

    def test_missing_ack_or_wrong_demand_never_enable_or_G(self):
        for flag in ('ignore_setpoint','keep_old_demand','stuck_ack'):
            self.node=ManualNode()
            self.bus.nodes[0]=self.node
            setattr(self.node,flag,True)
            self.ctl=ManualController(tool,self.args)
            self.ready()
            review=self.ctl.review('relative',1,50,self.out)
            # Exercise the logical ACK deadline independently of host load.
            # Actual missing-reply/quarantine has its own real-time tests.
            clock=Clock()
            with self.subTest(flag=flag),mock.patch('time.monotonic',clock.monotonic), \
                    mock.patch('time.sleep',clock.sleep),self.assertRaises(Exception) as failure:
                self.ctl.execute(review,self.out)
            self.assertNotIn(0x12f,self.controls())
            self.assertNotIn(0x2f,self.controls())
            self.assertFalse(self.ctl.proofs)
            self.assertEqual(self.controls()[-2:],[2,0],(flag,str(failure.exception),self.ctl.row(1)['quarantine_reason']))

    def test_stale_target_after_E_refuses_G_and_cleanup_same_client(self):
        self.submit()
        self.step('enable')
        review=self.ctl.review('go',1,None,self.out)
        session=self.ctl.manual_sessions[1]
        self.node.set_value(0x6062,7800)
        with self.assertRaisesRegex(ValueError,'607A/6062'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x2f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])
        self.assertIs(self.ctl.manual_sessions[1],session)

    def test_following_fault_and_envelope_background_same_client_cleanup(self):
        for key,value in ((0x6041,0x2027),(0x6041,0x2f),(0x6064,-1000),(0x6061,3),(0x6081,101),(0x607c,1)):
            self.node=ManualNode()
            self.bus.nodes[0]=self.node
            self.ctl=ManualController(tool,self.args)
            self.submit()
            self.step('enable')
            client=self.ctl.manual_sessions[1].client
            self.node.set_value(key,value)
            self.ctl.refresh(self.out)
            with self.subTest(key=hex(key),value=value):
                self.assertEqual(self.controls()[-2:],[2,0])
                self.assertIs(self.ctl.manual_sessions[1].client,client)
                self.assertTrue(self.ctl.row(1)['quarantined'])
                self.assertFalse(self.ctl.proofs)

    def test_timeout_isolation_zero_retry_scan_refresh_action_and_exit(self):
        self.submit()
        self.step('enable')
        self.node.drop_reads.add((0x6041,0))
        self.ctl.refresh(self.out)
        count=len(self.bus.sent)
        self.node.drop_reads.clear()
        self.ctl.scan(self.out)
        self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.ctl.review('go',1,None,self.out)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)
        self.assertTrue(self.ctl.row(1)['quarantined'])
        print('MANUAL timeout: same client quarantined; scan/refresh/G/exit zero subsequent requests; independent emergency stop required')

    def test_download_ack_loss_never_retries_cleanup_or_G(self):
        self.ready()
        self.node.drop_write_ack.add((0x607a,0))
        review=self.ctl.review('relative',1,50,self.out)
        with self.assertRaises(Exception):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls(),[])
        count=len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(count,len(self.bus.sent))

    def test_guards_OD_607C_budget_one_degree_device_limits(self):
        self.ready()
        self.ctl.od_reason=None
        with self.assertRaises(ValueError):
            self.ctl.review('relative',1,50,self.out)
        self.ctl.od_reason='explicit mock'
        for setting,val in (('max_displacement',None),('max_displacement',365),('soft_max',-1450)):
            saved=self.ctl.settings.copy()
            self.ctl.settings[setting]=val
            with self.subTest(setting=setting,val=val),self.assertRaises(ValueError):
                self.ctl.review('relative',1,50,self.out)
            self.ctl.settings=saved
        for index,val in ((0x607c,1),(0x607d,-1460),(0x6080,0),(0x607f,99)):
            sub=1 if index==0x607d else 0
            saved=self.node.od[index,sub].data
            self.node.set_value(index,val,sub)
            self.ctl.refresh(self.out)
            with self.subTest(index=hex(index)),self.assertRaises(ValueError):
                self.ctl.review('relative',1,50,self.out)
            self.node.od[index,sub].data=saved
        self.assertFalse(requests(self.bus,True))

    def test_manual_explicit_speed100_can_adjust_above100_under_limits(self):
        self.ready()
        self.assertEqual(self.ctl.manual_velocity,100)
        self.ctl.manual_velocity=250
        review=self.submit()
        self.assertEqual(review['args'].velocity,250)
        self.assertIn('250 U/s', '\n'.join(review['lines']))
        self.assertIn('rpm','\n'.join(review['lines']))

    def test_rescan_and_refresh_reuse_one_original_client(self):
        self.submit()
        self.step('enable')
        original=self.ctl.manual_sessions[1].client
        with mock.patch.object(tool._canopen,'SdoClient',side_effect=AssertionError('replacement client forbidden')):
            with self.assertRaisesRegex(ValueError,'先单独D'):
                self.ctl.scan(self.out)
            self.ctl.refresh(self.out)
            self.step('go')
        self.assertIs(self.ctl.manual_sessions[1].client,original)

    def test_ui_normal_Form_target_E_G_two_stage_and_no_hidden_actions(self):
        self.ready()
        app=ManualApp(tool,self.args)
        app.controller=self.ctl
        app.initial_snapshot=False
        app.next_refresh=float('inf')
        app.current_view='detail'
        press=bench_tests.BenchInteractionTests.press
        for key in ('z','p','b'):
            press(app,key)
            self.assertEqual(app.current_view,'detail')
        press(app,'a')
        self.assertEqual(app.input_field.text,'')
        # Exercise actual Form text input, commit and both F5 boundaries.
        for ch in '-1420':
            press(app,ch)
        press(app,'f5')
        self.assertEqual(app.current_view,'confirm')
        self.assertFalse(requests(self.bus,True))
        for ch in app.pending['token']:
            press(app,ch)
        press(app,'f5')
        app.worker.join(3)
        app.poll()
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127])
        for key,expected in (('e',0x12f),('g',0x2f)):
            press(app,key)
            press(app,'f5')
            self.assertEqual(app.current_view,'confirm')
            for ch in app.pending['token']:
                press(app,ch)
            press(app,'f5')
            app.worker.join(3)
            app.poll()
            self.assertEqual(self.controls()[-1],expected)
        self.assertIn('目标到达=是','\n'.join(app._detail_lines(self.ctl.row(1))))

    def test_ui_V_F2_refused_while_proof_exists(self):
        self.submit()
        app=ManualApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='detail'
        app.on_key('v',ord('v'))
        self.assertEqual(app.current_view,'detail')
        app.on_key('f2',0)
        self.assertEqual(app.current_view,'detail')

    def test_default_backend_cannot_call_manual_go(self):
        from pmp4010_bench import BenchController
        ctl=BenchController(tool,self.args)
        with self.assertRaises(ValueError):
            ctl.review('go',1,0,self.out)

    def test_CLI_mutual_exclusion_and_manual_modifier_guards_before_IO(self):
        cases=[['--manual','--motion','enable'],['--manual','--position'],['--manual','--params','backup'],
               ['--manual','--nmt'],['--manual','--apply'],['--manual','--target','1'],['--manual-velocity','100']]
        for argv in cases:
            with self.subTest(argv=argv),mock.patch('sys.stderr'),self.assertRaises(SystemExit) as exc:
                tool.main(argv)
            self.assertEqual(exc.exception.code,2)
        self.assertEqual(self.bus.sent,[])

    def test_logging_write_or_close_failure_cannot_skip_same_client_cleanup(self):
        import pmp4010_position as position
        for failing in ('event', 'close'):
            self.node=ManualNode()
            self.bus.nodes[0]=self.node
            self.ctl=ManualController(tool,self.args)
            self.submit()
            review=self.ctl.review('enable',1,None,self.out)
            original=getattr(position.Journal,failing)
            def fail(journal,*args,**kwargs):
                # E has already been requested when stage completion is logged.
                if failing=='close':
                    original(journal,*args,**kwargs)
                    raise OSError('injected disk failure')
                if args and args[0] in ('manual_stage_complete','exception'):
                    raise OSError('injected disk failure')
                return original(journal,*args,**kwargs)
            with self.subTest(failing=failing),mock.patch.object(position.Journal,failing,fail),self.assertRaises(OSError):
                self.ctl.execute(review,self.out)
            self.assertEqual(self.controls()[-2:],[2,0])
            self.assertFalse(self.ctl.proofs)
            self.assertTrue(self.ctl.row(1)['quarantined'])
            self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)

    def test_first_fault_or_out_of_envelope_sample_cannot_be_hidden(self):
        for key,value in ((0x6041,0x2f),(0x6064,-1000)):
            self.node=ManualNode()
            self.bus.nodes[0]=self.node
            self.ctl=ManualController(tool,self.args)
            self.submit()
            self.step('enable')
            original=self.bus.send
            seen=[False]
            saved=self.node.od[key,0].data
            def send(frame):
                is_first=(not seen[0] and frame.data[:4]==bytes([0x40,key&255,key>>8,0]))
                if is_first:
                    seen[0]=True
                    self.node.set_value(key,value)
                result=original(frame)
                if is_first:
                    self.node.od[key,0].data=saved
                return result
            with self.subTest(key=hex(key)),mock.patch.object(self.bus,'send',side_effect=send):
                self.ctl.refresh(self.out)
            self.assertEqual(self.controls()[-2:],[2,0])
            self.assertTrue(self.ctl.row(1)['quarantined'])

    def test_scan_with_active_proof_refuses_before_any_request(self):
        self.submit()
        self.step('enable')
        count=len(self.bus.sent)
        self.ctl.args.node_range='1-127'
        with self.assertRaisesRegex(ValueError,'先单独D'):
            self.ctl.scan(self.out)
        self.assertEqual(count,len(self.bus.sent))
        self.assertEqual(self.ctl.proofs[1]['phase'],'enabled')

    def test_fault_ui_shows_locked_and_unknown_stop_without_G(self):
        self.submit()
        self.step('enable')
        self.node.drop_reads.add((0x6041,0))
        self.ctl.refresh(self.out)
        app=ManualApp(tool,self.args)
        app.controller=self.ctl
        app.initial_snapshot=False
        app.next_refresh=float('inf')
        app.poll()
        self.assertIn('锁定',app.progress)
        self.assertIn('停止未确认',app.recovery)

    def test_external_enable_between_profile_and_target_never_writes_target(self):
        self.ready()
        review=self.ctl.review('relative',1,50,self.out)
        event=self.out.event
        def changed(name,data):
            event(name,data)
            if name=='write_readback' and data['object']=='profile_velocity':
                self.node.set_value(0x6041,0x27)
        self.out.event=changed
        with self.assertRaisesRegex(ValueError,'关键状态'):
            self.ctl.execute(review,self.out)
        self.assertFalse(any(i==0x607a for i,_,_ in requests(self.bus,True)))
        self.assertEqual(self.controls(),[2,0])

    def test_X_retains_original_proof_and_protection_until_D(self):
        self.submit()
        self.step('enable')
        self.step('go')
        self.step('stop')
        proof=self.ctl.proofs[1]
        self.assertEqual((proof['start'],proof['target'],proof['phase']),(-1470,-1420,'stopped'))
        self.ctl.refresh(self.out)
        self.assertEqual(self.ctl.proofs[1]['phase'],'stopped')
        for action,value in (('enable',None),('go',None),('relative',-50)):
            with self.assertRaises(ValueError):
                self.ctl.review(action,1,value,self.out)
        self.step('disable')
        self.assertFalse(self.ctl.proofs)
        self.submit(-50)
        self.assertEqual(self.ctl.proofs[1]['phase'],'submitted')

    def test_X_following_fault_envelope_and_parameters_still_cleanup_same_client(self):
        for key,value in ((0x6041,0x2007),(0x6041,0x0f),(0x6064,-1000),(0x6081,101)):
            self.node=ManualNode()
            self.bus.nodes[0]=self.node
            self.ctl=ManualController(tool,self.args)
            self.submit()
            self.step('enable')
            self.step('go')
            self.step('stop')
            client=self.ctl.manual_sessions[1].client
            self.node.set_value(key,value)
            with mock.patch.object(tool._canopen,'SdoClient',side_effect=AssertionError('replacement client forbidden')):
                self.ctl.refresh(self.out)
            with self.subTest(key=hex(key),value=value):
                self.assertEqual(self.controls()[-3:],[2,2,0])
                self.assertIs(self.ctl.manual_sessions[1].client,client)
                self.assertTrue(self.ctl.row(1)['quarantined'])
                self.assertFalse(self.ctl.proofs)

    def test_X_communication_timeout_no_cleanup_retries_or_client_replacement(self):
        self.submit()
        self.step('enable')
        self.step('go')
        self.step('stop')
        client=self.ctl.manual_sessions[1].client
        self.node.drop_reads.add((0x6041,0))
        self.ctl.refresh(self.out)
        count=len(self.bus.sent)
        self.node.drop_reads.clear()
        with mock.patch.object(tool._canopen,'SdoClient',side_effect=AssertionError('replacement client forbidden')):
            self.ctl.scan(self.out)
            self.ctl.refresh(self.out)
            self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)
        self.assertIs(self.ctl.manual_sessions[1].client,client)
        self.assertTrue(self.ctl.row(1)['quarantined'])

    def assert_locked_no_requests(self):
        count=len(self.bus.sent)
        client=self.ctl.manual_sessions[1].client
        self.assertTrue(client.quarantined)
        with mock.patch.object(tool._canopen,'SdoClient',side_effect=AssertionError('replacement client forbidden')):
            self.ctl.scan(self.out)
            self.ctl.refresh(self.out)
            for action in ('enable','go','disable'):
                with self.assertRaises(ValueError):
                    self.ctl.review(action,1,None,self.out)
            self.ctl.close()
        self.assertEqual(count,len(self.bus.sent))
        self.assertIs(self.ctl.manual_sessions[1].client,client)

    def test_send_enqueued_G_then_CanBusError_quarantines_without_cleanup_writes(self):
        self.submit()
        self.step('enable')
        review=self.ctl.review('go',1,None,self.out)
        original=self.bus.send
        def failed(frame):
            result=original(frame)
            if frame.data[:6]==bytes.fromhex('2b4060002f00'):
                raise tool._canbus.CanBusError('injected send failed after enqueue')
            return result
        with mock.patch.object(self.bus,'send',side_effect=failed),self.assertRaises(tool.MotionError):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f])
        self.assert_locked_no_requests()

    def test_X_receive_failure_and_raw_protocol_mismatch_quarantine_without_cleanup(self):
        for failure in ('receive','protocol'):
            self.node=ManualNode()
            self.bus=DelayedBus([self.node])
            tool._std_open_active.return_value=self.bus
            self.ctl=ManualController(tool,self.args)
            self.submit()
            self.step('enable')
            self.step('stop')
            original=self.bus.recv
            def failed(*args,**kwargs):
                if failure=='receive':
                    raise tool._canbus.CanBusError('injected receive failure')
                frame=original(*args,**kwargs)
                if frame is None:
                    return None
                data=bytearray(frame.data)
                data[1]^=1  # A real malformed multiplexor in the received reply.
                return type(frame)(frame.arbitration_id,bytes(data))
            before=list(self.controls())
            with self.subTest(failure=failure),mock.patch.object(self.bus,'recv',side_effect=failed):
                self.ctl.refresh(self.out)
            self.assertEqual(self.controls(),before)
            self.assert_locked_no_requests()

    def test_cleanup_send_enqueued_then_error_never_sends_following_disable(self):
        self.submit()
        self.step('enable')
        self.node.set_value(0x6041,0x2f)
        original=self.bus.send
        def failed(frame):
            result=original(frame)
            if frame.data[:6]==bytes.fromhex('2b4060000200'):
                raise tool._canbus.CanBusError('injected cleanup send failed after enqueue')
            return result
        with mock.patch.object(self.bus,'send',side_effect=failed):
            self.ctl.refresh(self.out)
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,2])
        self.assertIn('停止未确认',self.ctl.row(1)['quarantine_reason'])
        self.assert_locked_no_requests()

    def test_explicit_SDO_abort_is_known_reply_and_keeps_bounded_cleanup(self):
        self.submit()
        self.step('enable')
        del self.node.od[0x6077,0]
        client=self.ctl.manual_sessions[1].client
        self.ctl.refresh(self.out)
        self.assertEqual(self.controls()[-2:],[2,0])
        self.assertFalse(client.quarantined)
        self.assertIn('SDO abort',self.ctl.row(1)['quarantine_reason'])

    def test_exit_send_enqueued_then_error_marks_original_client_uncertain(self):
        self.submit()
        self.step('enable')
        original=self.bus.send
        def failed(frame):
            result=original(frame)
            if frame.data[:6]==bytes.fromhex('2b4060000600'):
                raise tool._canbus.CanBusError('injected exit send failed after enqueue')
            return result
        with mock.patch.object(self.bus,'send',side_effect=failed):
            self.ctl.close(self.out)
        self.assertEqual(self.controls()[-1],6)
        self.assertTrue(self.ctl.manual_sessions[1].client.quarantined)
        count=len(self.bus.sent)
        self.ctl.close()
        with self.assertRaises(ValueError):
            self.ctl.refresh(self.out)
        with self.assertRaises(ValueError):
            self.ctl.scan(self.out)
        self.assertEqual(count,len(self.bus.sent))
