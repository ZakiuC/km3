from pathlib import Path
import hashlib,json
B=Path(__file__).parent
manifest=json.loads((B/'pty-source-sha256.json').read_text())
for rel,item in manifest.items():assert hashlib.sha256((B/'pty-source'/rel).read_bytes()).hexdigest()==item['sha256']
summaries=[]
for scenario in ('success','no-ack','old-demand'):
 out=B/('pty-probe-'+scenario);rows=[json.loads(x) for x in (out/'app-audit.jsonl').read_text().splitlines()]
 keys=[json.loads(x) for x in (out/'keys.jsonl').read_text().splitlines()];trace=json.loads((out/'mock-trace.json').read_text())
 def ev(name):return [x for x in rows if x['event']=='job_event' and x['name']==name]
 reviews=[x for x in rows if x['event']=='ui_method_after' and x['method']=='review_action' and x['state']['pending']]
 executes=[x for x in rows if x['event']=='ui_method_before' and x['method']=='_execute'];assert len(reviews)==len(executes)==2
 tokens=[]
 for review,execute,action in zip(reviews,executes,('disable','probe_pp_start')):
  token=review['state']['pending']['token'];tokens.append(token)
  assert review['state']['pending']['action']==action
  assert review['state']['pending']['start']==review['state']['pending']['target']==-1470
  assert execute['state']['field_value']==execute['state']['pending']['token']==token
  assert len([x for x in rows if x['event']=='form_commit_after' and x['value']==token and x['result'] and review['monotonic']<x['monotonic']<execute['monotonic']])==1
  assert any(x['text']==token for x in keys)
  assert any(x['event']=='ui_method_before' and x['method']=='dispatch_key' and x['arguments'][0]=='f5' and 0<=execute['monotonic']-x['monotonic']<.1 for x in rows)
 assert tokens==['DISABLE mock-bench 1 -1470','PROBE_PP_START mock-bench 1 -1470']
 assert tokens[0] in (out/'D-disable-frozen-before-token.screen.txt').read_text()
 assert tokens[1] in (out/'B-probe-frozen-before-token.screen.txt').read_text()
 initial=[x['state'] for x in rows if x['event']=='ui_state' and x['state']['row_values']][0]
 assert initial['row_values']['statusword']==0x231 and initial['row_values']['controlword']==6
 assert initial['row_values']['actual_position']==-1470 and initial['row_values']['target_position']==-1469
 assert initial['row_values']['position_demand']==7800 and initial['row_values']['profile_velocity']==100
 writes=[x for x in rows if x['event']=='sdo_download_begin']
 beforeB=[x for x in writes if x['monotonic']<executes[1]['monotonic']]
 assert len(beforeB)==1 and beforeB[0]['index']==0x6040 and beforeB[0]['raw_hex']=='0000' and beforeB[0]['monotonic']>executes[0]['monotonic']
 probeW=[x for x in writes if x['monotonic']>executes[1]['monotonic']]
 assert len({x['client_id'] for x in probeW})==1
 control=[int.from_bytes(bytes.fromhex(x['raw_hex']),'little') for x in writes if x['index']==0x6040]
 business=[0x106,0x107,0x127,0x137]+([] if scenario=='no-ack' else [0x127])
 assert control==[0]+business+[2,0,6],control
 assert all(not w&8 and w&0x100 for w in business)
 assert not any(w&8 for w in control)
 assert all(w not in (0x10f,0x12f,0x13f) for w in control)
 assert not [x for x in rows if x['event'] in ('forbidden_operation_enable','sdo_upload_exception','sdo_download_exception')]
 assert [x['index'] for x in probeW if x['index']!=0x6040]==[0x607a,0x6082,0x6081,0x6083,0x6084]
 aligned=[x for x in rows if x['event']=='fixture_607A_only_write'];assert len(aligned)==1
 assert aligned[0]['target']==-1470 and aligned[0]['internal_demand']==7800 and not aligned[0]['statusword']&0x400
 fixtures=[x for x in rows if x['event']=='fixture_after_download'];assert all(x['actual_position']==-1470 and x['actual_velocity']==0 for x in fixtures)
 assert not any(x['statusword']&0x6f==0x27 for x in fixtures)
 # Quick-stop active mask7 legitimately has bit2=1; it is not state Operation enabled (mask27).
 assert all(x['statusword']&0x6f in (0x21,0x23) for x in fixtures if x['controlword'] in business)
 assert fixtures[-1]['statusword']==0x231 and fixtures[-1]['controlword']==6
 newack=[x for x in rows if x['event']=='fixture_new_command_ack']
 assert len(newack)==(0 if scenario=='no-ack' else 1)
 if newack:
  assert all(x['internal_demand']==7800 for x in fixtures if x['monotonic']<newack[0]['monotonic'])
  assert newack[0]['internal_demand']==(-1470 if scenario=='success' else 7800)
 samples=ev('disabled_start_sample');stable=[x for x in samples if x['data']['phase'].startswith('起步稳定采样')];held=[x for x in samples if x['data']['phase'].startswith('Halt保持核证采样')]
 assert len(stable)==5 and len(held)==(5 if scenario=='success' else 0)
 for group in (stable,held):assert all(group[i]['monotonic']-group[i-1]['monotonic']>=.19 for i in range(1,len(group)))
 result=ev('result');bench=ev('bench_result');assert len(result)==1 and len(bench)==2
 assert bench[0]['data']['success'] and bench[0]['data']['disabled_confirmed'] and bench[0]['data']['readback']['statusword']==0x240
 assert result[0]['data']['completed']==bench[1]['data']['success']==(scenario=='success')
 for data in (result[0]['data'],bench[1]['data']):
  assert data['disabled_confirmed'] and data['position_unchanged'] and data['zero_velocity_confirmed']
  assert not data['operation_enable_requested'] and not data['operation_enabled_observed']
 assert not result[0]['data']['halt_released'] and not result[0]['data']['travel_executed']
 assert bench[1]['data']['readback']['statusword']==0x240 and bench[1]['data']['readback']['actual_position']==-1470
 assert len(ev('probe_observed'))==(1 if scenario=='success' else 0)
 if scenario!='success':
  phrase='新ACK握手超时' if scenario=='no-ack' else '607A/6062未同时等于起点'
  assert len(ev('exception'))==1 and phrase in ev('exception')[0]['data']['message']
 app_exit=[x for x in rows if x['event']=='mock_app_exit'];assert len(app_exit)==1 and app_exit[0]['rc']==0 and app_exit[0]['closed']
 assert trace['closed'] and all(x['id']==0x601 for x in trace['requests'])
 assert all(json.loads(p.read_text())['unknown_escapes']==[] for p in out.glob('*.screen.json'))
 summary=dict(result='PASS',stage='stage2_no_operation_enable',scenario=scenario,driver_rc=json.loads((B/('pty-'+scenario+'.command.json')).read_text())['rc'],child_rc=0,real_SSH_calls=0,real_CAN_opens=0,initial_statusword='0231',initial_CW=6,initial_actual=-1470,initial_target=-1469,initial_internal_demand=7800,initial_profile_velocity=100,tokens=tokens,review_count=2,execute_count=2,original_Form_verified=True,controlwords=[f'{x:04X}' for x in control],business_bit3_always0=True,business_Halt_always1=True,operation_enabled_fallback=False,probe_success=scenario=='success',independent_result_fields=result[0]['data'],initial_stationary_samples=5,Halt_hold_samples=len(held),alignment_only_fixture=True,probe_final_status='0240',exit_final_status='0231',exit_operation_enabled=False,transport_closed=True,mechanical_or_hardware_acceptance=False)
 (out/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');summaries.append(summary)
(B/'pty-final-summary.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'result':'PASS','scenarios':3,'probe_outcomes':[x['probe_success'] for x in summaries],'controlword_bit3_always0':True,'reviews_per_scenario':2}))
