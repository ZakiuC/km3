import datetime,hashlib,json,pathlib,difflib
B=pathlib.Path(__file__).resolve().parent
results=[]
def rows(p):return [json.loads(s) for s in p.read_text().splitlines()]
def check(v,msg):
 if not v:raise AssertionError(msg)
for scenario in ('success','halt-drift','final-no-ack'):
 out=B/('pty-move-'+scenario+'-v2');audit=rows(out/'app-audit.jsonl');motion=rows(out/'motion.jsonl')
 cmd=json.loads((B/('pty-'+scenario+'-v2.command.json')).read_text());check(cmd['rc']==0,'driver rc')
 exit=[x for x in audit if x['event']=='mock_app_exit'][-1];check(exit['rc']==0 and exit['closed'],'normal closed exit')
 reviews=[json.loads((out/(x+'-frozen-review.json')).read_text()) for x in ('D-disable','R-move')]
 check(reviews[0]['token']=='DISABLE mock-bench 1 -1470','D token')
 check(reviews[1]['token']=='RELATIVE mock-bench 1 -1434','R token')
 check([w['value'] for w in reviews[1]['writes'] if w['key']=='target_position']==[-1470,-1434],'ordered target review')
 check(reviews[1]['start']==-1470 and reviews[1]['target']==-1434,'target budget')
 downloads=[x for x in audit if x['event']=='sdo_download_done'];clients={x['client_id'] for x in downloads[1:]};check(len(clients)==1,'single original move client excluding separate prior D action')
 controls=[int.from_bytes(bytes.fromhex(x['raw_hex']),'little') for x in downloads if x['index']==0x6040]
 expected=[0,0x106,0x107,0x127,0x137,0x127,0x12f]+([0x2f,0x3f,0x2f] if scenario=='success' else [0x2f,0x3f] if scenario=='final-no-ack' else [])+[2,0,6]
 check(controls==expected,'exact controls '+str(controls))
 writes=[x for x in audit if x['event']=='fixture_607A_only_write']
 check(len(writes)==(1 if scenario=='halt-drift' else 2),'607A count')
 check(writes[0]['target']==-1470 and writes[0]['internal_demand']==7800 and not writes[0]['statusword']&0x1400,'current607A cannot generate demand/ACK/bit10')
 if len(writes)==2:check(writes[1]['target']==-1434 and writes[1]['internal_demand']==-1470 and not writes[1]['statusword']&0x1400,'final607A cannot generate demand/ACK/bit10')
 current=[x for x in audit if x['event']=='fixture_current_new_ack'];final=[x for x in audit if x['event']=='fixture_final_new_ack']
 check(len(current)==1,'one fresh current ack');check(len(final)==(1 if scenario=='success' else 0),'independent final ack generation')
 check(not any('exception' in x['event'] for x in audit),'no SDO exceptions/retries')
 result=[x for x in motion if x['event']=='result'][-1]
 check(result['completed']==(scenario=='success') and result['completed_motion']==(scenario=='success'),'scenario outcome')
 check(result['disabled_confirmed'] and result['zero_velocity_confirmed'] and not result['mechanical_stop_confirmed'],'cleanup evidence separate from mechanical')
 starts=[x for x in motion if x['event']=='disabled_start_sample' and x['phase'].startswith('起步稳定采样')]
 check(len(starts)==5 and all(x['values']['actual_position']==-1470 and x['values']['actual_velocity']==0 for x in starts),'initial stable5')
 enabled=[x for x in motion if x['event']=='disabled_start_sample' and x['phase'].startswith('Halt使能零漂移采样')]
 if scenario!='halt-drift':check(len(enabled)==5 and all(x['values']['actual_position']==-1470 and x['values']['actual_velocity']==0 for x in enabled),'Halt stable5')
 after=[x for x in audit if x['event']=='fixture_after_download'];check(after[-1]['controlword']==6 and after[-1]['statusword']&0x6f==0x21,'q final Ready, not strict disabled')
 keys=rows(out/'keys.jsonl');typed=[x['text'] for x in keys]
 check(all(t in typed for t in ('D','OD mock-bench 1','DISABLE mock-bench 1 -1470','R','RELATIVE mock-bench 1 -1434')),'original keyboard approval path')
 check(any(x['label']=='replace active field' and x['text'].endswith('+36') for x in keys),'Form keyboard +36')
 results.append(dict(scenario=scenario,driver_rc=cmd['rc'],child_rc=exit['rc'],single_client=True,controls=[f'{x:04X}' for x in controls],target_writes=[x['target'] for x in writes],fresh_current_ack=len(current),fresh_final_ack=len(final),initial_samples=len(starts),halt_samples=len(enabled),result=result,post_q_statusword=f"0x{after[-1]['statusword']:04X}",CAN_closed=True))
manifest=json.loads((B/'pty-source-sha256.json').read_text())
for rel,meta in manifest.items():check(hashlib.sha256((B/'pty-source'/rel).read_bytes()).hexdigest()==meta['sha256'],'snapshot source hash')
summary=dict(result='PASS',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),transport='standard MockBus with real curses PTY',hardware_acceptance=False,actual_SSH_calls=0,actual_CAN_socket_opens=0,source_files_checked=len(manifest),scenarios=results,retained_initial_failure='pty-success.command.json rc1: default 0 + append +36 rejected as 0+36; normal UI q child rc0 and bus closed; v2 uses original Form Home/Delete keys')
(B/'pty-final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
for name,old in [('mock_app.py','mock_app.py'),('drive_move_pty.py','drive_probe_pty.py')]:
 prior=B.parent/'stage2-board-load-pty-snapshot'/old
 (B/(name+'.diff')).write_text(''.join(difflib.unified_diff(prior.read_text().splitlines(True),(B/name).read_text().splitlines(True),fromfile='stage2/'+old,tofile='post-power/'+name)))
print(json.dumps(summary,ensure_ascii=False,indent=2))
