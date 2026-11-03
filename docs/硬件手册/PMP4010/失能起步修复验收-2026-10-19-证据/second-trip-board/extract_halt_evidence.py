"""Derive timing and bounded observations from saved raw logs only; no SSH or CAN."""
from pathlib import Path
from collections import Counter
from fractions import Fraction
import hashlib,json,datetime
B=Path(__file__).resolve().parent
source=B/'after-q-final-app-audit.jsonl'
rows=[json.loads(s) for s in source.read_text().splitlines()]
indexed=list(enumerate(rows,1))
job=lambda name:next(r for r in rows if r['event']=='job_event' and r['name']==name)
begin=job('begin');result=job('result');bench=job('bench_result');exception=job('exception');cleanup=job('after_cleanup')
write=lambda r:r['event']=='sdo_download_begin'
value=lambda r:int.from_bytes(bytes.fromhex(r['raw_hex']),'little',signed=True)
halt=next(r for r in rows if write(r) and r['index']==0x6040 and value(r)==0x12F)
t0=halt['monotonic'];client=halt['client_id']
control=[dict(utc=r['utc'],monotonic=r['monotonic'],value=value(r),hex=f'0x{value(r):04X}',client_id=r['client_id']) for r in rows if r['event']=='sdo_download_done' and r['index']==0x6040]
targets=[dict(utc=r['utc'],value=value(r),client_id=r['client_id']) for r in rows if r['event']=='sdo_download_done' and r['index']==0x607A]
seq=[]
for line,r in indexed:
 if t0-.3<=r.get('monotonic',0)<=t0+.5 and r['event'] in ('sdo_upload_done','sdo_download_begin','sdo_download_done') and r['index'] in (0x6040,0x6041,0x6064,0x606C,0x6062,0x607A):
  seq.append(dict(source_line=line,utc=r['utc'],monotonic=r['monotonic'],offset_ms=(r['monotonic']-t0)*1000,event=r['event'],client_id=r['client_id'],index=f'{r["index"]:04X}',sub=r['sub'],raw_hex=r['raw_hex'],signed=value(r)))
(B/'halt-enable-upload-sequence.json').write_text(json.dumps(seq,ensure_ascii=False,indent=2)+'\n')
post=[(line,r) for line,r in indexed if r['event']=='sdo_upload_done' and r['client_id']==client and t0<=r['monotonic']<=exception['monotonic']]
firsts={f'{i:04X}':dict(source_line=line,utc=r['utc'],offset_ms=(r['monotonic']-t0)*1000,raw_hex=r['raw_hex'],signed=value(r)) for i in (0x6041,0x6064,0x606C,0x607A,0x6062) for line,r in [next((line,r) for line,r in post if r['index']==i)]}
positions=[r for r in rows if r['event']=='sdo_upload_done' and r['index']==0x6064 and begin['monotonic']<=r['monotonic']<=bench['monotonic']]
postpositions=[r for r in rows if r['event']=='sdo_upload_done' and r['index']==0x6064 and t0<=r['monotonic']]
lastposition=postpositions[-1]
stable={}
for prefix in ('起步稳定采样','Halt保持核证采样','Halt使能核证采样'):
 samples=[r for r in rows if r['event']=='job_event' and r['name']=='disabled_start_sample' and r['data'].get('phase','').startswith(prefix)]
 stable[prefix]=dict(count=len(samples),samples=[dict(utc=r['utc'],phase=r['data']['phase'],statusword=r['data']['values']['statusword'],actual_position=r['data']['values']['actual_position'],actual_velocity=r['data']['values']['actual_velocity'],position_demand=r['data']['values']['position_demand']) for r in samples])
ack=[]
for phase in ('current_ack_initial_zero','current_setpoint_ack','current_ack_return_zero'):
 r=next(r for r in rows if r['event']=='job_event' and r['name']=='disabled_start_sample' and r['data'].get('phase')==phase)
 sw=r['data']['values']['statusword'];ack.append(dict(phase=phase,utc=r['utc'],statusword=sw,statusword_hex=f'0x{sw:04X}',ack_bit12=bool(sw&0x1000),actual_position=r['data']['values']['actual_position'],position_demand=r['data']['values']['position_demand']))
