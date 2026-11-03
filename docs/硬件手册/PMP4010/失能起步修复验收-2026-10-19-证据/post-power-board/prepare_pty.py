from pathlib import Path
import hashlib,json,difflib
B=Path(__file__).resolve().parent;repo=Path('/home/zakiu/km3');prior=B.parent/'stage2-board-load-pty-snapshot'
root=B/'pty-source';root.mkdir(exist_ok=False)
files=list(json.loads((prior/'pty-source-sha256.json').read_text()))
manifest={}
expected={'pmp4010_bench.py':'8f6677156f04d331c7a5c0e552bc007f7949c65c1482bc7d67183cd17b8d4b5b','pmp4010_position.py':'e93ed776ee7ad13dc10e84f3027d6fc9c95200e016cc226ec61af2db1c86c9da','pmp4010_tui.py':'9d1e64e20e764471c84f76f6f6937941c28480e3a40b90e8203e7db5fa1190d1'}
for rel in files:
 src=repo/rel;data=src.read_bytes();sha=hashlib.sha256(data).hexdigest()
 if src.name in expected:assert sha==expected[src.name]
 dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
 manifest[rel]=dict(sha256=sha,source=str(src))
(B/'pty-source-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
s=(prior/'mock_app.py').read_text().replace("choices=('success','no-ack','old-demand')","choices=('success','halt-drift','final-no-ack')")
start=s.index('    class ProbeNode(BenchNode):');end=s.index('    bus=mockbus.MockBus',start)
s=s[:start]+'''    class MoveNode(BenchNode):
        """Independent current/final ACK generations; 607A alone changes neither 6062 nor bit10."""
        def __init__(self):
            super().__init__();self.current_accepted=False;self.final_triggered=False
        def handle(self,frame,now):
            if frame.arbitration_id!=0x601:raise AssertionError('NMT/other-node forbidden')
            d=frame.data;key=(d[1]|d[2]<<8,d[3])
            mockbus.MockNode.handle(self,frame,now)
            if d[0] not in (0x2f,0x2b,0x23):return
            if key==(0x6040,0):
                word=self.value(0x6040)
                states={0:0x240,2:0x217,6:0x231,0x106:0x231,0x107:0x233,0x127:0x233,0x137:0x233,0x12f:0x237,0x2f:0x237,0x3f:0x237}
                if word not in states:raise AssertionError('unknown Stage3 controlword')
                state=states[word]
                if word==0x137:
                    self.current_accepted=True;state|=0x1000
                    self.od[0x6062,0].data=self.od[0x607a,0].data
                    emit('fixture_current_new_ack',internal_demand=self.value(0x6062,signed=True),target=self.value(0x607a,signed=True))
                if word==0x12f and args.scenario=='halt-drift':
                    self.set_value(0x6064,-1469)
                    emit('fixture_halt_enable_drift',actual_position=-1469)
                if word==0x3f:
                    self.final_triggered=True
                    if args.scenario!='final-no-ack':
                        state|=0x1000
                        self.od[0x6062,0].data=self.od[0x607a,0].data
                        emit('fixture_final_new_ack',internal_demand=self.value(0x6062,signed=True),target=self.value(0x607a,signed=True))
                if word==0x2f and self.final_triggered and args.scenario!='final-no-ack':
                    state|=0x400
                    self.od[0x6064,0].data=self.od[0x607a,0].data
                self.set_value(0x6041,state)
            if key==(0x607a,0):
                emit('fixture_607A_only_write',target=self.value(0x607a,signed=True),internal_demand=self.value(0x6062,signed=True),statusword=self.value(0x6041),current_accepted=self.current_accepted,final_triggered=self.final_triggered)
            emit('fixture_after_download',index=key[0],sub=key[1],controlword=self.value(0x6040),statusword=self.value(0x6041),actual_position=self.value(0x6064,signed=True),internal_demand=self.value(0x6062,signed=True),actual_velocity=self.value(0x606c,signed=True),current_accepted=self.current_accepted,final_triggered=self.final_triggered)
    emit('mock_scenario',stage='stage3_sealed_disabled_relative',scenario=args.scenario,hardware_fact=False)
    node=MoveNode()
    for index,sub,value in ((0x6040,0,6),(0x6041,0,0x231),(0x6064,0,-1470),
                            (0x607a,0,-1469),(0x6062,0,7800),(0x605d,0,1),
                            (0x6081,0,100),(0x6083,0,5000000),(0x6084,0,5000000),
                            (0x607d,1,-2147483628),(0x607d,2,2147483627),
                            (0x607f,0,9000000),(0x6080,0,4000),
                            (0x60c5,0,2147483647),(0x60c6,0,2147483647)):
        node.set_value(index,value,sub)
''' +s[end:]
(B/'mock_app.py').write_text(s)
r=(prior/'pty_runner.py').read_text().replace("choices=('success','no-ack','old-demand')","choices=('success','halt-drift','final-no-ack')")
(B/'pty_runner.py').write_text(r)
s=(prior/'drive_probe_pty.py').read_text().replace('B two confirmations','R +36 two confirmations').replace("out=B/('pty-probe-'+scenario)","out=B/('pty-move-'+scenario)")
s=s.replace("text('B');probe=freeze_and_confirm('probe_pp_start','B-probe')","text('R');wait_for(lambda:state().get('view')=='action','R input');text('+36');probe=freeze_and_confirm('relative','R-move')")
s=s.replace("assert probe['start']==probe['target']==-1470","assert probe['start']==-1470 and probe['target']==-1434")
s=s.replace("assert probe['token']=='PROBE_PP_START mock-bench 1 -1470'","assert probe['token']=='RELATIVE mock-bench 1 -1434'")
s=s.replace("'B result',seconds=15","'R result',seconds=20").replace("'B-final-result-before-exit'","'R-final-result-before-exit'")
(B/'drive_move_pty.py').write_text(s)
(B/'record_local.py').write_bytes((prior/'record_local.py').read_bytes())
for name,old in [('mock_app.py','mock_app.py'),('drive_move_pty.py','drive_probe_pty.py')]:
 (B/(name+'.diff')).write_text(''.join(difflib.unified_diff((prior/old).read_text().splitlines(True),(B/name).read_text().splitlines(True),fromfile='stage2/'+old,tofile='post-power/'+name)))
print(json.dumps(dict(sources=len(manifest),SSH_calls=0,CAN_opens=0)))
