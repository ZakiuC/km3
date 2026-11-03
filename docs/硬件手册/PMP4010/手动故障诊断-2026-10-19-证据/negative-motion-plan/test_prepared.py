"""Only MockBus fixtures; no real CAN, subprocess, SSH or board import."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[4]
sys.path[:0]=[str(HERE),str(REPO/'device/drivers/pmp4010/tools')]
from _test_bench import BenchFixture
from _test_bench_support import DelayedBus, requests, tool, mockbus
from _test_manual import ManualNode, EnabledEdgeNode
from _test_position import Clock
from pmp4010_simple import SimpleController
import pinned_payload
import relative_minus10_once as motion
import reset_only_once as reset


class ResetNode(ManualNode):
    drop_reset_ack=False
    drop_after_code=False
    def handle(self, frame, now):
        if frame.data[:6]==bytes.fromhex('2b4060008000'):
            mockbus.MockNode.handle(self,frame,now)
            if self.drop_reset_ack:
                self._outbox.clear()
            if self.drop_after_code:
                self.drop_reads.add((0x603f,0))
            return
        super().handle(frame,now)


class PreparedTests(BenchFixture):
    def setUp(self):
        super().setUp()
        self.stream=io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.stream))
        self.prepare()
    def prepare(self):
        self.node=ResetNode()
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        tool.load_device_json.return_value[0]['bus']['can'][0]['iface']='can1'
        self.ctl=pinned_payload.make_controller(tool,SimpleController,Path(self.tmp.name)/'motion.jsonl')
        self.addCleanup(self.ctl.close)

    def test_default_plans_open_no_bus(self):
        self.assertEqual(motion.main([]),0)
        self.assertEqual(reset.main([]),0)
        tool._std_open_active.assert_not_called()

    def test_existing_2312_refuses_negative_motion_before_all_writes(self):
        self.node.set_value(0x603f,0x2312)
        with self.assertRaises(ValueError):
            motion.perform_once(self.ctl,self.out)
        self.assertFalse(requests(self.bus,True))
        self.assertEqual(requests(self.bus),[(i,0,0) for i in (0x1000,0x6041,0x6064,0x606c,0x6061,0x603f)])
        self.assertTrue(self.bus.closed)

    def test_one_healthy_negative_target_uses_original_sequence_and_exit(self):
        self.node.set_value(0x607f,1000000)
        before=self.node.value(0x6064,signed=True)
        result=motion.perform_once(self.ctl,self.out)
        self.assertTrue(result['target_reached'])
        self.assertEqual(result['target'],before-327680)
        writes=requests(self.bus,True)
        self.assertEqual([v for i,s,v in writes if i==0x6040],
                         [0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f,6])
        target_wire=(before-327680)&0xffffffff
        self.assertEqual([v for i,s,v in writes if i==0x607a],[target_wire,target_wire])
        self.assertEqual([v for i,s,v in writes if i==0x6081],[218453])
        self.assertTrue(self.bus.closed)

    def test_speed_limit_refuses_before_any_motion_write(self):
        with self.assertRaisesRegex(ValueError,'速度超过设备上限'):
            motion.perform_once(self.ctl,self.out)
        self.assertFalse(requests(self.bus,True))

    def test_motion_observation_timeout_original_cleanup_once_no_second_target(self):
        self.node=EnabledEdgeNode()
        self.bus.nodes[0]=self.node
        self.node.set_value(0x607f,1000000)
        clock=Clock()
        with self.assertRaises(TimeoutError):
            motion.perform_once(self.ctl,self.out,clock=clock.monotonic,pause=lambda _:clock.sleep(11))
        controls=[v for i,s,v in requests(self.bus,True) if i==0x6040]
        self.assertEqual(controls[-2:],[2,0])
        self.assertEqual(controls.count(2),1)
        self.assertNotIn(6,controls)
        self.assertEqual(len(set(v for i,s,v in requests(self.bus,True) if i==0x607a)),1)
        self.assertTrue(self.bus.closed)

    def test_motion_background_quarantine_adds_no_cleanup_or_exit_sends(self):
        self.node.set_value(0x607f,1000000)
        original=self.out.event
        def fail_next_refresh(name,data):
            original(name,data)
            if name=='manual_result' and data['action']=='go' and data['success']:
                self.node.drop_reads.add((0x6041,0))
        self.out.event=fail_next_refresh
        with self.assertRaises(ValueError):
            motion.perform_once(self.ctl,self.out)
        controls=[v for i,s,v in requests(self.bus,True) if i==0x6040]
        self.assertEqual(controls,[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])
        self.assertEqual(requests(self.bus)[-1],(0x6041,0,0))
        self.assertTrue(self.bus.closed)

    def test_reset_exact_eight_transactions_does_not_authorize_motion(self):
        self.node.set_value(0x603f,0x2312)
        result=reset.perform_reset_only(self.ctl,self.out,'Mock会话已显式授权OD未核对项，仅单次脱使能复位')
        self.assertEqual(requests(self.bus),[(0x2002,1,0),(0x6041,0,0),(0x603f,0,0),
            (0x6040,0,0),(0x6040,0,0x80),(0x6040,0,0),(0x603f,0,0),(0x6041,0,0)])
        self.assertEqual(result['error_code'],'0x2312')
        self.assertFalse(result['motion_authorized'])
        self.assertEqual(result['SDO_attempts'],8)
        self.assertEqual(self.stream.getvalue().count('"event": "复位TX"'),8)
        self.assertEqual(self.stream.getvalue().count('"event": "复位RX"'),8)
        self.assertTrue(self.bus.closed)

    def test_reset_mismatched_preconditions_zero_writes(self):
        for addr,value,sub in ((0x2002,1,1),(0x6041,0x27,0),(0x603f,0,0)):
            with self.subTest(addr=hex(addr)):
                self.prepare()
                self.node.set_value(0x603f,0x2312)
                self.node.set_value(addr,value,sub)
                with self.assertRaisesRegex(ValueError,'禁止任何复位写'):
                    reset.perform_reset_only(self.ctl,self.out,'Mock会话显式OD授权，仅复位')
                self.assertEqual(requests(self.bus),[(0x2002,1,0),(0x6041,0,0),(0x603f,0,0)])
                self.assertFalse(requests(self.bus,True))
                self.assertTrue(self.bus.closed)

    def test_reset_lost_80_ack_no_later_zero_read_or_cleanup(self):
        self.node.set_value(0x603f,0x2312)
        self.node.drop_reset_ack=True
        with self.assertRaises(Exception):
            reset.perform_reset_only(self.ctl,self.out,'Mock会话显式OD授权，仅复位')
        self.assertEqual(requests(self.bus),[(0x2002,1,0),(0x6041,0,0),(0x603f,0,0),
            (0x6040,0,0),(0x6040,0,0x80)])
        self.assertTrue(self.bus.closed)

    def test_reset_post_code_timeout_forbids_post_status(self):
        self.node.set_value(0x603f,0x2312)
        self.node.drop_after_code=True
        with self.assertRaises(Exception):
            reset.perform_reset_only(self.ctl,self.out,'Mock会话显式OD授权，仅复位')
        self.assertEqual(requests(self.bus),[(0x2002,1,0),(0x6041,0,0),(0x603f,0,0),
            (0x6040,0,0),(0x6040,0,0x80),(0x6040,0,0),(0x603f,0,0)])
        self.assertTrue(self.bus.closed)

    def test_reset_missing_reason_and_OD_refusal_add_zero_sends(self):
        with self.assertRaisesRegex(ValueError,'明确OD授权理由'):
            reset.perform_reset_only(self.ctl,self.out,'')
        with mock.patch.object(tool,'refuse_motion',return_value=False) as gate:
            with self.assertRaisesRegex(ValueError,'OD闸门拒绝'):
                reset.perform_reset_only(self.ctl,self.out,'已授权会话，仅复位')
        gate.assert_called_once()
        self.assertEqual(gate.call_args.args[2].od_gaps_bypass,'已授权会话，仅复位')
        self.assertFalse(requests(self.bus))
        tool._std_open_active.assert_not_called()

    def test_reset_configuration_alias_drift_refuses_before_SDO(self):
        tool.load_device_json.return_value[0]['bus']['can'][0]['iface']='changed-alias'
        with self.assertRaisesRegex(ValueError,'不再是can1'):
            reset.perform_reset_only(self.ctl,self.out,'已授权会话，仅复位')
        self.assertFalse(requests(self.bus))
        self.assertTrue(self.bus.closed)

    def test_reset_wire_guard_rejects_unplanned_write_before_transport(self):
        guard=reset.WireGuard(self.bus,tool._canbus)
        guard.begin(('read',0x2002,1))
        frame=tool._canbus.Frame(0x601,bytes.fromhex('2b40600080000000'))
        with self.assertRaisesRegex(RuntimeError,'精确白名单'):
            guard.send(frame)
        self.assertEqual(guard.attempts,0)
        self.assertFalse(requests(self.bus))


if __name__=='__main__':
    unittest.main(verbosity=2)
