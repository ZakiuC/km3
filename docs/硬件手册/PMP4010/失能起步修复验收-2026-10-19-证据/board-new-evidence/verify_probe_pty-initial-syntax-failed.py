import hashlib,json,pathlib
B=pathlib.Path(__file__).parent
manifest=json.loads((B/'pty-source-sha256.json').read_text())
for rel,item in manifest.items():assert hashlib.sha256((B/'pty-source'/rel).read_bytes()).hexdigest()==item['sha256']
allsummary=[]
for scenario in ('success','halt-refused','old-demand'):
 out=B/('pty-probe-'+scenario)
 rows=[json.loads(x) for x in (out/'app-audit.jsonl').read_text().splitlines()]
 keys=[json.loads(x) for x in (out/'keys.jsonl').read_text().splitlines()]
 trace=json.loads((out/'mock-trace.json').read_text())
 def events(name):return [x for x in rows if x['event']=='job_event' and x['name']==name]
 reviews=[x for x in rows if x['event']=='ui_method_after' and x['method']=='review_action' and x['state']['pending']]
 executes=[x for x in rows if x['event']=='ui_method_before' and x['method']=='_execute']
 assert len(reviews)==len(executes)==1
 review,execute=reviews[0],executes[0];token=review['state']['pending']['token']
 assert review['state']['pending']['action']=='probe_pp_start'
 assert review['state']['pending']['start']==review['state']['pending']['target']==-1470
 assert execute['state']['field_value']==execute['state']['pending']['token']==token
 assert token in (out/'probe-frozen-review.screen.txt').read_text()
 assert len([x for x in rows if x['event']=='form_commit_after' and x['value']==token and x['result'] and review['monotonic']<x['monotonic']<execute['monotonic']])==1
 assert any(x['text']==token for x in keys)
 assert any(x['event']=='ui_method_before' and x['method']=='dispatch_key' and x['arguments'][0]=='f5' and 0<=execute['monotonic']-x['monotonic']<.1 for x in rows)
 writes=[x for x in rows if x['event']=='sdo_download_begin']
 assert writes and all(x['monotonic']>execute['monotonic'] for x in writes)
 assert len({x['client_id'] for x in writes})==1
 assert not [x for x in rows if x['event'] in ('sdo_upload_exception','sdo_download_exception','forbidden_unhalt_enabled_command','old_trajectory_resume_hazard')]
 controls=[int.from_bytes(bytes.fromhex(x['raw_hex']),'little') for x in writes if x['index']==0x6040]
 expected=[0x106,2,0,6] if scenario=='halt-refused' else [0x106,0x107,0x10f,0x12f,0x13f,0x12f,2,0,6]
 assert controls==expected,(scenario,controls)
 assert all(w&0x100 for w in controls[:-3])
 assert not any(w&0xf==0xf and not w&0x100 for w in controls)
 # Quick stop (2), disable voltage (0), and exit shutdown (6) remove operation-enable;
 # their cleared bit8 is not an enabled PP trajectory resume.
 aligned=[x for x in rows if x['event']=='fixture_607A_only_write']
 assert len(aligned)==1 and aligned[0]['target']==-1470 and aligned[0]['internal_demand']==7800 and not aligned[0]['statusword']&0x400 and not aligned[0]['accepted']
 assert not [x for x in writes if x['index']==0x605d]
 assert [x['index'] for x in writes if x['index']!=0x6040]==[0x607a,0x6082,0x6081,0x6083,0x6084]
 fixtures=[x for x in rows if x['event']=='fixture_after_download']
 assert all(x['actual_position']==-1470 and x['actual_velocity']==0 for x in fixtures)
 trigger=[x for x in rows if x['event']=='fixture_new_command_ack']
 if scenario=='halt-refused':assert not trigger
 else:
  assert len(trigger)==1
  assert all(x['internal_demand']==7800 for x in fixtures if x['monotonic']<trigger[0]['monotonic'])
  assert trigger[0]['internal_demand']==(-1470 if scenario=='success' else 7800)
 samples=events('disabled_start_sample')
 first=[x for x in samples if x['data']['phase'].startswith('起步稳定采样')]
 held=[x for x in samples if x['data']['phase'].startswith('Halt保持核证采样')]
 assert len(first)==5 and len(held)==(5 if scenario=='success' else 0)
 for group in (first,held):
  assert all(group[i]['monotonic']-group[i-1]['monotonic']>=.19 for i in range(1,len(group)))
 result=events('result');bench=events('bench_result');assert len(result)==len(bench)==1
 assert result[0]['data']['completed']==bench[0]['data']['success']==(scenario=='success')
 assert result[0]['data']['disabled_confirmed'] and bench[0]['data']['disabled_confirmed']
 assert result[0]['data']['halt_released'] is False and result[0]['data']['travel_executed'] is False
 assert bench[0]['data']['readback']['actual_position']==-1470 and bench[0]['data']['readback']['actual_velocity']==0
 assert len(events('probe_observed'))==(1 if scenario=='success' else 0)
 if scenario!='success':
  assert len(events('exception'))==1
  phrase='Halt未确认' if scenario=='halt-refused' else '607A/6062未同时等于起点'
  assert phrase in events('exception')[0]['data']['message']
 app_exit=[x for x in rows if x['event']=='mock_app_exit'];assert len(app_exit)==1 and app_exit[0]['rc']==0 and app_exit[0]['closed']
 assert trace['closed'] and all(x['id']==0x601 for x in trace['requests'])
 screens=list(out.glob('*.screen.json'));assert screens
 assert all(json.loads(p.read_text())['unknown_escapes']==[] for p in screens)
 for x in [x for x in rows if x['event']=='source']:
  p=pathlib.Path(x['path']);assert p.is_relative_to(B/'pty-source')
  assert hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256']
 summary=dict(result='PASS',scenario=scenario,mode='real_curses_PTY_original_Form_review_execute',hardware_acceptance=False,real_SSH_calls=0,real_CAN_opens=0,child_rc=0,probe_success=scenario=='success',action_token=token,review_count=1,execute_count=1,controlwords_including_exit=controls,controlwords_hex=[f'{w:04X}' for w in controls],cleanup=[2,0],exit_additional_control=6,same_client_cleanup_and_exit=True,stationary_samples=len(first),Halt_held_samples=len(held),initial_actual=-1470,initial_internal_demand=7800,607A_alignment_does_not_update_internal_demand=True,607A_alignment_does_not_set_bit10=True,final_actual=-1470,final_velocity=0,disabled_confirmed=True,travel_executed=False,Halt_released=False,exception=events('exception')[0]['data'] if events('exception') else None,transport_closed=True,terminal_bytes=(out/'terminal.raw').stat().st_size,source_manifest='../pty-source-sha256.json')
 (out/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');allsummary.append(summary)
(B/'pty-final-summary.json').write_text(json.dumps(allsummary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'result':'PASS','scenarios':3,'child_rcs':[x['child_rc'] for x in allsummary],'probe_outcomes':[x['probe_success'] for x in allsummary]}))
