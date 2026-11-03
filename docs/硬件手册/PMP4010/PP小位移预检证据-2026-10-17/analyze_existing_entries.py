"""Offline replay only: no configuration, socket, execute or downloads."""
exec(compile(open('/tmp/km3-pp-preflight-20261017/replay_review.py').read(), 'replay_review.py', 'exec'))
row=controller.row(1)
assert row['values']['statusword'] & tool._canopen.CIA402_HOMING_TARGET_REACHED == 0
review=controller.review('enable',1,None,pre.Output())
assert review['token']=='ENABLE can1 1 -1465'
assert review['writes']==[dict(key='controlword',index=0x6040,sub=0,type='uint16',value=15,note='')]
pre.emit('enable_review_accepted_without_bit10',token=review['token'],writes=review['writes'],
         lines=review['lines'],actual_execute_calls=0,real_can_opens=0,real_can_sends=0,
         soft_limits_are_offline_replay_inputs=True,bit10_after_enable='not_measured_or_assumed')
controller.args.velocity=100
controller.args.acceleration=100
controller.args.deceleration=100
actual_args=controller._args(row,-1429)
assert [actual_args.velocity,actual_args.acceleration,actual_args.deceleration]==[500000,5000000,5000000]
pre.emit('bench_profile_source',cli_options=[100,100,100],resulting_profile=[actual_args.velocity,actual_args.acceleration,actual_args.deceleration],source='recorded_device_telemetry',real_can_opens=0,real_can_sends=0)
import pmp4010_params as params
specs=params.specifications(controller.od)
assert '6081:00' not in specs
pre.emit('existing_parameter_whitelist',objects=sorted(specs),contains_6081=False)
for action in ('interpolation','transmission'):
 try:
  params.plan(action,specs,period_us=4000,overrides=['6081:00=100'])
 except params.ParameterError as exc:
  assert str(exc)=='--set不得跨批次或写命令对象'
  pre.emit('parameter_override_expected_rejection',action=action,override='6081:00=100',message=str(exc),real_can_opens=0,real_can_sends=0)
 else:
  raise AssertionError('unexpected 6081 parameter write plan')
pre.emit('entry_analysis_complete',live_operations=0,execute_calls=0,existing_guard_preserved=True)
