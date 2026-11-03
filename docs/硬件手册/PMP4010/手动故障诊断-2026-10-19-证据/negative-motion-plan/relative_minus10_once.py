"""Prepare one existing relative -10mm / 100rpm manual transaction, no reset."""
import argparse
from pathlib import Path
import sys
import time

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parent))
from pinned_payload import ROOT, MANIFEST_SHA, Output, emit, load_pinned, make_controller


def perform_once(ctl, out, clock=time.monotonic, pause=time.sleep):
    """Original APIs only; one frozen target, original protection and cleanup."""
    reviewed=None
    completed=False
    result={}
    try:
        ctl.scan(out)
        if ctl.context!=('can1',1000000) or ctl.node!=1 or ctl.args.node!=1 or ctl.args.node_range!='1':
            raise ValueError('实际配置不再是can1/1Mbps/节点1；禁止运动')
        ctl.refresh(out)
        # _fresh refuses the original protection latch; a nonzero603F during
        # either snapshot cannot reach authorization or any motion download.
        ctl._fresh(ctl.node)
        ctl.authorize()
        ctl.set_value('speed','100')
        reviewed=ctl.review_action('relative',out,'-10')
        emit('单次冻结审阅',start=reviewed['start'],target=reviewed['target'],
             delta=reviewed['target']-reviewed['start'],velocity=reviewed['velocity'],
             requested_mm=-10,requested_rpm=100,mm_per_motor_revolution=4,
             calibration=reviewed['calibration'],settings=reviewed['settings'],
             lines=reviewed['lines'])
        ctl.execute_action(reviewed,out)  # Exactly one aggregate invocation.
        # The existing three stages retain their own original move_timeout.
        # This additional bound covers observing the resulting PP trajectory.
        deadline=clock()+10.0
        while clock()<deadline:
            ctl.refresh(out)
            row=ctl._fresh(ctl.node)
            v=row['values']
            emit('运行采样',position=v['actual_position'],velocity=v['actual_velocity'],
                 target=reviewed['target'],statusword='0x{:04X}'.format(v['statusword']))
            if (v['actual_position']==reviewed['target'] and v['actual_velocity']==0
                    and v['statusword'] & ctl.tool._canopen.CIA402_HOMING_TARGET_REACHED):
                completed=True
                result.update(target_reached=True,position=v['actual_position'],target=reviewed['target'],
                              mechanical_stop_confirmed=False)
                return result
            pause(.05)
        raise TimeoutError('单次动作ACK后10秒内未观察到目标/零速/bit10同时成立')
    except BaseException as exc:
        session=ctl.manual_sessions.get(ctl.node)
        if (session is not None and ctl.context is not None
                and (ctl.context[0],ctl.node) not in ctl.blocked
                and (ctl.node in ctl.proofs or ctl.node in ctl.enabled_sessions
                     or session.needs_stop or getattr(session.client,'quarantined',False))):
            ctl._cleanup_failure(ctl.node,session,exc,out)
        raise
    finally:
        # Success: original exit-only Shutdown. Failure: an original protection
        # latch/quarantined client prevents this exit from sending another SDO.
        results=ctl.close(out)
        result.update(shutdown_results=results,operation_enabled_cleared=any(
            item['node']==ctl.node and item['disabled_confirmed'] is True for item in results))
        emit('单次退出',target_reached=completed,shutdown_results=results,
             exit_semantics='原close：6040=0006，仅确认operation enabled清零；机械停止未知',
             second_target=False,automatic_retries=0)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-once',action='store_true')
    args=parser.parse_args(argv)
    emit('计划',payload=str(ROOT),manifest_sha256=MANIFEST_SHA,node=1,relative_mm=-10,rpm=100,
         mm_per_motor_revolution=4,aggregate_transactions=1,reset_operations=0,
         live_requested=args.execute_once,post_ack_observation_seconds=10)
    if not args.execute_once:
        emit('仅本地计划',CAN_opens=0,CAN_sends=0)
        return 0
    tool,controller_type=load_pinned()
    run_dir=ROOT/'logs/relative-minus10-100rpm-once-20261019'
    run_dir.mkdir(parents=True,exist_ok=False)  # Refuse repeating this prepared run.
    ctl=make_controller(tool,controller_type,run_dir/'motion.jsonl')
    try:
        result=perform_once(ctl,Output())
        emit('动作反馈完成',**result)
        return 0 if result['operation_enabled_cleared'] else 1
    except BaseException as exc:
        emit('结束且不重试',exception=type(exc).__name__,message=str(exc),
             motion_not_authorized_by_reset=True,mechanical_stop_confirmed=False)
        return 130 if isinstance(exc,KeyboardInterrupt) else 1


if __name__=='__main__':
    raise SystemExit(main())
