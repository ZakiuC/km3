"""Verify unchanged PP guard when prepare leaves bit10 zero; no live transport."""
import hashlib,json
from pathlib import Path
r=Path('/tmp/km3-pp-motion-20261017/final-pty-bit10-zero')
a=[json.loads(s) for s in (r/'app-audit.jsonl').read_text().splitlines()]
k=[json.loads(s) for s in (r/'keys.jsonl').read_text().splitlines()]
t=json.loads((r/'mock-trace.json').read_text())
expected={'pmp4010_bench.py':'fbe69ec63e58fb30f49866f48f93d01e14c15a8b129866c9fba6f292fa7c974f','pmp4010_tui.py':'6ef77e2edbbfea5f579fa5db0996472a9932c9a1ddc3bd0118abd1a5ab7654b8'}
for n,h in expected.items():
 assert any(Path(x['path']).name==n and x['sha256']==h for x in a if x['event']=='source')
results=[x for x in a if x['event']=='job_event' and x['name']=='bench_result']
assert len(results)==1
p=results[0]['data']
assert p['action']=='prepare_pp' and p['success'] and p['disabled_confirmed'] and p['target_reached'] is False
assert p['readback']['statusword']==0x260 and p['readback']['actual_position']==-1465
assert p['readback']['actual_velocity']==0 and p['readback']['profile_velocity']==100
writes=[x for x in a if x['event']=='sdo_download_begin']
assert [(x['index'],x['sub'],int.from_bytes(bytes.fromhex(x['raw_hex']),'little',signed=x['index']==0x607a)) for x in writes]==[(0x607a,0,-1465),(0x6081,0,100)]
assert len({x['client_id'] for x in writes})==1
assert not any(x['index']==0x6040 for x in writes)
assert not any(x['state']['enabled_session_nodes'] for x in a if x['event']=='ui_state')
samples=[x['data'] for x in a if x['event']=='job_event' and x['name']=='prepare_sample']
assert len([s for s in samples if s['phase'].startswith('稳定采样')])==5
assert all(s['values']['actual_position']==-1465 and s['values']['actual_velocity']==0 for s in samples)
reviews=[x for x in a if x['event']=='ui_method_after' and x['method']=='review_action']
assert [(x['state']['action'],bool(x['state']['pending'])) for x in reviews]==[('prepare_pp',True),('relative',False)]
execute=[x for x in a if x['event']=='ui_method_before' and x['method']=='_execute']
assert len(execute)==1
assert execute[0]['state']['field_value']==execute[0]['state']['pending']['token']=='PREPARE_PP mock-bench 1 -1465'
assert any(x['event']=='form_commit_after' and x['result'] and x['value']=='PREPARE_PP mock-bench 1 -1465' and reviews[0]['monotonic']<x['monotonic']<execute[0]['monotonic'] for x in a)
assert 'target reached 未置位' in (r/'pp-refused.screen.txt').read_text()
assert not any(x['event']=='synthetic_bit10_after_target_alignment' for x in a)
assert t['closed'] and all(f['id']==0x601 for f in t['requests'])
assert all(json.loads(p.read_text())['unknown_escapes']==[] for p in r.glob('*.screen.json'))
summary=dict(result='PASS',mode='real_curses_pty_with_standard_mockbus',hardware_acceptance=False,child_rc=0,prepare_success=True,prepare_stationary_samples=5,target_reached_after_prepare=False,prepare_downloads=2,all_controlword_downloads=0,all_NMT_frames=0,PP_review_rejected='target reached 未置位',PP_execute_calls=0,all_enabled_sessions_empty=True,original_UI_Form_token_verified=True,transport_closed=True,raw_terminal_bytes=(r/'terminal.raw').stat().st_size,mock_request_frames=len(t['requests']),source_hashes=expected)
(r/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
