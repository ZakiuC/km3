"""Single-confirmation jog transactions against actual standard MockBus SDO."""
import copy
from fractions import Fraction
import queue
import threading
import time
from unittest import mock

from _test_bench import BenchFixture
from _test_manual import ManualNode, DelayedManualNode, EnabledEdgeNode
from _test_bench_support import DelayedBus, requests, tool
from pmp4010_simple import SimpleController, SimpleApp


class SimpleTests(BenchFixture):
    def setUp(self):
        super().setUp()
        self.node = ManualNode()
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        self.args.manual_velocity = 100  # Existing explicit-speed regressions.
        self.ctl = SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)

    def ready(self):
        self.online()
        self.ctl.authorize()
        self.bus.sent.clear()

    def controls(self):
        return [v for i,_,v in requests(self.bus,True) if i == 0x6040]

    def action(self,kind):
        review = self.ctl.review_action(kind,self.out)
        self.ctl.execute_action(review,self.out)
        return review

    def linear_ready(self, conversion=None):
        self.args.manual_linear = True
        self.args.manual_mm_per_rev = conversion
        self.ctl = SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()

    def coordinate_action(self,kind,text):
        review=self.ctl.review_action(kind,self.out,text)
        self.ctl.execute_action(review,self.out)
        return review

    def test_linear_mm_s_exact_speed_default_quantization_and_review_seal(self):
        self.args.manual_velocity = 1000
        self.node.set_value(0x607f,100000)  # Explicit mock limit permits 1mm/s.
        self.linear_ready('4')
        row = self.ctl.row(1)
        self.assertEqual('设置 0.0305176 mm/s（1000 U/s）', self.ctl.speed_line(row))
        self.assertEqual((self.ctl.manual_velocity,self.ctl.next_velocity),(1000,1000))
        for text,units,actual in (('1',32768,'1'),('1/10',3276,'0.0999756')):
            self.ctl.set_value('speed',text)
            self.assertEqual(self.ctl.next_velocity,units)
            self.assertEqual(self.ctl.requested_mm_s,Fraction(text))
            review=self.ctl.review_action('forward',self.out)
            self.assertEqual(review['args'].velocity,units)
            self.assertIn('实际 '+actual+' mm/s（'+str(units)+' U/s）',' '.join(review['lines']))
        self.ctl.execute_action(review,self.out)
        self.assertIn((0x6081,0,3276),requests(self.bus,True))
        self.ctl.refresh(self.out)
        review=self.ctl.review_action('backward',self.out)
        before=len(requests(self.bus,True))
        self.ctl.requested_mm_s=Fraction(9)
        with self.assertRaisesRegex(ValueError,'审阅失效'):
            self.ctl.execute_action(review,self.out)
        self.assertEqual(len(requests(self.bus,True)),before)
        self.ctl.requested_mm_s=Fraction('1/10')
        review=self.ctl.review_action('backward',self.out)
        review['requested_mm_s']=Fraction(9)
        with self.assertRaisesRegex(ValueError,'修改'):
            self.ctl.execute_action(review,self.out)
        self.assertEqual(len(requests(self.bus,True)),before)

    def test_linear_mm_s_limits_missing_scale_and_invalid_input(self):
        self.linear_ready()
        with self.assertRaisesRegex(ValueError,'换算未设置'):
            self.ctl.set_value('speed','1')
        self.ctl.set_coordinate('conversion',self.out,'4')
        self.node.set_value(0x2006,9,sub=0x12)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError,'单位未知'):
            self.ctl.set_value('speed','1')
        self.node.set_value(0x2006,0,sub=0x12)
        self.ctl.refresh(self.out)
        row=copy.deepcopy(self.ctl.row(1))
        self.ctl._put(dict(row,calibration=None))
        with self.assertRaises(ValueError):
            self.ctl.set_value('speed','1')
        self.ctl._put(row)
        for text in ('nan','inf','0','-1','1/0','0.00000001','1000000000'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                self.ctl.set_value('speed',text)
        self.node.set_value(0x607f,32767)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError,'设备上限'):
            self.ctl.set_value('speed','1')
        self.node.set_value(0x607f,100000)
        self.node.set_value(0x6080,14)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError,'设备上限'):
            self.ctl.set_value('speed','1')  # Independent: 1mm/s == 15rpm.
        self.assertFalse(requests(self.bus,True))

    def test_linear_mm_s_nonunit_gear_and_signed_feedback_units(self):
        self.node.set_value(0x6091,3,sub=1)
        self.node.set_value(0x607f,100000)
        self.linear_ready('4')
        self.ctl.set_value('speed','1')
        self.assertEqual(self.ctl.next_velocity,10922)  # floor(131072/12).
        row=copy.deepcopy(self.ctl.row(1))
        for unit,raw,expected in ((0,15,'1'),(0,-15,'-1'),(1,10922,'0.999939'),(1,-10922,'-0.999939')):
            row['values'].update(velocity_feedback_unit=unit,actual_velocity=raw)
            self.assertEqual(self.ctl.feedback_speed(row),expected+' mm/s')
        row['values']['velocity_feedback_unit']=9
        self.assertIn('未可换算',self.ctl.feedback_speed(row))
        row['values']['velocity_feedback_unit']=0
        row['calibration']=None
        self.assertIn('未可换算',self.ctl.feedback_speed(row))
        with self.assertRaisesRegex(ValueError,'标定不可用'):
            self.ctl.coordinate_scale(None)
        self.ctl.mm_per_motor_revolution=None
        self.assertIn('未可换算',self.ctl.feedback_speed(self.ctl.row(1)))

    def test_linear_conversion_retains_u_s_clears_request_and_cancels_review(self):
        self.node.set_value(0x607f,100000)
        self.linear_ready('4')
        self.ctl.set_value('speed','1')
        review=self.ctl.review_action('forward',self.out)
        active=self.ctl.manual_velocity
        self.ctl.set_coordinate('conversion',self.out,'8')
        self.assertEqual((self.ctl.next_velocity,self.ctl.manual_velocity),(32768,active))
        self.assertIsNone(self.ctl.requested_mm_s)
        self.assertEqual('设置 2 mm/s（32768 U/s）',self.ctl.speed_line(self.ctl.row(1)))
        with self.assertRaisesRegex(ValueError,'取消'):
            self.ctl.execute_action(review,self.out)
        self.assertFalse(requests(self.bus,True))

    def test_linear_constructor_parser_and_explicit_scale_are_io_free(self):
        from contextlib import redirect_stderr
        import io
        for argv in (['--manual-linear'], ['--manual','--manual-mm-per-rev','10'],
                     ['--manual','--manual-linear','--manual-mm-per-rev','nan'],
                     ['--manual','--manual-linear','--manual-mm-per-rev','0'],
                     ['--manual','--manual-linear','--manual-mm-per-rev','-1']):
            with self.subTest(argv=argv),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                tool.main(argv)
        args=tool.build_parser().parse_args(['--manual','--manual-linear'])
        ctl=SimpleController(tool,args)
        self.assertTrue(ctl.linear)
        self.assertIsNone(ctl.mm_per_motor_revolution)
        self.assertEqual((ctl.manual_velocity,ctl.next_velocity),(1000,1000))
        self.assertEqual(ctl.step_mm,Fraction('0.1'))
        args.manual_mm_per_rev='10/3'
        ctl=SimpleController(tool,args)
        self.assertEqual(ctl.mm_per_motor_revolution,Fraction(10,3))
        self.assertFalse(self.bus.sent)
        self.assertIn('--manual-mm-per-rev',tool.build_parser().format_help())

    def test_linear_missing_scale_blocks_all_motion_but_zero_is_read_only(self):
        self.linear_ready()
        for kind in ('forward','backward','hold','relative','absolute'):
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'换算未设置'):
                self.ctl.review_action(kind,self.out,'1')
        self.node.set_value(0x6064,-1451)
        self.ctl.set_coordinate('zero',self.out)
        self.assertEqual(self.ctl.software_zero,-1451)
        self.assertFalse(requests(self.bus,True))
        self.ctl.set_coordinate('conversion',self.out,'10')
        self.assertEqual(self.ctl.mm_per_motor_revolution,10)
        self.assertFalse(requests(self.bus,True))

    def test_linear_relative_absolute_signed_fraction_and_over_one_degree(self):
        self.linear_ready('10')
        scale=Fraction(10,131072)
        self.assertEqual(self.ctl.coordinate_scale(self.ctl.row(1)['calibration']),scale)
        for kind,text in (('relative','+1/10'),('relative','-1/20'),('absolute','-1/20'),('absolute','+1/20')):
            self.ctl.refresh(self.out)
            start=self.ctl.row(1)['values']['actual_position']
            base=start if kind=='relative' else self.ctl.software_zero
            review=self.coordinate_action(kind,text)
            self.assertEqual(review['target'],base+int(Fraction(text)/scale))
            self.assertEqual(self.node.value(0x607a,signed=True),review['target'])
            self.assertEqual(self.node.value(0x6064,signed=True),review['target'])
            if text=='+1/10':
                self.assertGreater(abs(review['target']-review['start'])*Fraction(360,131072),1)
        self.assertEqual(self.ctl.origin,-1470)
        self.assertEqual(self.ctl.settings['soft_min'],-2147483627)
        self.assertEqual(self.ctl.settings['soft_max'],2147483627)
        self.assertEqual(self.controls().count(0x3f),4)
        self.assertNotIn('另限电机轴1度','\n'.join(review['lines']))

    def test_user_four_mm_per_motor_turn_exact_quantization_and_large_endpoint_span(self):
        self.linear_ready('4')
        scale=self.ctl.coordinate_scale(self.ctl.row(1)['calibration'])
        self.assertEqual(scale,Fraction(1,32768))
        self.assertEqual(self.ctl.quantities(self.ctl.row(1))[:2],(3276,Fraction(819,8192)))
        for text,delta in (('+.1',3276),('-.1',-3276)):
            review=self.ctl.review_action('absolute',self.out,text)
            self.assertEqual(review['target'],-1470+delta)
        # Absolute endpoint validity, not a signed subtraction, defines the
        # measuring range. No real mechanism travels in this host-only test.
        self.node.set_value(0x6064,-2000000000)
        self.ctl.refresh(self.out)
        target=2000000000
        request=Fraction(target-self.ctl.software_zero)*scale
        review=self.ctl.review_action('absolute',self.out,str(request))
        self.assertEqual(review['target'],target)
        self.assertGreater(target-review['start'],0x7fffffff)
        self.assertEqual(self.ctl.settings['max_displacement'],4294967254)
        self.assertFalse(requests(self.bus,True))

    def test_angular_zero_relative_absolute_quantize_offset_before_negative_origin(self):
        self.ready()
        scale=Fraction(360,131072)
        self.node.set_value(0x6064,-1451)
        self.ctl.set_coordinate('zero',self.out)
        for kind,text in (('relative','+.1'),('relative','-.05'),('absolute','-.05'),('absolute','+.05')):
            self.ctl.refresh(self.out)
            base=self.ctl.row(1)['values']['actual_position'] if kind=='relative' else -1451
            review=self.coordinate_action(kind,text)
            self.assertEqual(review['target'],base+int(Fraction(text)/scale))
            self.assertEqual(self.node.value(0x607a,signed=True),review['target'])

    def test_zero_uses_fresh_read_preserves_fixed_envelope_raw_target_and_review_expiry(self):
        self.ready()
        review=self.ctl.review_action('forward',self.out)
        settings=copy.deepcopy(self.ctl.settings)
        rawtarget=self.node.value(0x607a,signed=True)
        self.node.set_value(0x6064,-1400)
        self.ctl.set_coordinate('zero',self.out)
        self.assertEqual(self.ctl.software_zero,-1400)
        self.assertEqual(self.ctl.origin,-1470)
        self.assertEqual(self.ctl.settings,settings)
        self.assertEqual(self.node.value(0x607a,signed=True),rawtarget)
        self.assertFalse(requests(self.bus,True))
        with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
        with self.assertRaisesRegex(ValueError,'行程'):
            self.ctl.review_action('absolute',self.out,'1')
        self.assertFalse(requests(self.bus,True))

    def test_zero_under_hold_or_reached_running_preserves_physical_proof_and_guard(self):
        for kind in ('hold','forward'):
            with self.subTest(kind=kind):
                self.setUp()
                self.ready()
                self.action(kind)
                self.ctl.refresh(self.out)
                proof=self.ctl.proofs[1]
                frozen=copy.deepcopy({k:v for k,v in proof.items() if k!='session'})
                settings=copy.deepcopy(self.ctl.settings)
                before=len(requests(self.bus,True))
                self.ctl.set_coordinate('zero',self.out)
                captured=self.ctl.software_zero
                self.assertIs(self.ctl.proofs[1],proof)
                self.assertEqual({k:v for k,v in proof.items() if k!='session'},frozen)
                self.assertEqual(self.ctl.settings,settings)
                self.assertEqual(len(requests(self.bus,True)),before)
                # Physical proof's raw target is unchanged while software
                # zero stays at the captured sample, not at later feedback.
                self.node.set_value(0x6064,captured+10)
                self.ctl.refresh(self.out)
                review=self.ctl.review_action('absolute',self.out,'0')
                self.assertEqual(review['target'],captured)
                self.ctl.cancel_review()
                self.node.set_value(0x607a,proof['target']+1)
                self.ctl.refresh(self.out)
                self.assertTrue(self.ctl.blocked)
                self.assertNotIn(1,self.ctl.proofs)

    def test_zero_external_enable_moving_and_nonzero_home_offset_refused(self):
        self.ready()
        initial=self.ctl.software_zero
        for index,value in ((0x606c,1),(0x607c,1)):
            self.node.set_value(index,value)
            with self.assertRaises(ValueError):self.ctl.set_coordinate('zero',self.out)
            self.node.set_value(index,0)
        self.node.set_value(0x6040,0x2f)
        self.node.set_value(0x6041,0x237)
        with self.assertRaisesRegex(ValueError,'失能'):
            self.ctl.set_coordinate('zero',self.out)
        self.assertEqual(self.ctl.software_zero,initial)
        self.assertFalse(requests(self.bus,True))

    def test_zero_read_failure_locks_and_never_retries(self):
        self.ready()
        self.action('hold')
        initial=self.ctl.software_zero
        self.node.drop_reads.add((0x6064,0))
        with self.assertRaises(BaseException):self.ctl.set_coordinate('zero',self.out)
        self.assertTrue(self.ctl.blocked)
        self.assertEqual(self.ctl.software_zero,initial)
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.ctl.set_coordinate('zero',self.out)
        self.assertEqual(len(self.bus.sent),count)

    def test_linear_coefficient_and_zero_revision_sealed_and_disabled_change_only(self):
        self.linear_ready('10')
        review=self.ctl.review_action('relative',self.out,'.1')
        self.ctl.set_coordinate('conversion',self.out,'20/3')
        with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
        for field,value in (('software_zero',-1471),('mm_per_motor_revolution',Fraction(30)),
                            ('coordinate_revision',9),('linear',False),('step_mm',Fraction(2))):
            previous=getattr(self.ctl,field)
            review=self.ctl.review_action('relative',self.out,'.1')
            setattr(self.ctl,field,value)
            with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
            setattr(self.ctl,field,previous)
        self.assertFalse(requests(self.bus,True))
        self.action('hold')
        self.ctl.refresh(self.out)
        before=len(requests(self.bus,True))
        with self.assertRaisesRegex(ValueError,'失能'):
            self.ctl.set_coordinate('conversion',self.out,'10')
        self.assertIn(1,self.ctl.proofs)
        self.assertEqual(len(requests(self.bus,True)),before)

    def test_linear_int32_device_range_unquantized_boundary_and_zero_displacement_rejected(self):
        self.linear_ready('10')
        for kind,text in (('relative','0'),('relative','.00000001'),('absolute','0'),
                          ('absolute','1e30'),('relative','-1e30')):
            with self.subTest(kind=kind,text=text),self.assertRaises(ValueError):
                self.ctl.review_action(kind,self.out,text)
        # One tiny fraction beyond 607D must not be rounded back inside.
        self.ctl.settings['soft_max']=-1400
        value=(Fraction(70)+Fraction(1,10))*Fraction(10,131072)
        with self.assertRaisesRegex(ValueError,'量化不能'):
            self.ctl.review_action('relative',self.out,str(value))
        self.assertFalse(requests(self.bus,True))

    def test_extreme_finite_conversion_is_rendered_and_cannot_overflow_or_fake_motion(self):
        from pmp4010_simple import number
        self.linear_ready()
        for value in ('1e400','1e-400'):
            self.ctl.set_coordinate('conversion',self.out,value)
            self.assertTrue(number(self.ctl.mm_per_motor_revolution))
            app=SimpleApp(tool,self.args)
            app.controller=self.ctl
            app.current_view='main'
            app.rows,app.cols=24,80
            self.assertIn('软件行程未设限','\n'.join(app.build().to_lines()))
            with self.assertRaises(ValueError):self.ctl.review_action('forward',self.out)
        self.assertFalse(requests(self.bus,True))

    def test_constructor_startup_and_authorization_are_read_only(self):
        self.assertEqual(self.ctl.node,1)
        self.assertFalse(self.bus.sent)
        self.online()
        self.ctl.authorize()
        self.assertFalse(requests(self.bus,True))
        self.assertEqual(self.ctl.origin,-1470)
        self.assertEqual(self.ctl.settings,dict(soft_min=-1834,soft_max=-1106,max_displacement=364))
        with self.assertRaises(ValueError):
            self.ctl.authorize()

    def test_production_default1000_and_explicit100_preserved_with_live_rpm(self):
        from fractions import Fraction
        defaults=tool.build_parser().parse_args(['--manual'])
        explicit=tool.build_parser().parse_args(['--manual','--manual-velocity','100'])
        default_ctl=SimpleController(tool,defaults)
        explicit_ctl=SimpleController(tool,explicit)
        self.assertEqual((default_ctl.manual_velocity,default_ctl.next_velocity),(1000,1000))
        self.assertEqual((explicit_ctl.manual_velocity,explicit_ctl.next_velocity),(100,100))
        self.assertEqual(default_ctl.step_degrees,Fraction('0.1'))
        self.args.manual_velocity=defaults.manual_velocity
        self.ctl=SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()
        row=self.ctl.row(1)
        self.assertEqual(self.ctl.quantities(row)[2],Fraction(1875,4096))
        self.assertEqual(explicit_ctl.quantities(row)[2],Fraction(375,8192))
        review=self.action('forward')
        self.assertEqual(review['args'].velocity,1000)
        self.assertIn('0.457764 rpm（1000 U/s）','\n'.join(review['lines']))
        self.assertEqual([v for i,_,v in requests(self.bus,True) if i==0x6081],[1000])
        self.assertIn('默认1000',tool.build_parser().format_help())
        print('MANUAL production default: 1000U/s -> live608F/6091 0.457763671875rpm; explicit100 kept; step0.1deg unchanged')

    def test_one_forward_confirmation_uses_original_handshake_and_plus13(self):
        self.ready()
        review = self.ctl.review_action('forward',self.out)
        self.assertFalse(requests(self.bus,True))
        self.node.set_value(0x6064,-1467)
        self.ctl.execute_action(review,self.out)
        self.assertEqual((review['start'],review['target']),(-1470,-1434))
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),(-1470,-1434))
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])
        self.assertEqual(self.ctl.proofs[1]['phase'],'running')
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):
            self.ctl.execute_action(review,self.out)
        self.assertEqual(len(self.bus.sent),count)
        print('SIMPLE forward: one immutable confirmation; CW106/107/127/137/127/12F/2F/3F/2F; +13U allowed; fixed start/target')

    def test_delayed_states_full_simple_action_has_actual_CW_diagnostics(self):
        import json
        from pathlib import Path
        self.node=DelayedManualNode()
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        self.ctl=SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()
        review=self.action('forward')
        self.assertEqual(self.ctl.proofs[1]['target'],review['target'])
        self.assertEqual(self.ctl.proofs[1]['start'],review['start'])
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])
        records=[json.loads(line) for path in Path(self.tmp.name).glob('*.jsonl') for line in path.read_text().splitlines()]
        ack_words=[r['controlword'] for r in sorted(records,key=lambda r:r['timestamp_utc'])
                   if r['event']=='manual_controlword' and r['point']=='write_ack']
        self.assertEqual(ack_words,self.controls())
        for word,count in ((0x12f,1),(0x2f,2),(0x3f,1)):
            samples=[r for r in records if r['event']=='manual_transition_snapshot' and r['controlword']==word
                     and r['phase'] in ('Halt使能核对','使能后新ACK核对')]
            self.assertEqual(len(samples),count)
            for sample in samples:
                self.assertIsNotNone(sample['raw_statusword'])
                self.assertIsNotNone(sample['actual_position'])
                self.assertIsNotNone(sample['actual_velocity'])
                self.assertEqual(sample['fault_code'],0)
                self.assertEqual(sample['fault_code_hex'],'0x0000')
                self.assertEqual(sample['fault_description'],'无错误码')
        print('SIMPLE delayed SDO full f action: fixed start/target; write ACK/raw snapshots identify actual0106..012F..002F/003F/002F phases')

    def test_one_confirm_enabled_edge_executes_motion_once_with_frozen_target(self):
        self.node=EnabledEdgeNode()
        self.node.require_enabled_target=True
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        self.ctl=SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()
        review=self.ctl.review_action('forward',self.out)
        self.assertIn('使能后重写冻结目标并触发002F→003F→002F','\n'.join(review['lines']))
        self.node.set_value(0x6064,review['start']+3)
        self.ctl.execute_action(review,self.out)
        self.assertEqual(self.node.enabled_edges,[(0x2f,0x3f,review['target'])])
        self.assertEqual(self.node.enabled_target_writes,[review['target']])
        for _ in range(30):
            self.ctl.refresh(self.out)
            if self.node.value(0x6064,signed=True)==review['target']:break
        self.assertEqual(self.node.value(0x6064,signed=True),review['target'])
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),
                         (review['start'],review['target']))
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
        self.assertEqual(len(self.bus.sent),count)
        print('SIMPLE one confirmation: only Operation enabled new edge starts trajectory; original target reached; consumed review never replays')

    def test_locked_UI_marks_values_historical_and_first_bad_raw(self):
        self.node=DelayedManualNode()
        self.node.transition_sequences[0x106]=[0x237,0x231]
        self.bus=DelayedBus([self.node])
        tool._std_open_active.return_value=self.bus
        self.ctl=SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ready()
        with self.assertRaises(ValueError):self.action('forward')
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.rows,app.cols=24,80
        app.current_view='main'
        app.poll()
        text='\n'.join(app.build().to_lines())
        self.assertIn('当前实读状态未知',text)
        self.assertIn('最后有效采样',text)
        self.assertIn('6041=0x0237',text)
        self.assertIn('Space/x 快停',text)
        self.assertNotIn('实际速度 0 rpm  故障 0x0000',text)
        print('SIMPLE locked80x24: last-valid telemetry explicitly historical; current state unknown; firstCW0106/raw0237 remains visible')

    def test_live_and_historical_telemetry_show_alarm_description(self):
        self.ready()
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.rows,app.cols=24,80
        app.current_view='main'
        self.assertIn('0x0000（无错误码）', '\n'.join(app.build().to_lines()))
        row=self.ctl.row(1)
        row['values']['error_code_object']=0x2312
        self.ctl._put(row)
        self.assertIn('0x2312（电机堵转）', '\n'.join(app.build().to_lines()))
        row.update(lost=True,quarantined=True)
        self.ctl._put(row)
        text='\n'.join(app.build().to_lines())
        self.assertIn('最后有效采样',text)
        self.assertIn('0x2312（电机堵转）',text)

    def test_initial_partial_603F_unknown_CW_builds_80x24_and_persists(self):
        import json
        from pathlib import Path
        self.node.set_value(0x603f,0x2312)
        self.ctl.rt_ack=True
        self.ctl.scan(self.out)
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.rows,app.cols=24,80
        app.current_view='main'
        app.poll()
        text='\n'.join(app.build().to_lines())
        self.assertIn('当前实读状态未知',text)
        self.assertIn('非完整快照',text)
        self.assertIn('603F=0x2312（电机堵转）',text)
        self.assertIn('CW=未知',text)
        self.assertIn('未执行',text)
        self.assertIsNone(self.ctl.row(1)['failure_diagnostic']['controlword'])
        records=[json.loads(line) for line in Path(self.ctl.diagnostic_paths[1]).read_text().splitlines()]
        self.assertEqual(records[1]['first_failure']['fault_code'],0x2312)
        self.assertEqual(records[1]['first_failure']['fault_description'],'电机堵转')
        self.assertFalse(requests(self.bus,True))

    def test_background_partial_603F_UI_keeps_last_valid_values_historical(self):
        self.ready()
        self.action('forward')
        previous=copy.deepcopy(self.ctl.row(1)['values'])
        self.node.set_value(0x603f,0x6789)
        self.ctl.refresh(self.out)
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.rows,app.cols=24,80
        app.current_view='main'
        app.poll()
        text='\n'.join(app.build().to_lines())
        self.assertIn('最后有效采样',text)
        self.assertIn('历史软件坐标',text)
        self.assertIn('603F=0x6789（未知报警）',text)
        self.assertIn('CW=未知',text)
        self.assertEqual(self.ctl.row(1)['values'],previous)
        self.assertNotIn('实读：故障',text)

    def test_6041_fault_UI_does_not_claim_unread_603F(self):
        self.ready()
        self.node.set_value(0x6041,0x2f)
        self.node.set_value(0x603f,0xbeef)
        self.ctl.refresh(self.out)
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.rows,app.cols=24,80
        app.current_view='main'
        text='\n'.join(app.build().to_lines())
        self.assertIn('603F=未知',text)
        self.assertIn('6041=0x002F',text)
        self.assertNotIn('BEEF',text)

    def test_known_ready_and_switched_on_prepare_internally_external_enable_refused(self):
        self.ready()
        self.node.set_value(0x6040,6)
        self.node.set_value(0x6041,0x231)
        self.ctl.refresh(self.out)
        review=self.ctl.review_action('forward',self.out)
        self.ctl.execute_action(review,self.out)
        self.assertEqual(self.controls(),[0,0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),(review['start'],review['target']))
        self.ctl.safety_action('disable',self.out)
        self.bus.sent.clear()
        self.node.set_value(0x6040,7)
        self.node.set_value(0x6041,0x233)
        self.ctl.refresh(self.out)
        self.action('hold')
        self.assertEqual(self.controls()[:2],[0,0x106])
        self.ctl.safety_action('disable',self.out)
        self.node.set_value(0x6040,0x2f)
        self.node.set_value(0x6041,0x237)
        self.ctl.refresh(self.out)
        before=len(self.bus.sent)
        with self.assertRaisesRegex(ValueError,'外部使能'):
            self.ctl.review_action('forward',self.out)
        self.assertEqual(len(self.bus.sent),before)
        print('SIMPLE Ready0231/SwitchedOn0233: confirmed internal disable0 then sealed submission; externalEnabled0237 rejected')

    def test_hold_submits_frozen_current_not_residual_target_never_releases(self):
        self.ready()
        old=self.node.value(0x6062,signed=True)
        review=self.action('hold')
        self.assertNotEqual(old,review['target'])
        self.assertEqual(review['start'],review['target'])
        self.assertEqual(self.node.value(0x6062,signed=True),review['target'])
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f])
        self.assertEqual(self.ctl.proofs[1]['phase'],'enabled')
        self.ctl.refresh(self.out)
        print('SIMPLE e: frozen current607A/new ACK/6062 then12F Halt, never2F; +13U inside original range accepted')

    def test_stop_then_reverse_internal_disable_without_rebase(self):
        self.ready()
        self.action('forward')
        self.node.set_value(0x606c,1)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError,'非零'):
            self.ctl.review_action('backward',self.out)
        self.node.set_value(0x606c,0)
        self.ctl.refresh(self.out)
        self.ctl.safety_action('stop',self.out)
        self.assertEqual(self.ctl.proofs[1]['phase'],'stopped')
        review=self.ctl.review_action('backward',self.out)
        target,start=review['target'],review['start']
        self.ctl.execute_action(review,self.out)
        self.assertEqual(self.ctl.origin,-1470)
        self.assertEqual((self.ctl.proofs[1]['start'],self.ctl.proofs[1]['target']),(start,target))
        self.assertEqual(self.controls(),[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f,2,0,0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f])

    def test_zero_speed_next_direction_and_settings_draft_need_no_manual_disable(self):
        self.ready()
        self.action('forward')
        old_proof=copy.deepcopy({k:v for k,v in self.ctl.proofs[1].items() if k!='session'})
        self.ctl.set_value('speed','1.001')
        self.ctl.set_value('step','0.2')
        self.assertEqual(self.ctl.manual_velocity,100)
        self.assertEqual({k:v for k,v in self.ctl.proofs[1].items() if k!='session'},old_proof)
        self.ctl.refresh(self.out)
        before=len(requests(self.bus,True))
        review=self.ctl.review_action('backward',self.out)
        self.assertEqual(review['args'].velocity,2186)
        self.ctl.execute_action(review,self.out)
        writes=requests(self.bus,True)[before:]
        self.assertEqual(writes[:2],[(0x6040,0,2),(0x6040,0,0)])
        self.assertEqual(writes[2],(0x6081,0,2186))
        self.assertEqual(self.ctl.proofs[1]['start'],review['start'])
        self.assertEqual(self.ctl.proofs[1]['target'],review['target'])
        self.assertEqual(self.ctl.proofs[1]['velocity'],2186)
        self.assertEqual(self.ctl.origin,-1470)
        print('SIMPLE continuous f/b: zero speed permits next direction; draft s/a keep old proof; confirmed2/0 precede new6081/607A')

    def test_rpm_step_quantization_and_under_one_unit_rejection(self):
        self.ready()
        self.ctl.set_value('speed','1.001')
        self.ctl.set_value('step','0.1001')
        step,degree,rpm=self.ctl.quantities(self.ctl.row(1))
        self.assertEqual(step,36)
        self.assertNotEqual(str(rpm),'1.001')
        review=self.ctl.review_action('forward',self.out)
        self.assertIn('1.001',' '.join(review['lines']))
        for kind in ('speed','step','range'):
            with self.assertRaises(ValueError):
                self.ctl.set_value(kind,'0.00000001')
        self.ctl.cancel_review()
        self.assertFalse(requests(self.bus,True))

    def test_range_keeps_authorized_origin_and_device_intersection(self):
        self.ready()
        self.node.set_value(0x6064,-1460)
        self.ctl.refresh(self.out)
        self.ctl.set_value('range','0.5')
        self.assertEqual(self.ctl.origin,-1470)
        self.assertEqual((self.ctl.settings['soft_min'],self.ctl.settings['soft_max']),(-1652,-1288))
        self.node.set_value(0x6064,-1288)
        self.ctl.refresh(self.out)
        with self.assertRaisesRegex(ValueError,'行程'):
            self.ctl.review_action('forward',self.out)
        self.ctl.review_action('backward',self.out)

    def test_modified_and_cancelled_review_never_write(self):
        self.ready()
        review=self.ctl.review_action('forward',self.out)
        review['target']+=1
        with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
        review=self.ctl.review_action('forward',self.out)
        self.ctl.cancel_review()
        with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
        self.assertFalse(requests(self.bus,True))

    def test_calibration_or_parameter_change_prevents_enable(self):
        for index,sub,value in ((0x608f,1,131073),(0x6081,0,101),(0x607d,1,-1600)):
            with self.subTest(index=index):
                self.setUp()
                self.ready()
                review=self.ctl.review_action('forward',self.out)
                self.node.set_value(index,value,sub)
                with self.assertRaises(ValueError):self.ctl.execute_action(review,self.out)
                self.assertNotIn(0x12f,self.controls())
                self.assertNotIn(0x2f,self.controls())

    def test_ack_failure_stops_before_enable(self):
        self.ready()
        self.node.ignore_setpoint=True
        with self.assertRaises(ValueError):self.action('forward')
        self.assertNotIn(0x12f,self.controls())
        self.assertNotIn(0x2f,self.controls())

    def test_communication_quarantine_all_subsequent_operations_zero_requests(self):
        self.ready()
        review=self.ctl.review_action('forward',self.out)
        self.node.drop_reads.add((0x6041,0))
        with self.assertRaises(BaseException):self.ctl.execute_action(review,self.out)
        count=len(self.bus.sent)
        for operation in (lambda:self.ctl.safety_action('stop',self.out),lambda:self.ctl.review_action('hold',self.out),lambda:self.ctl.refresh(self.out)):
            try:operation()
            except ValueError:pass
        self.ctl.close()
        self.assertEqual(len(self.bus.sent),count)

    def test_ui_cancel_and_stop_from_edit_confirm_help_and_busy(self):
        self.ready()
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='main'
        for view in ('edit','confirm','help'):
            review=self.ctl.review_action('forward',self.out)
            app.pending=review
            app.current_view=view
            app.edit_kind='speed'
            app.draft='1'
            app.dispatch_key('x',ord('x'))
            self.assertIsNone(app.pending)
            self.assertIsNone(self.ctl._simple_review)
            self.assertEqual(app.draft,'')
            self.assertEqual(app.urgent,'stop')
            app.dispatch_key('enter',10)
            self.assertFalse(requests(self.bus,True))
            app.urgent=None
        app.worker=mock.Mock()
        app.current_view='confirm'
        app.dispatch_key('space',32)
        self.assertTrue(app.cancel.is_set())
        self.assertTrue(app.refresh_cancel.is_set())
        app.worker=None
        app.current_view='main'
        app.dispatch_key('s',ord('s'))
        app.dispatch_key('1',49)
        app.dispatch_key('esc',27)
        self.assertEqual(app.current_view,'main')
        self.assertFalse(requests(self.bus,True))

    def test_busy_stop_cancels_actual_worker_no_queued_motion(self):
        self.ready()
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='main'
        review=self.ctl.review_action('forward',self.out)
        self.bus.delay_key=(0x6081,0)
        app._launch('execute',lambda out:self.ctl.execute_action(review,out))
        self.assertTrue(self.bus.entered.wait(1))
        app.dispatch_key('x',ord('x'))
        self.assertTrue(app.cancel.is_set())
        self.bus.delay_key=None
        self.bus.release.set()
        end=time.monotonic()+2
        while time.monotonic()<end:
            app.poll()
            if app.worker is None and app.urgent is None and self.controls():break
            time.sleep(.005)
        self.assertEqual(self.controls(),[2])
        self.assertIsNone(app.pending)
        self.assertIsNone(self.ctl._simple_review)
        self.assertNotIn(0x12f,self.controls())
        self.assertNotIn(0x2f,self.controls())
        if app.refresh_worker:app.refresh_worker.join(1)
        print('SIMPLE busy x: real delayed SDO worker cancelled; pending cleared; one quickstop2, no target/enable/release replay')

    def test_stop_interrupts_background_snapshot_before_remaining_uploads(self):
        self.ready()
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='help'
        self.bus.delay_key=(0x6041,0)
        app._start_refresh()
        self.assertTrue(self.bus.entered.wait(1))
        app.dispatch_key('space',32)
        self.assertTrue(app.refresh_cancel.is_set())
        self.bus.delay_key=None
        self.bus.release.set()
        end=time.monotonic()+2
        while time.monotonic()<end:
            app.poll()
            if app.worker is None and app.urgent is None and self.controls():break
            time.sleep(.005)
        requests_before_stop=requests(self.bus)
        stop_index=next(i for i,r in enumerate(requests_before_stop) if r==(0x6040,0,2))
        self.assertEqual([r[:2] for r in requests_before_stop[:stop_index]],[(0x6041,0),(0x2002,1)],
            'cancelled refresh must stop after the blocked status upload, before remaining snapshot reads')
        self.assertEqual(self.controls(),[2])
        if app.refresh_worker:app.refresh_worker.join(1)
        print('SIMPLE refresh Space: control/status uploads only then cancellation; no remaining snapshot reads before quickstop2')


    def test_reset_key_waits_for_inflight_sdo_without_cancelling_refresh(self):
        from _test_bench_support import mockbus
        self.ready()
        for key,value,size in (((0x1001,0),0,1),((0x200e,2),0x03002312,4)):
            self.node.od[key]=mockbus.OdEntry(value.to_bytes(size,'little'))
        self.node.set_value(0x6081,100)
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='main'
        self.bus.delay_key=(0x6041,0)
        app._start_refresh()
        self.assertTrue(self.bus.entered.wait(1))
        self.node.set_value(0x6041,0x4c0)
        self.node.set_value(0x603f,0x2312)
        app.dispatch_key('t',ord('t'))
        self.assertFalse(app.refresh_cancel.is_set())
        self.assertIsNotNone(app.worker)
        self.bus.delay_key=None
        self.bus.release.set()
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            app.poll()
            if app.worker is None and app.refresh_worker is None:break
            time.sleep(.005)
        self.assertEqual(app.current_view,'confirm')
        self.assertIsNotNone(app.pending)
        self.assertEqual(app.pending['kind'],'reset')
        self.assertFalse(requests(self.bus,True))
        app.dispatch_key('esc',27)
        self.assertIsNone(self.ctl._reset_review)



