"""Sealed strict-disabled PP travel, standard MockBus only; no hardware proof."""
from pathlib import Path
import copy
import json
from unittest import mock

from _test_bench_support import DelayedBus, mockbus, position, requests, tool
from _test_disabled_start import HaltProbeNode
from _test_position import Clock
from pmp4010_bench import DisabledStartReview
import pmp4010_tui as ui
import _test_bench as bench_tests


class DisabledMoveNode(HaltProbeNode):
    """Current preload and final motion have independent ACK generations."""
    def __init__(self):
        super().__init__()
        self.final_triggered = False
        self.missing_final_ack = self.missing_final_bit10 = self.stuck_final_ack = False
        self.missing_final_position = self.final_velocity_nonzero = self.old_final_demand = False

    def handle(self, frame, now):
        data = frame.data
        if data[:4] != bytes.fromhex('2b406000') or int.from_bytes(data[4:6], 'little') not in (0x12f, 0x2f, 0x3f):
            return super().handle(frame, now)
        mockbus.MockNode.handle(self, frame, now)
        word = self.value(0x6040)
        sw = 0x27
        if word == 0x3f:
            self.final_triggered = True
            if not self.missing_final_ack:
                sw |= 0x1000
            if not self.old_final_demand:
                self.od[0x6062, 0].data = self.od[0x607a, 0].data
        if word == 0x2f and self.final_triggered:
            if not self.missing_final_bit10:
                sw |= 0x400
            if self.stuck_final_ack:
                sw |= 0x1000
            if not self.missing_final_position:
                self.od[0x6064, 0].data = self.od[0x607a, 0].data
            if self.final_velocity_nonzero:
                self.set_value(0x606c, 1)
        self.set_value(0x6041, sw)
        if (0x6040, 0) in self.drop_write_ack:
            self._outbox.clear()


