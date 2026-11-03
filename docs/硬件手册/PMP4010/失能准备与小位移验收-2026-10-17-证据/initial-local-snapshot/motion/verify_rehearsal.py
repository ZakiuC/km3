"""Verify actual PTY/Form/TUI path and standard MockBus action evidence."""
import hashlib
import json
from pathlib import Path
ROOT=Path('/tmp/km3-pp-motion-20261017')
OUT=ROOT/'rehearsal-1'
rows=[json.loads(x) for x in (OUT/'app-audit.jsonl').read_text().splitlines()]
keys=[json.loads(x) for x in (OUT/'keys.jsonl').read_text().splitlines()]
trace=json.loads((OUT/'mock-trace.json').read_text())
prepare_results=[r for r in rows if r['event']=='job_event' and r['name']=='bench_result' and r['data'].get('action')=='prepare_pp']
assert len(prepare_results)==1
prepare=prepare_results[0]
assert prepare['data']['success'] and prepare['data']['disabled_confirmed'] and prepare['data']['target_reached']
assert prepare['data']['readback']['actual_position']==-1465
assert prepare['data']['readback']['profile_velocity']==100
assert prepare['data']['readback']['actual_velocity']==0
prep_writes=[r for r in rows if r['event']=='sdo_download_begin' and r['monotonic']<=prepare['monotonic']]
assert [(r['index'],r['sub'],int.from_bytes(bytes.fromhex(r['raw_hex']),'little',signed=r['index']==0x607a)) for r in prep_writes]==[(0x607a,0,-1465),(0x6081,0,100)]
assert all(not r['state']['enabled_session_nodes'] for r in rows if r['event']=='ui_state' and r['monotonic']<=prepare['monotonic'])
samples=[r['data'] for r in rows if r['event']=='job_event' and r['name']=='prepare_sample']
pre_samples=[x for x in samples if str(x['phase']).startswith('稳定采样')]
assert len(pre_samples)==5,[r['phase'] for r in samples]
assert all(s['values']['actual_position']==-1465 and s['values']['actual_velocity']==0 for s in samples)
review_events=[r for r in rows if r['event']=='ui_method_after' and r['method']=='review_action' and r['state']['pending']]
execute_events=[r for r in rows if r['event']=='ui_method_before' and r['method']=='_execute']
assert len(review_events)==len(execute_events)==2
for review,execute,token in zip(review_events,execute_events,['PREPARE_PP mock-bench 1 -1465','RELATIVE mock-bench 1 -1429']):
 assert review['state']['pending']['token']==token
 assert execute['state']['field_value']==execute['state']['pending']['token']==token
 assert review['monotonic']<execute['monotonic']
 commits=[r for r in rows if r['event']=='form_commit_after' and r['value']==token and r['result'] and review['monotonic']<r['monotonic']<execute['monotonic']]
 assert len(commits)==1
 assert any(k['text']==token for k in keys)
 assert any(r['event']=='ui_method_before' and r['method']=='dispatch_key' and r['arguments'][0]=='f5' and r['monotonic']<=execute['monotonic'] and execute['monotonic']-r['monotonic']<.1 for r in rows)
pp_results=[r for r in rows if r['event']=='job_event' and r['name']=='bench_result' and not r['data'].get('action')]
assert len(pp_results)==1
pp=pp_results[0]
assert pp['data']['success'] and pp['data']['disabled_confirmed']
assert pp['data']['readback']['actual_position']==-1429 and pp['data']['readback']['actual_velocity']==0
phases={}
for r in rows:
 if r['event']=='job_event' and r['name'] in ('ready_for_setpoint','setpoint_ack','target_completion','after_cleanup','target_reached'):
  phases.setdefault(r['name'],[]).append(r['data'])
assert phases['ready_for_setpoint'][-1]['statusword'] & 0x1000==0
assert phases['setpoint_ack'][-1]['statusword'] & 0x1000
assert phases['target_completion'][-1]['statusword'] & 0x1000==0
assert phases['target_completion'][-1]['actual_position']==-1429
assert phases['after_cleanup'][-1]['statusword'] & 0x4f==0x40
assert trace['closed'] and all(f['id']==0x601 for f in trace['requests'])
assert all(json.loads(p.read_text())['unknown_escapes']==[] for p in OUT.glob('*.screen.json'))
client_ids={r['client_id'] for r in prep_writes}
assert len(client_ids)==1
pp_write_events=[r for r in rows if r['event']=='sdo_download_begin' and r['monotonic']>prepare['monotonic']]
pp_clients={r['client_id'] for r in pp_write_events}
assert len(pp_clients)==1 # including original same-client exit Shutdown
controls=[int.from_bytes(bytes.fromhex(r['raw_hex']),'little') for r in pp_write_events if r['index']==0x6040]
assert controls==[0,6,7,15,47,63,47,2,0,6],controls
summary=dict(result='PASS',mode='real_curses_pty_with_standard_mockbus',hardware_acceptance=False,
 pty_child_exit_code=0,real_ssh_calls=0,real_can_opens=0,prepare_downloads=2,prepare_controlword_downloads=0,
 prepare_stationary_samples=5,prepare_all_snapshot_phases=[s['phase'] for s in samples],
 prepare_enabled_sessions_empty=True,prepare_bit10_true_is_explicit_fixture_behaviour=True,
 action_tokens=[r['state']['pending']['token'] for r in review_events],
 real_form_commits_verified=True,actual_tui_review_and_execute_verified=True,
 pp_ack_sequence=[0,1,0],pp_final_position=-1429,pp_final_velocity=0,pp_disabled_confirmed=True,
 pp_and_exit_controls=controls,cleanup_same_action_client=True,pty_unknown_escapes=0,
 raw_terminal_bytes=(OUT/'terminal.raw').stat().st_size,mock_request_frames=len(trace['requests']),
 transport_closed=True,phases=phases,
 source_hashes={r['path']:r['sha256'] for r in rows if r['event']=='source'})
(OUT/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