class ResetNode(ManualNode):
    """Host-only alarm model: clearing needs a held reset, not its ACK."""
    def __init__(self):
        super().__init__()
        from _test_bench_support import mockbus
        for key, value, size in (((0x1001,0),0,1), ((0x200e,2),0x03002312,4)):
            self.od[key] = mockbus.OdEntry(value.to_bytes(size,'little'), writable=False)
        self.set_value(0x6081,100)
        self.set_value(0x6041,0x4c0)
        self.set_value(0x603f,0x2312)
        self.high_at = None
        self.downloads = []
        self.fail_download = None
        self.behavior = 'clear'
        self.alarm_recovery = True

    def handle(self, frame, now):
        from _test_bench_support import mockbus
        data = frame.data
        if not self.alarm_recovery:
            return super().handle(frame,now)
        if data[:4] == bytes.fromhex('2b406000'):
            word = int.from_bytes(data[4:6],'little')
            self.downloads.append((word,time.monotonic()))
            # Raw standard SDO response; no controller methods are stubbed.
            mockbus.MockNode.handle(self,frame,now)
            if word == 2:
                self.set_value(0x6041,0x487)
            if word == 0 and self.high_at is None:
                self.set_value(0x6041,0x4c0)
            if word == 0x80:
                self.high_at = time.monotonic()
            if word == 0 and self.high_at is not None:
                if self.behavior == 'relatch':
                    self.set_value(0x6041,0x4c0)
                    self.set_value(0x603f,0x2312)
                if self.behavior == 'register':self.set_value(0x1001,1)
                if self.behavior == 'cw':self.set_value(0x6040,0x80)
                if self.behavior == 'offset':self.set_value(0x607c,1)
                if self.behavior == 'static':self.set_value(0x6083,101)
            if self.fail_download == len(self.downloads):self._outbox.clear()
            return
        if (data[:4] == bytes.fromhex('40416000') and self.high_at is not None
                and time.monotonic()-self.high_at >= .8 and self.behavior != 'unchanged'
                and (self.value(0x6040)==0x80 or self.behavior not in ('relatch',))):
            self.set_value(0x6041,0x440)
            if self.behavior != 'retained':self.set_value(0x603f,0)
        super().handle(frame,now)


