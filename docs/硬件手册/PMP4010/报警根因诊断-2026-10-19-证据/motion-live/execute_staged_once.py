"""仅对已暂存、已冷加载、根审冻结文件执行一次；无上传、复位或重试。"""
import argparse
import importlib.util
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('motion_evidence_runner', BASE/'execute_authorized_once.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-input', type=Path, required=True)
    parser.add_argument('--runner-sha256', required=True)
    parser.add_argument('--frozen-sha256', required=True)
    parser.add_argument('--parent-authorized-staged-once', action='store_true')
    args = parser.parse_args(argv)
    if r.digest(Path(__file__).read_bytes()) != args.runner_sha256:
        raise ValueError('已暂存执行器自身SHA变化')
    config_raw = args.frozen_input.read_bytes()
    if r.digest(config_raw) != args.frozen_sha256:
        raise ValueError('冻结输入文件SHA变化')
    config = json.loads(config_raw)
    r.validate(config)
    # Stage/cold receipts are necessary local evidence, never authorization.
    for name in ('stage', 'cold-load'):
        if (BASE/(name+'.rc')).read_text().strip() != '0':
            raise ValueError('暂存/冷加载记录不为0')
    staged = json.loads((BASE/'stage.stdout').read_text())
    if staged['stage'] != config['remote_stage'] or staged['sha256'] != config['files']:
        raise ValueError('暂存记录不匹配冻结输入')
    if not args.parent_authorized_staged_once:
        print(json.dumps(dict(local_plan_only=True, SSH_invocations=0, CAN_sends=0,
            frozen_input_sha256=r.digest(config_raw), runner_sha256=args.runner_sha256,
            remote_stage=config['remote_stage'], frozen_argv=config['argv']), ensure_ascii=False, indent=2))
        return 0
    # Claim before even the fresh passive SSH. Any failure permanently consumes this invocation.
    r.save(BASE/'staged-execution.attempt.json', dict(utc=r.now(), automatic_retries=0,
        runner_sha256=args.runner_sha256, frozen_input_sha256=r.digest(config_raw),
        frozen_input=config), exclusive=True)
    rc, raw = r.invoke('before-motion-passive', r.passive(), 35)
    if rc:
        raise RuntimeError('前置被动采样失败；禁止重试，回报根')
    before = json.loads(raw)
    r.save(BASE/'before-motion-passive-summary.json', before)
    equality = r.compare(json.loads(r.BASELINE.read_text()), before)
    r.save(BASE/'before-motion-comparison.json', equality)
    if not all(equality.values()) or before['log_root_exists']:
        raise RuntimeError('前置状态变化/日志目录已存在；禁止绕过，回报根')
    # Generated stdin rehashes both already-staged files and uses remote open(x) before bootstrap.
    # SSH command text remains exactly python3 -I -B - and contains no diagnostic paths.
    rc, raw = r.invoke('motion-once', r.execute_source(config), 90)
    feedback = r.brief_feedback(raw)
    r.save(BASE/'motion-immediate-feedback.json', feedback)
    summary = dict(motion_rc=rc, feedback=feedback, automatic_retries=0,
                   mechanical_stop_confirmed=False, source_sha256=config['files'])
    r.save(BASE/'staged-execution-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if rc != 0 or feedback['operation_enabled_cleared'] is not True:
        print('立即回报根：动作异常或未确认OperationEnabled清零；未自动追加板端命令。', flush=True)
        return rc or 1
    after_rc, raw = r.invoke('after-motion-passive', r.passive(), 35)
    summary['passive_rc'] = after_rc
    if after_rc == 0:
        after = json.loads(raw)
        r.save(BASE/'after-motion-passive-summary.json', after)
        summary['passive_equal'] = r.compare(before, after)
        summary['logs'] = [{key:value for key,value in item.items() if key != 'content_base64'}
                           for item in after['logs']]
        old, = json.loads(before['can_link']['stdout'])
        new, = json.loads(after['can_link']['stdout'])
        summary['interface_counter_delta'] = {side:{name:value-old['stats64'][side][name]
            for name,value in new['stats64'][side].items()} for side in ('rx','tx')}
    r.save(BASE/'staged-execution-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return after_rc


if __name__ == '__main__':
    raise SystemExit(main())
