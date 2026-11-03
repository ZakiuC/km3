from pathlib import Path
from fractions import Fraction
import hashlib,json
B=Path(__file__).parent;out=B/'board-final-evidence'
rows=[json.loads(x) for x in (out/'app-audit.jsonl').read_text().splitlines()]
journals=list(out.glob('pmp4010-motion-*.jsonl'));assert len(journals)==1
jr=[json.loads(x) for x in journals[0].read_text().splitlines()]
def ev(name):return [x for x in rows if x['event']=='job_event' and x['name']==name]
writes=[x for x in rows if x['event']=='sdo_download_begin'];done=[x for x in rows if x['event']=='sdo_download_done']
assert len(writes)==len(done)==11
assert [(x['index'],x['sub'],x['raw_hex']) for x in writes]==[(x['index'],x['sub'],x['raw_hex']) for x in done]
assert len({x['client_id'] for x in writes})==1 and all(x['node']==1 for x in writes)
controls=[int.from_bytes(bytes.fromhex(x['raw_hex']),'little') for x in writes if x['index']==0x6040]
assert controls==[0x106,0x107,0x10f,2,0,6]
parameters=[dict(index=f'{x["index"]:04X}',sub=x['sub'],value=int.from_bytes(bytes.fromhex(x['raw_hex']),'little',signed=x['index']==0x607a)) for x in writes if x['index']!=0x6040]
assert [(x['index'],x['value']) for x in parameters]==[('607A',-1469),('6082',0),('6081',100),('6083',5000000),('6084',5000000)]
assert not any(x['index'] in (0x605d,0x6060,0x607c) for x in writes)
assert not [x for x in rows if x['event'] in ('sdo_upload_exception','sdo_download_exception')]
assert len(ev('exception'))==1 and '位置漂移' in ev('exception')[0]['data']['message']
assert not ev('probe_observed')
assert len(ev('result'))==len(ev('bench_result'))==1
assert ev('result')[0]['data']['completed'] is False
assert ev('result')[0]['data']['disabled_confirmed'] is False
assert ev('bench_result')[0]['data']['success'] is False and ev('bench_result')[0]['data']['readback'] is None
assert ev('bench_result')[0]['data']['disabled_confirmed'] is True and ev('bench_result')[0]['data']['state_unknown'] is True
initial=[x for x in ev('disabled_start_sample') if x['data']['phase'].startswith('起步稳定采样')]
assert len(initial)==5
assert all(x['data']['values']['actual_position']==-1469 and x['data']['values']['actual_velocity']==0 for x in initial)
assert all(initial[i]['monotonic']-initial[i-1]['monotonic']>=.2 for i in range(1,5))
assert not [x for x in ev('disabled_start_sample') if x['data']['phase'].startswith('Halt保持核证采样')]
reads=[x for x in rows if x['event']=='sdo_upload_done']
positions=[dict(utc=x['utc'],monotonic=x['monotonic'],value=int.from_bytes(bytes.fromhex(x['raw_hex']),'little',signed=True)) for x in reads if x['index']==0x6064]
lo=min(p['value'] for p in positions);hi=max(p['value'] for p in positions)
assert hi==-1457
last_10f=[x for x in writes if x['index']==0x6040 and x['raw_hex']=='0f01'][0]
first_drift=next(p for p in positions if p['monotonic']>last_10f['monotonic'] and p['value']!=-1469)
assert first_drift['value']==-1457
poscleanup=next(p for p in positions if p['monotonic']>ev('exception')[0]['monotonic']);assert poscleanup['value']==-1466
lastsw=next(x for x in reversed(reads) if x['index']==0x6041)
lastword=int.from_bytes(bytes.fromhex(lastsw['raw_hex']),'little');assert lastword==0x231 and not lastword&4
assert any(x['event']=='job_line' and '已确认 Switch on disabled' in x['text'] for x in rows)
assert any(x['event']=='job_line' and '总线已关闭' in x['text'] for x in rows)
app_exit=[x for x in rows if x['event']=='board_app_exit'];assert len(app_exit)==1 and app_exit[0]['rc']==0
exit=json.loads((B/'board-probe-session-1/exit.json').read_text());assert exit['child_rc']==0 and not exit['forced_kill']
review=[x for x in rows if x['event']=='ui_method_after' and x['method']=='review_action' and x['state']['pending']];execute=[x for x in rows if x['event']=='ui_method_before' and x['method']=='_execute']
assert len(review)==len(execute)==1
assert execute[0]['state']['field_value']==execute[0]['state']['pending']['token']=='PROBE_PP_START can1 1 -1469'
assert all(x['monotonic']>execute[0]['monotonic'] for x in writes)
before=json.loads((B/'before-real-tui.stdout').read_text());after=json.loads((B/'after-real-tui.stdout').read_text())
changes=[k for k in before if k!='utc' and before[k]!=after[k]];assert changes==[]
summary=dict(result='EVIDENCE_VERIFIED_REAL_PROBE_FAILED_POSITION_DRIFT',hardware_probe_success=False,hardware_travel_acceptance=False,application_exit_rc=0,review_count=1,execute_count=1,token='PROBE_PP_START can1 1 -1469',reviewed_start=-1469,soft_limits=[-1505,-1433],max_displacement=36,planned_probe_travel=0,controlwords=[f'{w:04X}' for w in controls],parameter_downloads=parameters,total_downloads=11,probe_downloads=10,exit_downloads=1,same_client_for_action_cleanup_exit=True,Halt_control_readbacks=ev('probe_control_readback'),first_drift=first_drift,observed_position_min=lo,observed_position_max=hi,first_drift_delta=12,first_drift_motor_degrees=str(Fraction(12*360,131072)),cleanup_position_at_joint_check=-1466,cleanup_protocol_switch_on_disabled_observed=True,joint_start_zero_speed_disabled_check_passed=False,result_event=ev('result')[0]['data'],bench_result_event=ev('bench_result')[0]['data'],current_setpoint_trigger_sent=False,current_new_ACK_obtained=False,internal_demand_after_failure=7800,SDO_exceptions=0,quarantined=False,retries=0,exit_last_statusword=f'{lastword:04X}',exit_operation_enabled=False,exit_strict_disabled=False,exit_status_source='last original same-client upload before CAN close',normal_q_exit=True,CAN_closed=True,postexit_CAN_open=False,passive_before_after_changed_keys=changes,mechanical_stop_confirmed=False,limitations=['Position readings establish observed drift, not its cause or unobserved peak physical travel.','Implementation travel_executed=false denotes no PP travel trigger; observed +12U drift still occurred during Halt enable.','No independent raw CAN sniffer was opened; SDO audit is from original standard client calls.'],cleanup='Live runner/SSH/UI exited0 normally; no retries, second probe or motion; all stage/evidence retained; current/config/services unchanged.')
(B/'board-final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'evidence_verification':'PASS','real_probe':'FAILED_POSITION_DRIFT','controls':summary['controlwords'],'first_drift':first_drift,'q_exit_status':summary['exit_last_statusword'],'CAN_closed':True,'passive_changes':changes},ensure_ascii=False))
