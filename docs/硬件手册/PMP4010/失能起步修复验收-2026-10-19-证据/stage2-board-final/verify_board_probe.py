"""Read-only verifier for sealed stage2 real D/B/exit evidence; never opens CAN."""
from pathlib import Path
import hashlib,json
B=Path(__file__).parent
rows=[json.loads(x) for x in (B/'board-final-evidence/app-audit.jsonl').read_text().splitlines()]
journals=list((B/'board-final-evidence').glob('pmp4010-motion-*.jsonl'));assert len(journals)==1
jr=[json.loads(x) for x in journals[0].read_text().splitlines()]
def events(name):return [x for x in rows if x['event']=='job_event' and x['name']==name]
def number(x,signed=False):return int.from_bytes(bytes.fromhex(x['raw_hex']),'little',signed=signed)
writes=[x for x in rows if x['event']=='sdo_download_begin'];done=[x for x in rows if x['event']=='sdo_download_done']
assert len(writes)==len(done)==14
assert [(x['client_id'],x['index'],x['sub'],x['raw_hex']) for x in writes]==[(x['client_id'],x['index'],x['sub'],x['raw_hex']) for x in done]
assert all(x['node']==1 for x in writes)
cws=[x for x in writes if x['index']==0x6040];words=[number(x) for x in cws]
assert words==[0,0x106,0x107,0x127,0x137,0x127,2,0,6]
assert all(not word&8 for word in words)
assert all(word&0x100 and not word&8 for word in words[1:6])
assert len({x['client_id'] for x in writes[1:]})==1 and writes[0]['client_id']!=writes[1]['client_id']
parameters=[dict(index=f'{x["index"]:04X}',sub=x['sub'],value=number(x,x['index']==0x607a)) for x in writes if x['index']!=0x6040]
assert [(x['index'],x['value']) for x in parameters]==[('607A',-1470),('6082',0),('6081',100),('6083',5000000),('6084',5000000)]
assert not any(x['index'] in (0x605d,0x6060,0x607c) for x in writes)
assert not [x for x in rows if x['event'] in ('sdo_upload_exception','sdo_download_exception')]
assert not events('exception')
review=[x for x in rows if x['event']=='ui_method_after' and x['method']=='review_action' and x['state']['pending']]
execute=[x for x in rows if x['event']=='ui_method_before' and x['method']=='_execute']
assert len(review)==len(execute)==2
expected_tokens=['DISABLE can1 1 -1470','PROBE_PP_START can1 1 -1470']
assert [x['state']['pending']['token'] for x in review]==expected_tokens
assert [x['state']['field_value'] for x in execute]==expected_tokens
assert all(x['state']['pending']['token']==x['state']['field_value'] for x in execute)
assert writes[0]['monotonic']>execute[0]['monotonic'] and all(x['monotonic']>execute[1]['monotonic'] for x in writes[1:])
assert all(token in (B/('board-probe-session-1/'+name+'-frozen-before-token.screen.txt')).read_text() for token,name in zip(expected_tokens,['D','B']))
bench=events('bench_result');assert len(bench)==2
for x in bench:
 d=x['data'];assert d['success'] and d['disabled_confirmed'] and not d['state_unknown']
 assert d['readback']['statusword']==0x260 and d['readback']['actual_position']==-1470 and d['readback']['actual_velocity']==0
initial=[x for x in events('disabled_start_sample') if x['data']['phase'].startswith('起步稳定采样')]
held=[x for x in events('disabled_start_sample') if x['data']['phase'].startswith('Halt保持核证采样')]
for group in (initial,held):
 assert len(group)==5
 assert all(x['data']['values']['actual_position']==-1470 and x['data']['values']['actual_velocity']==0 for x in group)
 assert all(group[i]['monotonic']-group[i-1]['monotonic']>=.2 for i in range(1,5))
assert all(x['data']['values']['position_demand']==7800 for x in initial)
assert all(x['data']['values']['position_demand']==x['data']['values']['target_position']==-1470 and x['data']['values']['controlword']==0x127 for x in held)
assert len(events('probe_control_readback'))==5
assert [(x['data']['expected'],x['data']['actual']) for x in events('probe_control_readback')]==[(w,w) for w in words[1:6]]
reads=[x for x in rows if x['event']=='sdo_upload_done'];statuses=[x for x in reads if x['index']==0x6041]
# CIA402 QuickStop may carry bit2; classify the full state before claiming OperationEnabled.
assert all(number(x)&0x6f!=0x27 for x in statuses)
ack_windows=[]
for lo,hi,want_ack,want_demand in ((cws[3],cws[4],0,7800),(cws[4],cws[5],1,-1470),(cws[5],cws[6],0,-1470)):
 sw=[x for x in statuses if lo['monotonic']<x['monotonic']<hi['monotonic']]
 demand=[x for x in reads if x['index']==0x6062 and lo['monotonic']<x['monotonic']<hi['monotonic']]
 assert sw and demand and all(bool(number(x)&0x1000)==bool(want_ack) for x in sw)
 assert all(number(x,True)==want_demand for x in demand)
 ack_windows.append(dict(controlword=f'{number(lo):04X}',statuswords=sorted(set(f'{number(x):04X}' for x in sw)),ACK12=want_ack,position_demand=want_demand))
