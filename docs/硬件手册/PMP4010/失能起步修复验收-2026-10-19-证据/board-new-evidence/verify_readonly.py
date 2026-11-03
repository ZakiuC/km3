import hashlib,json,pathlib
b=pathlib.Path(__file__).parent
rows=[json.loads(x) for x in (b/'diagnostic-live.stdout').read_text().splitlines()]
def one(event):
 rr=[r for r in rows if r['event']==event];assert len(rr)==1;return rr[0]
plan=one('plan');tx=[r for r in rows if r['event']=='tx'];rx=[r for r in rows if r['event']=='rx']
assert len(tx)==len(rx)==38
expected=plan['table']+plan['samples']*3
assert len(expected)==38
for r,(i,s,t) in zip(tx,expected):
 d=bytes.fromhex(r['raw_hex'])
 assert r['cob_id']=='601' and d==bytes([0x40,i&255,i>>8,s,0,0,0,0])
for r in rx:assert r['cob_id']=='581' and not r['extended'] and not r['remote']
assert one('complete')['uploads']==38 and one('complete')['writes']==one('complete')['nmt']==one('complete')['retries']==0 and not one('complete')['quarantined']
assert one('closed')['is_open'] is False and one('closed')['tx_count']==one('closed')['rx_count']==38
assert one('passive_comparison')['changed_keys']==[]
before=one('passive_before')['snapshot'];after=one('passive_after')['snapshot']
assert [k for k in before if k!='utc' and before[k]!=after[k]]==[]
stage=json.loads((b/'stage-frozen.stdout').read_text())
assert stage['safe_members']==21 and stage['manifest_members_verified']==20 and not stage['payload_executed']
prestage=json.loads((b/'before-stage.stdout').read_text())
stagechanges=[k for k in before if k!='utc' and prestage[k]!=before[k]]
assert sorted(stagechanges)==['payload_exists','payload_root','source_hashes']
values=one('snapshot')['values'];samples=[r for r in rows if r['event']=='sample']
assert len(samples)==3 and all(r['values']=={k:values[k] for k in r['values']} for r in samples)
rcs={label:json.loads((b/(label+'.command.json')).read_text())['rc'] for label in ('before-stage','stage-frozen','baseline-38','transfer-diagnostic','diagnostic-offline','diagnostic-live')}
assert all(v==0 for v in rcs.values())
summary=dict(result='PASS_READ_ONLY_DIAGNOSTIC',hardware_motion_acceptance=False,utc_start=one('passive_before')['utc'],utc_end=one('passive_after')['utc'],command_return_codes=rcs,stage=stage,diagnostic_sha256=hashlib.sha256((b/'diagnose.py').read_bytes()).hexdigest(),raw_command='diagnostic-live.command.json',raw_stdout='diagnostic-live.stdout',raw_stderr='diagnostic-live.stderr',uploads=38,downloads=0,nmt=0,retries=0,tx=38,rx=38,quarantined=False,CAN_closed=True,values=values,stable_samples=[r['values'] for r in samples],passive_changed_keys=[],staging_only_changed_keys=stagechanges,remaining='No Halt, enable, movement or cleanup controlword sent. Frozen implementation and PTY rehearsal pending main-session protocol decision.',cleanup='CAN closed normally; all local/remote temporary stage and evidence intentionally retained; current/services/config unchanged.')
(b/'readonly-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
if (b/'evidence-sha256.json').exists() and not (b/'evidence-sha256-phase1.json').exists():
 (b/'evidence-sha256-phase1.json').write_bytes((b/'evidence-sha256.json').read_bytes())
(b/'evidence-sha256.json').write_text(json.dumps({str(p.relative_to(b)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(b.iterdir()) if p.is_file() and p.name!='evidence-sha256.json'},indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
