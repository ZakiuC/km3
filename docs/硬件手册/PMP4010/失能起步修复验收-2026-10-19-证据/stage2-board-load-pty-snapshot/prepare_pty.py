from pathlib import Path
import hashlib,json
B=Path(__file__).parent;prior=B.parent;repo=Path('/home/zakiu/km3');freeze=Path('/tmp/km3-pp-disabled-start-20261019-impl/switched-on-stage2/freeze')
assert hashlib.sha256((freeze/'source-sha256.json').read_bytes()).hexdigest()=='3e492ef6a6b36aa94789d78500a2992aee0ff52b2702acc7e377ffb484408b47'
frozen=json.loads((freeze/'source-sha256.json').read_text())['files']
root=B/'pty-source';root.mkdir(exist_ok=False)
items=list(json.loads((prior/'pty-source-sha256.json').read_text()))
manifest={}
for rel in items:
 src=freeze/'source'/rel if rel in frozen else repo/rel
 data=src.read_bytes();sha=hashlib.sha256(data).hexdigest()
 if rel in frozen:assert sha==frozen[rel]
 dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
 manifest[rel]=dict(sha256=sha,source=str(src),frozen=rel in frozen)
(B/'pty-source-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
s=(prior/'mock_app.py').read_text().replace("choices=('success','halt-refused','old-demand')","choices=('success','no-ack','old-demand')")
start=s.index('    class ProbeNode(BenchNode):');end=s.index('    bus=mockbus.MockBus',start)
s=s[:start]+'''    class ProbeNode(BenchNode):
        """Stage2 model; 607A never updates 6062 or bit10, no operation enable."""
        def __init__(self):
            super().__init__();self.accepted=False
        def handle(self,frame,now):
            if frame.arbitration_id!=0x601:raise AssertionError('NMT/other-node forbidden')
            d=frame.data;key=(d[1]|d[2]<<8,d[3])
            mockbus.MockNode.handle(self,frame,now)
            if d[0] not in (0x2f,0x2b,0x23):return
            if key==(0x6040,0):
                word=self.value(0x6040)
                if word&8:
                    emit('forbidden_operation_enable',word=word)
                    raise AssertionError('Stage2 forbids bit3 and every enabled fallback')
                states={0:0x240,2:0x217,6:0x231,0x106:0x231,0x107:0x233,0x127:0x233,0x137:0x233}
                if word not in states:raise AssertionError('unknown Stage2 controlword')
                if word not in (0,2,6) and not word&0x100:raise AssertionError('Stage2 business Halt release')
                state=states[word]
                if word==0x137 and args.scenario!='no-ack':
                    self.accepted=True;state|=0x1000
                    if args.scenario!='old-demand':self.od[0x6062,0].data=self.od[0x607a,0].data
                    emit('fixture_new_command_ack',internal_demand=self.value(0x6062,signed=True),target=self.value(0x607a,signed=True))
                if word==0x127 and self.accepted:state|=0x400
                self.set_value(0x6041,state)
            if key==(0x607a,0):
                emit('fixture_607A_only_write',target=self.value(0x607a,signed=True),internal_demand=self.value(0x6062,signed=True),statusword=self.value(0x6041),accepted=self.accepted)
            emit('fixture_after_download',index=key[0],sub=key[1],controlword=self.value(0x6040),statusword=self.value(0x6041),actual_position=self.value(0x6064,signed=True),internal_demand=self.value(0x6062,signed=True),actual_velocity=self.value(0x606c,signed=True),accepted=self.accepted)
    emit('mock_scenario',stage='stage2_SwitchedOn_no_operation_enable',scenario=args.scenario,hardware_fact=False)
    node=ProbeNode()
    for index,sub,value in ((0x6040,0,6),(0x6041,0,0x231),(0x6064,0,-1470),
                            (0x607a,0,-1469),(0x6062,0,7800),(0x605d,0,1),
                            (0x6081,0,100),(0x6083,0,5000000),(0x6084,0,5000000),
                            (0x607d,1,-2147483628),(0x607d,2,2147483627),
                            (0x607f,0,9000000),(0x6080,0,4000),
                            (0x60c5,0,2147483647),(0x60c6,0,2147483647)):
        node.set_value(index,value,sub)
''' +s[end:]
s=s.replace('config.move_timeout=120','config.move_timeout=15')
(B/'mock_app.py').write_text(s)
(B/'pty_runner.py').write_text((prior/'pty_runner.py').read_text().replace("choices=('success','halt-refused','old-demand')","choices=('success','no-ack','old-demand')"))
(B/'record_local.py').write_bytes((prior/'record_local.py').read_bytes())
print(json.dumps({'snapshot_sources':len(manifest),'freeze_verified':True,'CAN_opens':0,'SSH_calls':0}))