assert len(events('probe_observed'))==len(events('after_cleanup'))==len(events('result'))==1
observed=events('probe_observed')[0]['data'];result=events('result')[0]['data'];cleanup=events('after_cleanup')[0]['data']
assert observed['current_setpoint_acknowledged'] and observed['position_demand']==-1470 and observed['halt_held']
for k in ('completed','disabled_confirmed','position_unchanged','zero_velocity_confirmed','cleanup_confirmed','no_fault_following_error_confirmed'):assert result[k] is True
for k in ('mechanical_stop_confirmed','halt_released','operation_enable_requested','operation_enabled_observed','travel_executed','independent_estop_required'):assert result[k] is False
assert cleanup['statusword']&0x4f==0x40 and cleanup['actual_position']==-1470 and cleanup['actual_velocity']==0
positions=[number(x,True) for x in reads if x['index']==0x6064];velocities=[number(x,True) for x in reads if x['index']==0x606c]
assert positions and min(positions)==max(positions)==-1470 and velocities and set(velocities)=={0}
start=[x for x in jr if x['event']=='begin'];assert len(start)==1
assert start[0]['settings']==dict(soft_min=-1506,soft_max=-1434,max_displacement=36)
assert start[0]['reviewed_start']==-1470 and start[0]['planned_displacement']==0
# The actual transaction is bounded by the app's 15-second overall limit.
elapsed=events('result')[0]['monotonic']-execute[1]['monotonic'];assert elapsed<15
lastsw=statuses[-1];assert number(lastsw)==0x231 and not number(lastsw)&4
assert lastsw['client_id']==cws[-1]['client_id'] and lastsw['monotonic']>cws[-1]['monotonic']
assert any(x['event']=='job_line' and '总线已关闭' in x['text'] for x in rows)
assert rows[-1]['event']=='board_app_exit' and rows[-1]['rc']==0
exit=json.loads((B/'board-probe-session-1/exit.json').read_text());assert exit['child_rc']==0 and not exit['forced_kill']
before=json.loads((B/'before-real-tui.stdout').read_text());after=json.loads((B/'after-real-tui.stdout').read_text())
changes=[k for k in before if k!='utc' and before[k]!=after[k]];assert changes==[]
assert all('no entry' in x for x in after['can_rcvlists'].values())
source=[x for x in rows if x['event']=='board_session'];assert len(source)==1
assert source[0]['payload']=='/tmp/km3-pp-disabled-start-stage2-20261019-e0fhd4gp/payload' and source[0]['manifest_sha256']=='5b1c1e1aae87afa8f239bd8c5b8231b8c1b604063cbd4a76f3b27569d2fd18cc' and source[0]['move_timeout']==15
summary=dict(result='EVIDENCE_VERIFIED_REAL_STAGE2_PROBE_PASS',hardware_probe_success=True,hardware_travel_acceptance=False,application_exit_rc=0,review_count=2,execute_count=2,tokens=expected_tokens,reviewed_start=-1470,soft_limits=[-1506,-1434],max_displacement=36,planned_probe_travel=0,controlwords=[f'{w:04X}' for w in words],all_controlwords_bit3_clear=True,business_controlwords_Halt_held=True,operation_enabled_fallback=False,parameter_downloads=parameters,total_downloads=14,D_downloads=1,B_including_cleanup_downloads=12,exit_downloads=1,same_client_for_B_cleanup_exit=True,initial_stationary_samples=5,Halt_hold_samples=5,ACK_readback_windows=ack_windows,current_new_ACK_obtained=True,internal_demand_before=7800,internal_demand_after=-1470,observed_position_min=-1470,observed_position_max=-1470,all_observed_velocities_zero=True,observed_position_read_count=len(positions),observed_velocity_read_count=len(velocities),result_event=result,bench_result_events=[x['data'] for x in bench],probe_final_statusword='0260',probe_strict_disabled=True,probe_duration_seconds=elapsed,SDO_exceptions=0,retries=0,exit_last_statusword='0231',exit_operation_enabled=False,exit_strict_disabled=False,exit_status_source='last original same-client upload before CAN close',normal_q_exit=True,CAN_closed=True,postexit_CAN_open=False,passive_before_after_changed_keys=changes,mechanical_stop_confirmed=False,limitations=['This proves SwitchedOn current-setpoint preload/ACK under Halt, not Halt OperationEnabled stability or real travel acceptance.','All sampled position and speed values were unchanged/zero; sampling cannot establish unobserved mechanical movement or brake state.','No independent raw CAN sniffer was opened; SDO evidence is from original standard-client observer calls.','Ready SW0231 after original exit-only Shutdown6 differs from strictdisabled SW0260 at probe completion.'],cleanup='Original D then B executed once each; original q exited0 and closed CAN; no retry, additional probe or movement. New temporary files and all stage1/stage2 evidence retained; current/config/services unchanged.')
(B/'board-final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(evidence_verification='PASS',real_stage2_probe='PASS',controls=summary['controlwords'],ACK_windows=ack_windows,probe_status='0260',q_exit_status='0231',CAN_closed=True,passive_changes=changes,probe_duration_seconds=elapsed),ensure_ascii=False))
