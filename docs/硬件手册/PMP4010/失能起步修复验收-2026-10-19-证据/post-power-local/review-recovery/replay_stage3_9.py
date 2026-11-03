import pathlib
path=pathlib.Path('docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/stage2-local-acceptance/review-message-transfer/replay.py')
source=path.read_text().split('cases = [',1)[0]
source=source.replace("manifest = Path('/tmp/km3-pp-disabled-start-20261019-impl/switched-on-stage2/freeze/source-sha256.json')", "manifest = Path('docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/stage2-local-acceptance/freeze-source-sha256.json')")
source=source.replace("expected = json.loads(manifest.read_text())['files']", "expected = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in json.loads(manifest.read_text())['files']}")
exec(compile(source,str(path)+':memory-prefix','exec'))
from _test_disabled_move import DisabledMoveNode
cases=['fresh_preload_old_target','new_controller_rejects_old_seal','calibration_changed_after_seal','demand_reverts_after_final_target','second_6062_release_read_old','early_final_ACK','missing_final_ACK','second_moving_position_outside_origin','next_move_repeats_preload']
results=[]
normal=[0x106,0x107,0x127,0x137,0x127,0x12f,0x2f,0x3f,0x2f,2,0]
for case in cases:
    node=DisabledMoveNode();bus=DelayedBus([node]);clock=Clock();clock.now=0;journals=[];phase=[None];seen=[0];injected=[False]
    args=options('memory-only.jsonl');args.move_timeout=5
    with ExitStack() as stack:
        for patch in environment(bus):stack.enter_context(patch)
        stack.enter_context(mock.patch('time.monotonic',clock.monotonic));stack.enter_context(mock.patch('time.sleep',clock.sleep))
        stack.enter_context(mock.patch.object(position,'Journal',MemoryJournal))
        ctl=BenchController(tool,args);out=ui.JobOutput(queue.Queue(),threading.Event());ctl.rt_ack=True;ctl.scan(out);ctl.refresh(out)
        ctl.od_reason='power recovery readonly review';ctl.settings.update(soft_min=-1506,soft_max=-1434,max_displacement=36)
        review=ctl.review('relative',1,36,out);bus.sent.clear()
        factory=ctl._disabled_start_guard
        def wrapped_factory(*a):
            real=factory(*a)
            def guard(session,label,**kw):
                if phase[0]!=label:phase[0]=label;seen[0]=0
                return real(session,label,**kw)
            return guard
        stack.enter_context(mock.patch.object(ctl,'_disabled_start_guard',side_effect=wrapped_factory))
        event=MemoryJournal.event
        def observed(self,name,**data):
            event(self,name,**data)
            if name=='write_readback' and data['object']=='target_position' and data['expected']==-1434:
                if case=='demand_reverts_after_final_target':node.set_value(0x6062,7800);injected[0]=True
                if case=='early_final_ACK':node.set_value(0x6041,0x1027);injected[0]=True
        stack.enter_context(mock.patch.object(MemoryJournal,'event',observed))
        send=bus.send
        def sending(frame):
            d=frame.data;addr=(d[1]|d[2]<<8,d[3])
            target=(0x6062,7800) if case=='second_6062_release_read_old' and phase[0]=='after_halt_release' else (0x6064,-1400) if case=='second_moving_position_outside_origin' and phase[0]=='最终目标新命令' else None
            if target and d[0]==0x40 and addr==(target[0],0):
                seen[0]+=1
                if seen[0]==2 and not injected[0]:
                    old=node.od[addr].data;node.set_value(target[0],target[1]);injected[0]=True
                    try:return send(frame)
                    finally:node.od[addr].data=old
            return send(frame)
        stack.enter_context(mock.patch.object(bus,'send',side_effect=sending))
        if case=='calibration_changed_after_seal':node.set_value(0x608f,131073,1)
        if case=='missing_final_ACK':node.missing_final_ack=True
        if case=='new_controller_rejects_old_seal':ctl=BenchController(tool,args)
        error=None
        try:ctl.execute(review,out)
        except BaseException as exc:error=exc
        controls=[v for i,_,v in requests(bus,True) if i==0x6040]
        target_writes=[v if v<2**31 else v-2**32 for i,_,v in requests(bus,True) if i==0x607a]
        if case in ('fresh_preload_old_target','next_move_repeats_preload'):
            assert error is None and controls==normal and target_writes==[-1470,-1434]
            if case=='next_move_repeats_preload':
                node.final_triggered=False;ctl.refresh(out);second=ctl.review('relative',1,-36,out);bus.sent.clear();ctl.execute(second,out)
                assert [v for i,_,v in requests(bus,True) if i==0x6040]==normal
                assert node.value(0x6064,signed=True)==-1470
        else:
            assert error is not None,case
            if case=='new_controller_rejects_old_seal':assert not bus.sent
            elif case=='calibration_changed_after_seal':assert not requests(bus,True)
            else:
                assert controls[-2:]==[2,0]
                if case not in ('missing_final_ACK','second_moving_position_outside_origin'):assert 0x3f not in controls
                if case in ('demand_reverts_after_final_target','second_6062_release_read_old','early_final_ACK','second_moving_position_outside_origin'):assert injected[0]
        results.append(dict(case=case,result='PASS',error=None if error is None else str(error),controls=[hex(v) for v in controls],target_writes=target_writes,requests=len(bus.sent)))
assert hashes()==expected and not forbidden
print(json.dumps(dict(result='PASS',case_count=len(results),cases=results,current_source_files_before_after_equal=len(expected),forbidden_IO_attempts=len(forbidden),hardware_acceptance=False),ensure_ascii=False,indent=2))