class DisabledMoveTests(bench_tests.BenchFixture):
    def setUp(self):
        super().setUp()
        self.node = DisabledMoveNode()
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
        self.ctl.args.move_timeout = 5

    def move_review(self, action='relative', value=50, suffix='motion'):
        if self.ctl.context is None:
            self.authorized()
        else:
            self.ctl.refresh(self.out)
        self.ctl.args.motion_log = str(Path(self.tmp.name) / (suffix+'.jsonl'))
        self.bus.sent.clear()
        return self.ctl.review(action, 1, value, self.out)

    def controls(self):
        return [v for i, _, v in requests(self.bus, True) if i == 0x6040]

    def observe(self, hook):
        original = self.out.event
        def event(name, data):
            original(name, data)
            hook(name, data)
        self.out.event = event

    def result(self):
        return [e[2] for e in self.out.events.queue if e[:2] == ('event', 'result')][-1]

    def test_bit10_zero_full_move_fresh_current_then_final_ack_and_ordered_targets(self):
        review = self.move_review()
        self.assertIsInstance(review, DisabledStartReview)
        self.assertEqual([w['value'] for w in review['writes'] if w['key'] == 'target_position'], [-1470, -1420])
        self.assertEqual(review['args'].velocity, 100)
        self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f,2,0])
        self.assertEqual([v for i,_,v in requests(self.bus, True) if i == 0x607a], [(-1470)&0xffffffff,(-1420)&0xffffffff])
        events = [e[2] for e in self.out.events.queue if e[:2] == ('event','disabled_start_sample')]
        for phase in ('起步稳定采样','Halt保持核证采样','Halt使能零漂移采样'):
            self.assertEqual(sum(e['phase'].startswith(phase) for e in events), 5)
        self.assertTrue(self.result()['completed'] and self.result()['disabled_confirmed'])
        self.assertFalse(self.result()['position_unchanged'])
        self.assertTrue(self.result()['position_target_confirmed'] and self.result()['zero_velocity_confirmed'])
        self.assertTrue(self.result()['operation_enable_requested'] and self.result()['travel_executed'])
        print('DISABLED MOVE Mock: current fresh ACK then 12F/Halt stable5, release2F, final607A freshACK/bit10/actual/demand/zero, cleanup2/0')

    def test_bit10_one_strict_disabled_also_preloads_old_internal_target(self):
        self.node.set_value(0x6041, 0x660)
        review = self.move_review('absolute', -1420)
        self.assertIsInstance(review, DisabledStartReview)
        self.ctl.execute(review, self.out)
        self.assertEqual(self.controls()[:5], [0x106,0x107,0x127,0x137,0x127])
        self.assertTrue(self.result()['completed'])

    def test_halt_enable_drift_is_immediate_failure_no_release_or_final_target(self):
        review = self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6064,-1469) if n=='probe_control_readback' and d['expected']==0x12f else None)
        with self.assertRaisesRegex(ValueError, '漂移'):
            self.ctl.execute(review, self.out)
        self.assertEqual(self.controls(), [0x106,0x107,0x127,0x137,0x127,0x12f,2,0])
        self.assertEqual(sum(i==0x607a for i,_,v in requests(self.bus,True)),1)
        self.assertFalse(self.result()['completed'])
        self.assertTrue(self.result()['disabled_confirmed'])

    def test_release_requires_current_demand_ack_zero_and_every_frozen_guard(self):
        review = self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6062,7800) if n=='disabled_start_sample' and d['phase']=='Halt使能零漂移采样5/5' else None)
        with self.assertRaisesRegex(ValueError, '607A/6062'):
            self.ctl.execute(review, self.out)
        self.assertNotIn(0x2f,self.controls())
        self.assertNotIn(0x3f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_final_target_is_written_only_after_release_start_zero_ack_guard(self):
        review = self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x606c,1) if n=='probe_control_readback' and d['expected']==0x2f else None)
        with self.assertRaisesRegex(ValueError, '实际速度非零'):
            self.ctl.execute(review,self.out)
        self.assertEqual(sum(i==0x607a for i,_,v in requests(self.bus,True)),1)
        self.assertNotIn(0x3f,self.controls())

    def test_final_ack_cannot_reuse_preload_ack_or_early_one(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6041,0x1027) if n=='write_readback' and d['object']=='target_position' and d['expected']==-1420 else None)
        with self.assertRaisesRegex(ValueError,'ACK提前'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x3f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_final_missing_ack_is_not_satisfied_by_current_ack(self):
        self.node.missing_final_ack=True
        review=self.move_review()
        with self.assertRaisesRegex(ValueError,'ACK握手超时|整体超时'):
            self.ctl.execute(review,self.out)
        self.assertIn(0x3f,self.controls())
        self.assertFalse(self.result()['completed'])

    def test_final_completion_requires_all_independent_evidence(self):
        for flag in ('missing_final_bit10','stuck_final_ack','missing_final_position','final_velocity_nonzero','old_final_demand'):
            # A separate fixture-like state for each sealed transaction.
            self.node = DisabledMoveNode()
            self.bus.nodes[0] = self.node
            setattr(self.node,flag,True)
            review=self.move_review(suffix=flag)
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.ctl.execute(review,self.out)
            self.assertFalse(self.result()['completed'])
            self.assertEqual(self.controls()[-2:],[2,0])

    def test_cancel_before_halt_enable_has_no_enable_request(self):
        review=self.move_review()
        self.observe(lambda n,d: self.out.cancel_event.set() if n=='disabled_start_sample' and d['phase']=='before_halt_enable' else None)
        with self.assertRaises(KeyboardInterrupt):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x12f,self.controls())
        self.assertFalse(self.result()['operation_enable_requested'])

    def test_final_target_readback_mismatch_never_triggers(self):
        review=self.move_review()
        original=self.bus.send
        def send(frame):
            result=original(frame)
            if frame.data[:4]==bytes.fromhex('237a6000') and int.from_bytes(frame.data[4:8],'little',signed=True)==-1420:
                self.node.set_value(0x607a,-1421)
            return result
        with mock.patch.object(self.bus,'send',side_effect=send), self.assertRaisesRegex(ValueError,'写读回不一致'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x3f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_final_target_ack_loss_quarantines_no_cleanup_or_new_client(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.drop_write_ack.add((0x607a,0)) if n=='disabled_start_sample' and d['phase']=='after_halt_release' else None)
        with self.assertRaises(Exception):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls()[-1],0x2f)
        self.assertNotIn(0x3f,self.controls())
        count=len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)

    def test_every_move_repeats_preload_and_cannot_replay_review(self):
        review=self.move_review()
        self.ctl.execute(review,self.out)
        count=len(self.bus.sent)
        with self.assertRaisesRegex(ValueError,'失效|复用'):
            self.ctl.execute(review,self.out)
        self.assertEqual(len(self.bus.sent),count)
        self.node.final_triggered=False
        review2=self.move_review(value=-50,suffix='return')
        self.ctl.execute(review2,self.out)
        self.assertEqual(self.controls()[:5],[0x106,0x107,0x127,0x137,0x127])
        self.assertEqual(self.node.value(0x6064,signed=True),-1470)

    def test_velocity_library_required_before_download(self):
        review=self.move_review()
        with mock.patch.object(position,'require_velocity_library',side_effect=ValueError('velocity ABI absent')), self.assertRaisesRegex(ValueError,'ABI'):
            self.ctl.execute(review,self.out)
        self.assertFalse(requests(self.bus,True))

    def test_sealed_ordered_targets_action_and_args_tamper_rejected_before_io(self):
        mutations = [lambda r: r['writes'].reverse(),
                     lambda r: r['writes'][-1].update(value=-1419),
                     lambda r: r.update(action='probe_pp_start'),
                     lambda r: setattr(r['args'],'target',-1419),
                     lambda r: r.pop('disabled_start_before'),
                     lambda r: r.clear()]
        for i, mutate in enumerate(mutations):
            review=self.move_review(suffix='tamper'+str(i))
            mutate(review)
            self.bus.sent.clear()
            with self.subTest(i=i), self.assertRaisesRegex(ValueError,'失效|修改|复用'):
                self.ctl.execute(review,self.out)
            self.assertEqual(self.bus.sent,[])

    def test_stripped_dict_origin_cannot_fall_back_to_ordinary_disabled_motion(self):
        review=self.move_review()
        forged=dict(review)
        forged.pop('disabled_start_id')
        forged.pop('disabled_start_before')
        forged['args']=copy.copy(review['args'])
        del forged['args']._disabled_start_origin
        self.node.set_value(0x6041,0x660)
        with self.assertRaisesRegex(ValueError,'严格disabled'):
            self.ctl.execute(forged,self.out)
        self.assertFalse(requests(self.bus,True))

    def test_current_demand_cannot_revert_after_final_target_write(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6062,7800) if n=='write_readback' and d['object']=='target_position' and d['expected']==-1420 else None)
        with self.assertRaisesRegex(ValueError,'活动目标'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x3f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_halt_enable_early_ack_one_is_not_hidden_by_later_zero(self):
        review=self.move_review()
        original=self.bus.send
        enabled=[False]
        seen=[0]
        def send(frame):
            if frame.data[:4]==bytes.fromhex('2b406000') and int.from_bytes(frame.data[4:6],'little')==0x12f:
                enabled[0]=True
            if enabled[0] and frame.data[:4]==bytes.fromhex('40416000'):
                seen[0]+=1
                self.node.set_value(0x6041,0x1027 if seen[0]==1 else 0x27)
            return original(frame)
        with mock.patch.object(self.bus,'send',side_effect=send), self.assertRaisesRegex(ValueError,'ACK提前'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x2f,self.controls())
        self.assertFalse(self.result()['completed'])

    def test_preload_failure_never_attempts_operation_enable(self):
        self.node.ignore_setpoint=True
        review=self.move_review()
        with self.assertRaisesRegex(ValueError,'ACK握手超时|整体超时'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x12f,self.controls())
        self.assertFalse(self.result()['operation_enable_requested'])

    def test_whole_deadline_is_not_reset_between_preload_and_enable(self):
        self.ctl.args.move_timeout=100
        review=self.move_review()
        self.observe(lambda n,d: setattr(self.clock,'now',self.clock.now+13) if n=='disabled_start_sample' and d['phase']=='Halt使能零漂移采样5/5' else None)
        with self.assertRaisesRegex(ValueError,'整体超时|时限'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x2f,self.controls())
        self.assertNotIn(0x3f,self.controls())

    def test_final_target_read_timeout_quarantines_and_never_triggers_or_cleans_up(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.drop_reads.add((0x607a,0)) if n=='probe_write_attempt' and d['object']=='target_position' and d['expected']==-1420 else None)
        with self.assertRaises(Exception):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls()[-1],0x2f)
        self.assertNotIn(0x3f,self.controls())
        self.assertIsNone(self.result()['disabled_confirmed'])
        count=len(self.bus.sent)
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)

    def test_move_keeps_initial_samples_and_sealed_mode_cal_offset_limits_guards(self):
        cases=[(0x6064,-1469,0),(0x606c,1,0),(0x6061,3,0),(0x607c,1,0),(0x608f,131073,1),(0x607f,9999,0)]
        for i,(index,value,sub) in enumerate(cases):
            self.node=DisabledMoveNode()
            self.bus.nodes[0]=self.node
            review=self.move_review(suffix='initial'+str(i))
            event=self.out.event
            self.observe(lambda n,d: self.node.set_value(index,value,sub) if n=='disabled_start_sample' and d['phase']=='起步稳定采样3/5' else None)
            with self.subTest(index=hex(index)), self.assertRaises(ValueError):
                self.ctl.execute(review,self.out)
            self.assertFalse(requests(self.bus,True))
            self.out.event=event

    def test_halt_enable_fault_never_releases_or_writes_final_target(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6041,0x2f) if n=='probe_control_readback' and d['expected']==0x12f else None)
        with self.assertRaisesRegex(ValueError,'故障'):
            self.ctl.execute(review,self.out)
        self.assertNotIn(0x2f,self.controls())
        self.assertEqual(sum(i==0x607a for i,_,v in requests(self.bus,True)),1)

    def test_motion_first_position_sample_outside_original_budget_cannot_be_hidden(self):
        review=self.move_review()
        self.observe(lambda n,d: self.node.set_value(0x6064,-1105) if n=='probe_control_readback' and d['expected']==0x3f else None)
        with self.assertRaisesRegex(ValueError,'单次位移上限'):
            self.ctl.execute(review,self.out)
        self.assertFalse(self.result()['completed'])
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_tui_relative_has_two_stage_review_and_ordered_current_final_targets(self):
        self.move_review()
        app=ui.PmpApp(tool,self.args)
        app.controller=self.ctl
        app.initial_snapshot=False
        app.next_refresh=float('inf')
        app.current_view='detail'
        app.begin_action('relative')
        app.input_field._draft='50'
        bench_tests.BenchInteractionTests.press(app,'f5')
        self.assertEqual(app.current_view,'confirm')
        self.assertIsInstance(app.pending,DisabledStartReview)
        self.assertEqual([w['value'] for w in app.pending['writes'] if w['key']=='target_position'],[-1470,-1420])
        self.assertFalse(requests(self.bus,True))
        app.input_field._draft=app.pending['token']
        bench_tests.BenchInteractionTests.press(app,'f5')
        app.worker.join(3)
        app.poll()
        records=[json.loads(line) for line in Path(self.ctl.args.motion_log).read_text().splitlines()]
        self.assertTrue([r for r in records if r['event']=='result'][-1]['completed'])
        self.assertIn('目标位置=True',app.recovery)

    def test_low_positive_profile_is_preserved_without_raise(self):
        self.node.set_value(0x6081,50)
        review=self.move_review()
        self.assertEqual(review['args'].velocity,50)
        self.ctl.execute(review,self.out)
        self.assertEqual(self.node.value(0x6081),50)
        self.assertTrue(self.result()['completed_motion'])

    def test_cancel_during_halt_enabled_sampling_stops_without_final_target(self):
        review=self.move_review()
        self.observe(lambda n,d: self.out.cancel_event.set() if n=='disabled_start_sample' and d['phase']=='Halt使能零漂移采样3/5' else None)
        with self.assertRaises(KeyboardInterrupt):
            self.ctl.execute(review,self.out)
        self.assertTrue(self.result()['operation_enable_attempted'])
        self.assertFalse(self.result()['travel_trigger_attempted'] or self.result()['completed_motion'])
        self.assertNotIn(0x2f,self.controls())
        self.assertEqual(self.controls()[-2:],[2,0])

    def test_moving_first_and_last_position_reads_each_validate_original_budget(self):
        for bad_read in (1,2):
            self.node=DisabledMoveNode()
            self.bus.nodes[0]=self.node
            review=self.move_review(suffix='freshposition'+str(bad_read))
            original=self.bus.send
            active=[False]
            reads=[0]
            def send(frame):
                if frame.data[:4]==bytes.fromhex('2b406000'):
                    word=int.from_bytes(frame.data[4:6],'little')
                    active[0]=word==0x3f
                if active[0] and frame.data[:4]==bytes.fromhex('40646000'):
                    reads[0]+=1
                    self.node.set_value(0x6064,-1105 if reads[0]==bad_read else -1470)
                return original(frame)
            with self.subTest(bad_read=bad_read),mock.patch.object(self.bus,'send',side_effect=send),self.assertRaisesRegex(ValueError,'单次位移上限'):
                self.ctl.execute(review,self.out)
            self.assertFalse(self.result()['completed_motion'])

    def test_enabled_stable_window_rechecks_mode_calibration_home_and_limits(self):
        cases=[(0x606c,1,0),(0x6061,3,0),(0x607c,1,0),(0x608f,131073,1),(0x607f,9999,0)]
        for i,(index,value,sub) in enumerate(cases):
            self.node=DisabledMoveNode()
            self.bus.nodes[0]=self.node
            review=self.move_review(suffix='enabled'+str(i))
            event=self.out.event
            self.observe(lambda n,d: self.node.set_value(index,value,sub) if n=='disabled_start_sample' and d['phase']=='Halt使能零漂移采样3/5' else None)
            with self.subTest(index=hex(index)),self.assertRaises(ValueError):
                self.ctl.execute(review,self.out)
            self.assertNotIn(0x2f,self.controls())
            self.assertNotIn(0x3f,self.controls())
            self.assertEqual(self.controls()[-2:],[2,0])
            self.out.event=event

    def test_cancelled_review_and_changed_settings_each_refuse_before_io(self):
        review=self.move_review()
        self.ctl.discard_prepare()
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError,'失效'):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.bus.sent,[])
        review=self.move_review(suffix='settings')
        self.ctl.settings['max_displacement']=100
        self.bus.sent.clear()
        with self.assertRaisesRegex(ValueError,'设置'):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.bus.sent,[])

    def test_original_gear_scaled_int32_envelope_guard_remains_mandatory(self):
        self.authorized()
        self.node.set_value(0x6091,2,1)
        self.ctl.settings.update(soft_min=-2147483648,max_displacement=180)
        with self.assertRaisesRegex(ValueError,'G换算.*Int32'):
            self.move_review()
        self.assertFalse(requests(self.bus,True))

    def test_final_completion_observation_cannot_outlive_whole_deadline(self):
        review=self.move_review()
        self.observe(lambda n,d: setattr(self.clock,'now',self.clock.now+16) if n=='disabled_start_sample' and d['phase']=='final_target_completion' else None)
        with self.assertRaisesRegex(ValueError,'整体超时'):
            self.ctl.execute(review,self.out)
        self.assertFalse(self.result()['completed_motion'] or self.result()['completed'])
        self.assertTrue(self.result()['position_target_confirmed'] and self.result()['zero_velocity_confirmed'])

    def test_abi_preflight_failure_with_new_enabled_observation_owes_initial_stop(self):
        review=self.move_review()
        self.node.set_value(0x6041,0x27)
        with mock.patch.object(position,'require_velocity_library',side_effect=ValueError('injected ABI unavailable')),self.assertRaisesRegex(ValueError,'injected ABI unavailable'):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls(),[2,0])
        outer=[e[2] for e in self.out.events.queue if e[:2]==('event','bench_result')][-1]
        self.assertFalse(outer['success'])
        self.assertTrue(outer['disabled_confirmed'] and outer['operation_enabled_observed'])
        self.assertFalse(outer['operation_enable_attempted'])

    def test_prewrite_cancel_with_new_enabled_inner_observation_owes_initial_stop(self):
        review=self.move_review()
        def cancel(name,data):
            if name=='disabled_start_sample' and data['phase']=='before_setup':
                self.node.set_value(0x6041,0x27)
                self.out.cancel_event.set()
        self.observe(cancel)
        with self.assertRaises(KeyboardInterrupt):
            self.ctl.execute(review,self.out)
        self.assertEqual(self.controls(),[2,0])
        self.assertTrue(self.result()['disabled_confirmed'] and self.result()['operation_enabled_observed'])
        self.assertFalse(self.result()['completed'] or self.result()['operation_enable_attempted'])