class ResetTests(BenchFixture):
    def setUp(self):
        super().setUp()
        from _test_position import Clock
        self.clock = Clock()
        self.stack.enter_context(mock.patch('pmp4010_manual.time.monotonic',self.clock.monotonic))
        self.stack.enter_context(mock.patch('pmp4010_manual.time.sleep',self.clock.sleep))
        self.node = ResetNode()
        self.bus = DelayedBus([self.node])
        tool._std_open_active.return_value = self.bus
        self.ctl = SimpleController(tool,self.args)
        self.addCleanup(self.ctl.close)
        self.ctl.rt_ack = True
        self.ctl.scan(self.out)
        self.bus.sent.clear()
        self.assertEqual(self.ctl.action_locks[1]['kind'],'alarm_2312')

    def review(self):
        return self.ctl.review_reset(self.out)

    def test_confirmation_cancel_and_quarantine_never_download(self):
        r=self.review()
        self.ctl.cancel_review()
        with self.assertRaises(ValueError):self.ctl.execute_reset(r,self.out)
        self.assertFalse(requests(self.bus,True))
        session=self.ctl.manual_sessions[1]
        session.client.quarantined=True
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.review()
        self.assertEqual(len(self.bus.sent),count)
        self.assertEqual(tool._std_open_active.call_count,1)

    def test_delayed_clear_three_writes_ack_waits_complete_log_and_no_motion_authorization(self):
        import json
        from pathlib import Path
        review=self.review()
        original=self.ctl.manual_sessions[1]
        self.assertTrue(self.ctl.execute_reset(review,self.out))
        self.assertIs(self.ctl.manual_sessions[1],original)
        self.assertEqual([w for w,t in self.node.downloads],[0,0x80,0])
        times=[t for w,t in self.node.downloads]
        self.assertGreaterEqual(times[1]-times[0],.1)
        self.assertGreaterEqual(times[2]-times[1],1.)
        self.assertGreaterEqual(self.clock.now-times[2],.5)
        self.assertIsNone(self.ctl.origin)
        self.assertIsNone(self.ctl.od_reason)
        self.assertIsNone(self.ctl.software_zero)
        self.assertFalse(self.ctl.blocked)
        self.assertFalse(self.ctl.proofs)
        self.assertFalse(self.ctl.row(1)['quarantined'])
        self.assertEqual(tool._std_open_active.call_count,1)
        files=list(Path(self.tmp.name).glob('*reset-*.jsonl'))
        self.assertEqual(len(files),1)
        rows=[json.loads(line) for line in files[0].read_text().splitlines()]
        controls=[r for r in rows if r['event']=='sdo_attempt' and r['direction']=='download']
        self.assertEqual([r['raw'] for r in controls],['0000','8000','0000'])
        self.assertEqual([r['values']['error_code_object'] for r in rows if r['event']=='health'],[0x2312,0,0,0])
        self.assertEqual(rows[-1]['event'],'reset_feedback_verified')
        with self.assertRaisesRegex(ValueError,'运动授权'):self.ctl.review_action('forward',self.out)
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.ctl.execute_reset(review,self.out)
        self.assertEqual(len(self.bus.sent),count)

    def test_retained_warning_code_relatch_register_controlword_and_snapshot_fail_closed(self):
        for behavior in ('unchanged','retained','relatch','register','cw','offset','static'):
            with self.subTest(behavior=behavior):
                self.setUp()
                self.node.behavior=behavior
                review=self.review()
                with self.assertRaises(ValueError):self.ctl.execute_reset(review,self.out)
                self.assertTrue(self.ctl.blocked)
                self.assertTrue(self.ctl.row(1)['quarantined'])
                self.assertLessEqual(len(self.node.downloads),3)
                count=len(self.bus.sent)
                with self.assertRaises(ValueError):self.review()
                self.ctl.refresh(self.out)
                self.ctl.close(self.out)
                self.assertEqual(len(self.bus.sent),count)

    def test_each_lost_control_ack_quarantines_without_further_requests_or_cleanup(self):
        for stage in (1,2,3):
            with self.subTest(stage=stage):
                self.setUp()
                r=self.review()
                self.node.fail_download=stage
                with self.assertRaises(Exception):self.ctl.execute_reset(r,self.out)
                self.assertEqual(len(self.node.downloads),stage)
                self.assertTrue(self.ctl.manual_sessions[1].client.quarantined)
                count=len(self.bus.sent)
                with self.assertRaises(ValueError):self.review()
                self.ctl.refresh(self.out)
                self.ctl.close(self.out)
                self.assertEqual(len(self.bus.sent),count)
                from pathlib import Path
                import json
                failure=json.loads(next(Path(self.tmp.name).glob('*reset-*.jsonl')).read_text().splitlines()[-1])
                self.assertEqual(failure['bit7_may_remain_set'],stage>=2)

    def test_unrelated_locks_and_protected_preconditions_never_write(self):
        for change in ('lock','reaction','register','offset','mode','moving','following'):
            with self.subTest(change=change):
                self.setUp()
                if change=='lock':self.ctl.action_locks[1]['kind']='other'
                elif change=='reaction':self.node.set_value(0x200e,0,2)
                elif change=='register':self.node.set_value(0x1001,1)
                elif change=='offset':self.node.set_value(0x607c,1)
                elif change=='mode':self.node.set_value(0x6061,3)
                elif change=='moving':self.node.set_value(0x606c,1)
                elif change=='following':self.node.set_value(0x6041,0x24c0)
                with self.assertRaises(ValueError):self.review()
                self.assertTrue(self.ctl.blocked)
                self.assertFalse(requests(self.bus,True))

    def test_log_open_failure_writes_nothing_and_does_not_publish_unlock(self):
        r=self.review()
        with mock.patch('pmp4010_position.Journal',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.ctl.execute_reset(r,self.out)
        self.assertTrue(self.ctl.blocked)
        self.assertFalse(requests(self.bus,True))

    def test_log_close_failure_after_feedback_cannot_publish_unlock(self):
        import pmp4010_position as position
        close=position.Journal.close
        def fail(journal):
            close(journal)
            if journal.source=='manual_alarm_reset':raise OSError('close failed')
        r=self.review()
        with mock.patch.object(position.Journal,'close',fail):
            with self.assertRaises(OSError):self.ctl.execute_reset(r,self.out)
        self.assertEqual([w for w,t in self.node.downloads],[0,0x80,0])
        self.assertTrue(self.ctl.blocked)
        self.assertTrue(self.ctl.row(1)['quarantined'])

    def test_reset_confirmation_persists_on_action_locked_row_and_escape_cancels(self):
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.action='reset'
        app.pending=self.review()
        app.current_view='confirm'
        app.poll()
        self.assertEqual(app.current_view,'confirm')
        self.assertIsNotNone(app.pending)
        self.assertIn('Enter', '\n'.join(app.pending['lines']))
        app.dispatch_key('esc',27)
        self.assertIsNone(app.pending)
        self.assertIsNone(self.ctl._reset_review)
        self.assertFalse(requests(self.bus,True))

    def test_every_execute_sdo_lost_reply_malformed_or_cancel_stops_at_that_request(self):
        # Enumerate the real successful wire sequence, then mutate every SDO
        # boundary independently. No controller/session method is mocked.
        r=self.review()
        self.bus.sent.clear()
        self.ctl.execute_reset(r,self.out)
        count=len(self.bus.sent)
        from _test_bench_support import mockbus
        for fault in ('lost','malformed','cancel'):
            for number in range(1,count+1):
                with self.subTest(fault=fault,request=number):
                    case=ResetTests()
                    case.setUp()
                    try:
                        r=case.review()
                        case.bus.sent.clear()
                        send=case.bus.send
                        def inject(frame):
                            send(frame)
                            if len(case.bus.sent)==number:
                                if fault=='cancel':case.out.cancel_event.set()
                                else:
                                    case.node._outbox.clear()
                                    if fault=='malformed':
                                        case.node._outbox.append(mockbus.Frame(0x581,b'\x00'+frame.data[1:4]+bytes(4)))
                        case.bus.send=inject
                        with self.assertRaises(BaseException):case.ctl.execute_reset(r,case.out)
                        self.assertEqual(len(case.bus.sent),number)
                        self.assertTrue(case.ctl.manual_sessions[1].client.quarantined)
                        self.assertTrue(case.ctl.blocked)
                        before=len(case.bus.sent)
                        case.out.cancel_event.clear()
                        with self.assertRaises(ValueError):case.review()
                        case.ctl.refresh(case.out)
                        case.ctl.close(case.out)
                        self.assertEqual(len(case.bus.sent),before)
                        self.assertEqual(tool._std_open_active.call_count,1)
                    finally:
                        case.doCleanups()
        print('RESET mutation matrix: {} SDO boundaries x lost/malformed/cancel; no next request/new client/cleanup'.format(count))

    def test_known_feedback_and_log_failures_preserve_transport_health_but_lock_action(self):
        for cause in ('unchanged','retained','log'):
            with self.subTest(cause=cause):
                self.setUp()
                r=self.review()
                if cause=='log':
                    with mock.patch('pmp4010_position.Journal',side_effect=OSError('disk full')):
                        with self.assertRaises(OSError):self.ctl.execute_reset(r,self.out)
                else:
                    self.node.behavior=cause
                    with self.assertRaises(ValueError):self.ctl.execute_reset(r,self.out)
                self.assertFalse(self.ctl.manual_sessions[1].client.quarantined)
                self.assertEqual(self.ctl.action_locks[1]['kind'],'reset_failed')
                before=len(self.bus.sent)
                self.ctl.close(self.out)
                self.assertEqual(len(self.bus.sent),before)

    def test_later_non_alarm_or_diagnostic_log_failure_upgrades_alarm_lock(self):
        for cause in ('other','log'):
            with self.subTest(cause=cause):
                self.setUp()
                session=self.ctl.manual_sessions[1]
                if cause=='other':
                    self.ctl._cleanup_failure(1,session,ValueError('protected parameter changed'),self.out)
                else:
                    self.ctl.diagnostic_paths.pop(1)
                    with mock.patch('pmp4010_position.Journal',side_effect=OSError('disk full')):
                        self.ctl._persist_failure(1,self.ctl.failure_diagnostics[1],self.ctl.cleanup_diagnostics[1],self.out)
                self.assertNotEqual(self.ctl.action_locks[1]['kind'],'alarm_2312')
                count=len(self.bus.sent)
                with self.assertRaises(ValueError):self.review()
                self.assertEqual(len(self.bus.sent),count)

    def test_reset_success_preserves_coordinates_settings_and_next_failure_has_new_log(self):
        from pathlib import Path
        self.node.set_value(0x603f,0)
        self.node.set_value(0x6041,0x440)
        # First authorize on a fresh ordinary snapshot, then recreate a
        # real alarm while retaining the chosen 4 mm/turn coordinates.
        self.ctl.blocked.clear()
        self.ctl.action_locks.clear()
        self.ctl.refresh(self.out)
        self.ctl.linear=True
        self.ctl.mm_per_motor_revolution=Fraction(4)
        self.ctl.authorize()
        self.ctl.software_zero=-1000
        before=(self.ctl.origin,self.ctl.software_zero,copy.deepcopy(self.ctl.settings),self.ctl.mm_per_motor_revolution)
        self.node.set_value(0x603f,0x2312)
        self.node.set_value(0x6041,0x4c0)
        self.ctl.refresh(self.out)
        old_logs=set(Path(self.tmp.name).glob('*diagnostics-*.jsonl'))
        r=self.review()
        self.ctl.execute_reset(r,self.out)
        self.assertEqual((self.ctl.origin,self.ctl.software_zero,self.ctl.settings,self.ctl.mm_per_motor_revolution),before)
        self.assertFalse(self.ctl.proofs)
        self.assertIsNone(self.ctl._simple_review)
        self.node.high_at=None
        self.node.set_value(0x603f,0x3456)
        self.ctl.refresh(self.out)
        new_logs=set(Path(self.tmp.name).glob('*diagnostics-*.jsonl'))
        self.assertTrue(old_logs < new_logs)
        self.assertEqual(len(new_logs-old_logs),1)
        self.assertEqual(self.ctl.failure_diagnostics[1]['fault_code'],0x3456)

    def test_owned_enabled_alarm_discards_old_go_review_before_reset_and_keeps_settings(self):
        self.node.alarm_recovery=False
        self.node.set_value(0x603f,0)
        self.node.set_value(0x6041,0x440)
        self.ctl.blocked.clear()
        self.ctl.action_locks.clear()
        self.ctl.refresh(self.out)
        self.ctl.authorize()
        r=self.ctl.review_action('hold',self.out)
        self.ctl.execute_action(r,self.out)
        old=self.ctl.review('go',1,None,self.out)
        self.assertIn(1,self.ctl.proofs)
        self.node.alarm_recovery=True
        self.node.set_value(0x603f,0x2312)
        self.node.set_value(0x6041,0x4a7)
        self.ctl.refresh(self.out)
        self.assertEqual(self.ctl.action_locks[1]['kind'],'alarm_2312')
        self.assertTrue(self.ctl._cleanup_witnesses[1]['disabled_confirmed'])
        self.node.downloads.clear()
        reset=self.review()
        self.ctl.execute_reset(reset,self.out)
        self.assertEqual([w for w,t in self.node.downloads],[0,0x80,0])
        self.assertFalse(self.ctl.proofs)
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.ctl.execute(old,self.out)
        self.assertEqual(len(self.bus.sent),count)

    def test_t_enter_executes_once_and_80x24_confirmation_shows_scoped_approval(self):
        app=SimpleApp(tool,self.args)
        app.controller=self.ctl
        app.current_view='main'
        app.rows,app.cols=24,80
        app.dispatch_key('t',ord('t'))
        app.worker.join(1)
        app.poll()
        self.assertEqual(app.current_view,'confirm')
        grid=app.build()
        # Grid rendering is exercised by the production UI; fit must retain
        # every separate OD key and the reset-only Enter/Esc authorization.
        screen='\n'.join(grid.to_lines())
        self.assertIn('Enter 仅确认本次复位',screen)
        self.assertIn('保护锁定',screen)
        self.assertIn('非厂家最短脉宽',screen)
        self.assertIn('Esc 取消',screen)
        for entry in self.ctl.od['entries']:
            if entry['status']!='verified':self.assertIn(entry['key'],screen)
        app.dispatch_key('enter',13)
        app.worker.join(1)
        app.poll()
        self.assertEqual([w for w,t in self.node.downloads],[0,0x80,0])
        self.assertIsNone(self.ctl.origin)
        self.assertEqual(app.current_view,'authorize')
        if app.refresh_worker:app.refresh_worker.join(1)

    def test_reset_review_parameter_failure_cannot_become_recoverable_by_restoring_value(self):
        self.node.set_value(0x607c,1)
        with self.assertRaises(ValueError):self.review()
        self.assertEqual(self.ctl.action_locks[1]['kind'],'other')
        self.node.set_value(0x607c,0)
        count=len(self.bus.sent)
        with self.assertRaises(ValueError):self.review()
        self.assertEqual(len(self.bus.sent),count)
        self.assertFalse(requests(self.bus,True))
