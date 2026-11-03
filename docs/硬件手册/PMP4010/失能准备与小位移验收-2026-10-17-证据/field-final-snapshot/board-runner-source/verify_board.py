"""Verify captured one-shot board session without any new device operation."""
from pathlib import Path
import json,hashlib
r=Path('/tmp/km3-pp-motion-20261017/board-session-1')
remote=r/'remote-final'
a=[json.loads(x) for x in (remote/'app-audit.jsonl').read_text().splitlines()]
journals=list(remote.glob('pmp4010-motion-*.jsonl'))
assert len(journals)==1
j=[json.loads(x) for x in journals[0].read_text().splitlines()]
assert a[-1]['event']=='board_app_exit' and a[-1]['rc']==0
assert json.loads((r/'exit.json').read_text())['child_rc']==0
assert all(json.loads(p.read_text())['unknown_escapes']==[] for p in r.glob('*.screen.json'))
results=[x for x in j if x['event']=='bench_result']
assert len(results)==1
result=results[0]
assert result['action']=='prepare_pp' and result['success'] and result['disabled_confirmed'] and result['target_reached'] is False and result['state_unknown'] is False
assert result['readback']['statusword']==0x260 and result['readback']['actual_position']==-1465 and result['readback']['actual_velocity']==0
assert result['readback']['target_position']==-1465 and result['readback']['profile_velocity']==100
assert result['readback']['profile_acceleration']==result['readback']['profile_deceleration']==5000000
samples=[x for x in j if x['event']=='prepare_sample']
stationary=[x for x in samples if x['phase'].startswith('稳定采样')]
assert len(stationary)==5
assert all(x['values']['actual_position']==-1465 and x['values']['actual_velocity']==0 and x['values']['statusword']==0x260 and x['calibration']['values']==[131072,1,1,1] for x in samples)
for pre,post in zip(stationary,stationary[1:]):
 from datetime import datetime
 assert (datetime.fromisoformat(post['timestamp_utc'])-datetime.fromisoformat(pre['timestamp_utc'])).total_seconds()>=.2
writes=[x for x in a if x['event']=='sdo_download_begin']
done=[x for x in a if x['event']=='sdo_download_done']
assert [(x['index'],x['sub'],x['raw_hex']) for x in writes]==[(0x607a,0,'47faffff'),(0x6081,0,'64000000')]
assert len(done)==2 and len({x['client_id'] for x in writes})==1
assert not any(x['event'].endswith('_exception') for x in a)
states=[x['state'] for x in a if x['event']=='ui_state']
assert all(not s['enabled_session_nodes'] and not s['blocked'] and not s['row_quarantined'] for s in states)
telemetry=[s for s in states if s['row_values']]
assert all(s['row_values']['actual_position']==-1465 and s['row_values']['actual_velocity']==0 and s['row_values']['statusword']==0x260 for s in telemetry)
review=[x for x in a if x['event']=='ui_method_after' and x['method']=='review_action']
assert [(x['state']['action'],bool(x['state']['pending'])) for x in review]==[('prepare_pp',True),('relative',False)]
execute=[x for x in a if x['event']=='ui_method_before' and x['method']=='_execute']
assert len(execute)==1
assert execute[0]['state']['field_value']==execute[0]['state']['pending']['token']=='PREPARE_PP can1 1 -1465'
assert any(x['event']=='form_commit_after' and x['result'] and x['value']=='PREPARE_PP can1 1 -1465' and review[0]['monotonic']<x['monotonic']<execute[0]['monotonic'] for x in a)
assert any(x['event']=='ui_method_before' and x['method']=='dispatch_key' and x['arguments'][0]=='f5' and 0<=execute[0]['monotonic']-x['monotonic']<.1 for x in a)
assert 'target reached 未置位' in (r/'pp-refused-bit10-zero.screen.txt').read_text()
q=next(x for x in reversed(a) if x['event']=='ui_method_before' and x['method']=='dispatch_key' and x['arguments'][0]=='q')
assert not [x for x in writes if x['monotonic']>q['monotonic']]
passive=json.loads((r.parent/'board-post-passive-comparison.json').read_text())
assert passive['result']=='PASS' and passive['file_count']==35 and all(passive['checks'].values())
summary=dict(result='PREPARE_PASSED_PP_GUARD_REFUSED_NO_MOTION',hardware_motion_executed=False,
 prepare_hardware_success=True,prepare_execute_count=1,prepare_form_token='PREPARE_PP can1 1 -1465',real_TUI_F5_freeze_Form_token_F5_verified=True,
 stationary_samples=stationary,all_prepare_phases=[s['phase'] for s in samples],readback=result['readback'],disabled_confirmed=True,target_reached=False,mechanical_stop_confirmed=False,
 total_SDO_downloads=2,completed_SDO_downloads=2,total_controlword_downloads=0,exit_additional_downloads=0,
 NMT_actions=0,NMT_sends=0,NMT_evidence_basis='fixed reviewed production PmpApp/BenchController SDO-only path; no raw CAN socket/sniffer added',
 prepare_write_client_ids=sorted({x['client_id'] for x in writes}),same_prepare_client_for_both_writes=True,
 SDO_exceptions=0,quarantine=False,blocked=[],enabled_sessions_ever=[],PP_review_count=1,PP_execute_count=0,
 PP_refusal='电机未确认静止：target reached 未置位',PP_requested_delta=36,PP_candidate_target=-1429,
 final_preexit_telemetry=telemetry[-1]['row_values'],postexit_SDO_read=False,postexit_CAN_open=False,
 child_rc=0,forced_kill=False,all_control_requests_node1=all(x.get('node',1)==1 for x in a if x['event'] in ('sdo_upload_begin','sdo_download_begin')),
 actual_upload_requests=sum(x['event']=='sdo_upload_begin' for x in a),source_hashes={x['path']:x['sha256'] for x in a if x['event']=='source'},
 prepost_identity_current_config_35_file_hashes_services_identical=True,post_RT_PID=passive['owner']['pid'],post_RT_FDs=passive['owner']['fds'],post_CAN_receive_lists=passive['owner']['CAN_receive_lists'],
 raw_files={str(p.relative_to(r)):dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in (r/'terminal.raw',r/'keys.jsonl',remote/'app-audit.jsonl',journals[0],r/'prepare-frozen.screen.txt',r/'pp-refused-bit10-zero.screen.txt',r/'final-disabled-detail.screen.txt')})
(r/'final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
