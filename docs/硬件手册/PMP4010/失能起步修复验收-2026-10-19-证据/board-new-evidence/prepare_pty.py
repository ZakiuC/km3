from pathlib import Path
import hashlib,json,shutil
B=Path(__file__).parent;repo=Path('/home/zakiu/km3');freeze=Path('/tmp/km3-pp-disabled-start-20261019-impl/probe-freeze')
assert hashlib.sha256((freeze/'source-sha256.json').read_bytes()).hexdigest()=='a9750a6952ee851f502bd0bd59b7eedd2542d35c1f423503f1a75acc17c6e5ff'
frozen=json.loads((freeze/'source-sha256.json').read_text())['files']
root=B/'pty-source';root.mkdir(exist_ok=False)
items=['device/drivers/pmp4010/tools/'+n for n in ('pmp4010.py','pmp4010_bench.py','pmp4010_position.py','pmp4010_tui.py','pmp4010_params.py','pmp4010_velocity.py','_test_bench_support.py')]
items+=['tools/hwtui/'+n for n in ('canbus.py','canopen.py','canopen_wire.py','canopen_consts.py','mockbus.py','tui.py','discover.py','verify_pmp4010_pty.py')]
items+=['device/drivers/pmp4010/driver/od.json','build-host/libpmp4010_velocity.so']
manifest={}
for rel in items:
 src=freeze/'source'/rel if rel in frozen else repo/rel
 data=src.read_bytes();sha=hashlib.sha256(data).hexdigest()
 if rel in frozen:assert sha==frozen[rel]
 dst=root/rel;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
 manifest[rel]=dict(sha256=sha,source=str(src),frozen=rel in frozen)
(B/'pty-source-sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
s=(Path('/tmp/km3-pp-motion-20261017/mock_app.py')).read_text().replace("ROOT=Path('/home/zakiu/km3')", "ROOT=Path(__file__).parent/'pty-source'")
s=s.replace("parser.add_argument('--keep-bit10-zero',action='store_true')", "parser.add_argument('--scenario',choices=('success','halt-refused','old-demand'),default='success')")
start=s.index("    if 'prepare_pp' not in bench.ACTION_POLICY:")
end=s.index("    bus=mockbus.MockBus",start)
node='''    if 'probe_pp_start' not in bench.ACTION_POLICY:
        raise RuntimeError('frozen probe missing')
    class ProbeNode(BenchNode):
        """Firmware behaviour model only; shared MockNode alone encodes SDO."""
        def __init__(self):
            super().__init__()
            self.accepted=False
        def handle(self,frame,now):
            if frame.arbitration_id!=0x601:
                raise AssertionError('fixture forbids NMT/other node')
            d=frame.data
            key=(d[1]|d[2]<<8,d[3])
            mockbus.MockNode.handle(self,frame,now)
            if d[0] not in (0x2f,0x2b,0x23):return
            if key==(0x6040,0):
                word=self.value(0x6040)
                state={0:0x40,2:7,6:0x21,0x106:0x21,0x107:0x23,0x10f:0x27,0x12f:0x27,0x13f:0x27}.get(word)
                if state is None:
                    emit('forbidden_unhalt_enabled_command',word=word)
                    raise AssertionError('unexpected or unsafe controlword')
                if (word&0xf)==0xf and not word&0x100:
                    emit('old_trajectory_resume_hazard',word=word,internal_target=self.value(0x6062,signed=True))
                    raise AssertionError('enabled Halt release would resume old trajectory')
                if word==0x106 and args.scenario=='halt-refused':
                    self.set_value(0x6040,word&~0x100)
                    emit('fixture_halt_not_accepted',requested=word,actual=word&~0x100)
                if word==0x13f:
                    self.accepted=True;state|=0x1000
                    if args.scenario!='old-demand':
                        self.od[0x6062,0].data=self.od[0x607a,0].data
                    emit('fixture_new_command_ack',internal_demand=self.value(0x6062,signed=True),target=self.value(0x607a,signed=True))
                if word==0x12f and self.accepted:state|=0x400
                self.set_value(0x6041,state)
            if key==(0x607a,0):
                emit('fixture_607A_only_write',target=self.value(0x607a,signed=True),internal_demand=self.value(0x6062,signed=True),statusword=self.value(0x6041),accepted=self.accepted)
            emit('fixture_after_download',index=key[0],sub=key[1],controlword=self.value(0x6040),statusword=self.value(0x6041),actual_position=self.value(0x6064,signed=True),internal_demand=self.value(0x6062,signed=True),actual_velocity=self.value(0x606c,signed=True),accepted=self.accepted)
    emit('mock_scenario',scenario=args.scenario,hardware_fact=False)
    node=ProbeNode()
    for index,sub,value in ((0x6040,0,0),(0x6041,0,0x260),(0x6064,0,-1470),
                            (0x607a,0,7800),(0x6062,0,7800),(0x605d,0,1),
                            (0x6081,0,500000),(0x6083,0,5000000),(0x6084,0,5000000),
                            (0x607d,1,-2147483628),(0x607d,2,2147483627),
                            (0x607f,0,9000000),(0x6080,0,4000),
                            (0x60c5,0,2147483647),(0x60c6,0,2147483647)):
        node.set_value(index,value,sub)
'''
s=s[:start]+node+s[end:]
s=s.replace('config=options()',"config=options(out/'motion.jsonl')")
s=s.replace('config.soft_min=-1465','config.soft_min=None').replace('config.soft_max=-1429','config.soft_max=None').replace('config.move_timeout=5','config.move_timeout=120')
# Every imported source, including native dependency, is recorded independently.
s=s.replace('for module in (tool,bench,tui,tool._canopen,tool._canbus):','for module in (tool,bench,tui,tool._canopen,tool._canbus):')
(B/'mock_app.py').write_text(s)
p=B/'pty_runner.py';s=p.read_text().replace("ROOT=Path('/home/zakiu/km3')","ROOT=Path(__file__).parent/'pty-source'").replace('keep_bit10_zero=False',"scenario='success'").replace("        if keep_bit10_zero:\n            self.command.append('--keep-bit10-zero')", "        self.command += ['--scenario',scenario]").replace("parser.add_argument('--keep-bit10-zero',action='store_true')", "parser.add_argument('--scenario',choices=('success','halt-refused','old-demand'),default='success')").replace('Session(args.output_dir,args.keep_bit10_zero)','Session(args.output_dir,args.scenario)')
p.write_text(s)
print(json.dumps({'copied_sources':len(manifest),'frozen_subset_verified':sum(x['frozen'] for x in manifest.values()),'real_CAN_opens':0}))