clients=sorted({r['client_id'] for r in rows if r['event'].startswith('sdo_') and begin['monotonic']<=r['monotonic']<=bench['monotonic']})
counts=Counter(r['event'] for r in rows)
qline=next(r for r in rows if r['event']=='job_line' and '6040h=0x0006' in r.get('text',''))
postcheck=json.loads((B/'postcheck-summary.json').read_text())
summary=dict(result='HARDWARE_MOTION_ACCEPTANCE_FAILED_SAFE_GUARD_STOP',raw_source=source.name,raw_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),halt_enable_begin=dict(utc=halt['utc'],monotonic=t0,client_id=client),first_uploads_after_12F=firsts,first_trigger_displacement_users=firsts['6064']['signed']-(-1470),first_trigger_displacement_motor_degrees=str(Fraction(firsts['6064']['signed']+1470)*Fraction(45,16384)),first_trigger_not_cleanup_position=True,motion_observed_6064=dict(count=len(positions),min=min(map(value,positions)),max=max(map(value,positions))),post12F_through_q_observed_6064=dict(count=len(postpositions),min=min(map(value,postpositions)),max=max(map(value,postpositions)),last=value(lastposition),last_utc=lastposition['utc']),sampled_606C_in_motion=sorted({value(r) for r in rows if r['event']=='sdo_upload_done' and r['index']==0x606C and begin['monotonic']<=r['monotonic']<=bench['monotonic']}),ack_0_1_0=ack,stable_sample_groups=stable,stable_count_note='5 initial strictdisabled + 5 pre-enable Halt hold completed; post-12F enable check rejected first position upload, so no post-enable 5-sample proof.',controls=control,target_writes=targets,motion_controlwords=[r['hex'] for r in control[:-1]],q_controlword=control[-1]['hex'],elapsed_seconds=dict(begin_to_result=result['monotonic']-begin['monotonic'],begin_to_bench_result=bench['monotonic']-begin['monotonic'],halt_enable_to_exception=exception['monotonic']-t0,exception_to_first_quick_stop=next(r['monotonic'] for r in rows if write(r) and r['index']==0x6040 and value(r)==2)-exception['monotonic']),exception=exception,cleanup=cleanup,motion_result=result,bench_result=bench,q_readback_line=qline,board_app_exit=next(r for r in rows if r['event']=='board_app_exit'),motion_client_ids=clients,execute_call_count=sum(r['event']=='ui_method_before' and r['method']=='_execute' for r in rows),begin_count=sum(r['event']=='job_event' and r['name']=='begin' for r in rows),result_count=sum(r['event']=='job_event' and r['name']=='result' for r in rows),download_begin=counts['sdo_download_begin'],download_done=counts['sdo_download_done'],sdo_exceptions=[r for r in rows if r['event'] in ('sdo_upload_exception','sdo_download_exception')],quarantine_observed=any(r['event']=='ui_state' and r['state']['row_quarantined'] for r in rows),retry_or_new_action=False,retry_scope='Exactly one UI execute and one motion begin/result; every observer delegates the original SDO method once. This does not measure CAN frame-level retransmission.',CAN_closed_confirmed=any(r['event']=='job_line' and '总线已关闭' in r['text'] for r in rows),postcheck_passive_changed_keys=postcheck['passive_changed_keys'],postcheck_related_runners=postcheck['related_runners'],remaining_limits=['Observed encoder position discrepancy after Halt enable does not establish physical root cause or mechanical stop/brake behavior.','No final target -1434 was written; no Halt release or final trigger attempted; requested move did not complete.','Previous interrupted boot session completion remains unknown.'],cleanup_status='Original q completed rc0; no runner/CAN receiver remains; isolated b2 remote package and logs intentionally retained; no source/config/services/current changed.')
(B/'motion-evidence-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:summary[k] for k in ('result','first_uploads_after_12F','first_trigger_displacement_users','first_trigger_displacement_motor_degrees','motion_observed_6064','post12F_through_q_observed_6064','sampled_606C_in_motion','ack_0_1_0','stable_count_note','motion_controlwords','q_controlword','elapsed_seconds','motion_client_ids','execute_call_count','download_begin','download_done','quarantine_observed','CAN_closed_confirmed','postcheck_passive_changed_keys')},ensure_ascii=False,indent=2))
