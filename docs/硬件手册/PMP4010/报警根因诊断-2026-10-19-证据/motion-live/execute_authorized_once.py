"""根审冻结输入后一次执行；默认只检查本地计划，绝不自动重试。"""
import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parent/'motion-compare-proposal'
BASELINE = BASE.parent/'reset-observe-proposal/after-reset-passive-summary.json'
LOG_ROOT = '/var/tmp/km3-pmp4010-alarm-20261019-735538978204/payload/logs/relative-minus10-100rpm-a131072-once-20261019'
NAMES = ('motion_compare_once.py', 'pinned_payload.py')
TOKEN = '--parent-authorized-frozen-once'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=5',
       'root@192.168.0.188', 'python3 -I -B -']
KEYS = ('hostname', 'model', 'boot_id', 'device_id', 'config_sha', 'can_env_sha',
        'current', 'services', 'related_processes_excluding_snapshot_pid', 'can_rcvlists')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, obj, exclusive=False):
    with path.open('x' if exclusive else 'w') as stream:
        stream.write(json.dumps(obj, ensure_ascii=False, indent=2)+'\n')


def validate(config):
    if set(config) != {'files', 'argv', 'remote_stage', 'runner_sha256', 'passive_sha256', 'baseline_sha256', 'bootstrap_sha256'}:
        raise ValueError('冻结输入字段不完整或存在未知字段')
    if set(config['files']) != set(NAMES):
        raise ValueError('必须且只能冻结指定两文件')
    if config['remote_stage'] != '/var/tmp/km3-pmp4010-alarm-20261019-735538978204/motion-compare-proposal':
        raise ValueError('远程目录必须匹配已审bootstrap固定路径')
    argv = config['argv']
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        raise ValueError('argv必须是字符串列表')
    # No arbitrary script switches or alternate payload roots.
    if (len(argv) != 5 or argv[0] != '--execute-once' or argv[1] != '--od-session-ack'
            or argv[3] != '--motion-confirm'):
        raise ValueError('仅允许明确执行和两个会话确认参数')
    raw = {name:(SOURCE/name).read_bytes() for name in NAMES}
    for name in NAMES:
        if digest(raw[name]) != config['files'][name]:
            raise ValueError('冻结源文件SHA不符: '+name)
    for key, path in (('runner_sha256', Path(__file__)), ('passive_sha256', BASE/'passive_snapshot.py'),
                      ('baseline_sha256', BASELINE), ('bootstrap_sha256', SOURCE/'run_once_stdin.py')):
        if digest(path.read_bytes()) != config[key]:
            raise ValueError('冻结SHA不符: '+key)
    return raw


def invoke(prefix, raw, timeout):
    # Persist exact argv/stdin and claim before SSH. All later runs fail closed.
    script = BASE/(prefix+'.stdin.py')
    with script.open('xb') as stream:
        stream.write(raw)
    meta = dict(argv=SSH, stdin_file=str(script), stdin_sha256=digest(raw),
                start_utc=now(), automatic_retries=0, timeout_seconds=timeout)
    command = BASE/(prefix+'.command.json')
    save(command, meta, exclusive=True)
    try:
        p = subprocess.run(SSH, input=raw, capture_output=True, timeout=timeout)
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        rc, stdout, stderr = 124, exc.stdout or b'', exc.stderr or b''
        stderr += b'\nSSH timeout: remote state uncertain; no retry.\n'
    except OSError as exc:
        rc, stdout, stderr = 125, b'', str(exc).encode()
    (BASE/(prefix+'.stdout')).write_bytes(stdout)
    (BASE/(prefix+'.stderr')).write_bytes(stderr)
    (BASE/(prefix+'.rc')).write_text(str(rc)+'\n')
    meta.update(end_utc=now(), rc=rc)
    save(command, meta)
    return rc, stdout


def passive():
    # Source entrypoint expects argv[1], supplied within stdin, not parent command text.
    source = (BASE/'passive_snapshot.py').read_text()
    return ('import sys\nsys.argv = ["-", '+repr(LOG_ROOT)+']\n'+source).encode()


def compare(before, after):
    equality = {key:before[key] == after[key] for key in KEYS}
    for state in (before, after):
        if state['can_link'].get('rc') != 0:
            raise ValueError('被动CAN接口状态命令失败')
    old, = json.loads(before['can_link']['stdout'])
    new, = json.loads(after['can_link']['stdout'])
    equality['can_linkinfo'] = old['linkinfo'] == new['linkinfo']
    equality['can_flags'] = old['flags'] == new['flags']
    return equality


def upload_source(config, files):
    items = {name:base64.b64encode(raw).decode() for name, raw in files.items()}
    # Remote mkdir is exclusive. Partial upload is deliberately unrecoverable by this runner.
    return ('''import base64,datetime,hashlib,json,pathlib
stage=pathlib.Path(STAGE)
stage.mkdir(mode=0o700,exist_ok=False)
items=ITEMS
expected=EXPECTED
for name,encoded in items.items():
 raw=base64.b64decode(encoded,validate=True)
 assert hashlib.sha256(raw).hexdigest()==expected[name]
 with (stage/name).open('xb') as stream:stream.write(raw)
 assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==expected[name]
print(json.dumps(dict(event='staged_exact_files',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),stage=str(stage),sha256=expected)))
'''.replace('STAGE', repr(config['remote_stage'])).replace('ITEMS', repr(items)).replace('EXPECTED', repr(config['files']))).encode()


def execute_source(config, bootstrap_raw=None):
    # Preserve the exact frozen bootstrap; its path/argv review is independent of this runner.
    if bootstrap_raw is None:
        bootstrap_raw = (SOURCE/'run_once_stdin.py').read_bytes()
    if digest(bootstrap_raw) != config['bootstrap_sha256']:
        raise ValueError('冻结bootstrapSHA不符')
    # A separate exclusive marker before source loading prevents manual wrapper replay too.
    return ('''import base64,datetime,hashlib,json,pathlib,sys
stage=pathlib.Path(STAGE)
expected=EXPECTED
for name,sha in expected.items():
 assert not (stage/name).is_symlink()
 assert hashlib.sha256((stage/name).read_bytes()).hexdigest()==sha
with (stage/'execution.attempt').open('x') as stream:
 stream.write(datetime.datetime.now(datetime.timezone.utc).isoformat()+'\\n')
script=stage/'motion_compare_once.py'
sys.argv=[str(script)]+ARGV
print(json.dumps(dict(event='remote_execution_start',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),argv=sys.argv,sha256=expected)),flush=True)
try:
 bootstrap=base64.b64decode(BOOTSTRAP,validate=True)
 assert hashlib.sha256(bootstrap).hexdigest()==BOOTSHA
 exec(compile(bootstrap,'<frozen-bootstrap>','exec'),dict(__name__='__main__',__file__='<frozen-bootstrap>'))
finally:
 print(json.dumps(dict(event='remote_execution_end',utc=datetime.datetime.now(datetime.timezone.utc).isoformat())),flush=True)
'''.replace('STAGE', repr(config['remote_stage'])).replace('EXPECTED', repr(config['files'])).replace('ARGV', repr(config['argv'])).replace('BOOTSTRAP', repr(base64.b64encode(bootstrap_raw).decode())).replace('BOOTSHA', repr(config['bootstrap_sha256']))).encode()


def brief_feedback(raw):
    events = []
    invalid = 0
    for line in raw.decode(errors='replace').splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                events.append(item)
        except ValueError:
            invalid += 1
    exits = [e for e in events if e.get('event') in ('单次退出', '一次动作反馈完成')]
    enable = [e for e in events if e.get('event') == 'manual_result' and e.get('action') == 'enable']
    go = [e for e in events if e.get('event') == 'Go观察计时起点']
    values = [e for e in events if e.get('event') == '诊断实读' and e.get('index') in ('6041', '603F', '1001')]
    return dict(enable_results=enable, Go_timing_events=go,
                operation_enabled_cleared=exits[-1].get('operation_enabled_cleared') if exits else None,
                last_health_values={key:next((e for e in reversed(values) if e['index'] == key), None)
                                    for key in ('6041', '603F', '1001')},
                terminal_events=[e for e in events if e.get('event') in ('单次退出', '结束且不重试', '一次动作反馈完成')],
                invalid_json_lines=invalid, mechanical_stop_confirmed=False,
                interpretation='rc为远程应用退出码；不自动归为transport丢包；Go计时事件仅证明首次Go写前已到达')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-input', type=Path, required=True)
    parser.add_argument(TOKEN, action='store_true')
    args = parser.parse_args(argv)
    config_raw = args.frozen_input.read_bytes()
    config = json.loads(config_raw)
    files = validate(config)
    if not args.parent_authorized_frozen_once:
        print(json.dumps(dict(local_plan_only=True, CAN_sends=0, SSH_invocations=0,
                              frozen_input_sha256=digest(config_raw), **config), ensure_ascii=False, indent=2))
        return 0
    # This is a local authorization marker, not a grant of human permission.
    save(BASE/'execution.attempt.json', dict(utc=now(), frozen_input_sha256=digest(config_raw),
         frozen_input=config, automatic_retries=0), exclusive=True)
    rc, raw = invoke('before-passive', passive(), 35)
    if rc:
        raise RuntimeError('前置被动采样失败；本次机会已消耗，禁止重试')
    before = json.loads(raw)
    save(BASE/'before-passive-summary.json', before)
    equality = compare(json.loads(BASELINE.read_text()), before)
    save(BASE/'before-baseline-comparison.json', equality)
    if not all(equality.values()) or before['log_root_exists']:
        raise RuntimeError('前置被动状态变化或本次日志目录已存在；停止，回报根，不绕过')
    rc, _ = invoke('stage', upload_source(config, files), 35)
    if rc:
        raise RuntimeError('暂存失败；禁止重试或执行')
    rc, motion_raw = invoke('motion-once', execute_source(config), 90)
    observations = brief_feedback(motion_raw)
    save(BASE/'motion-immediate-feedback.json', observations)
    # Tell the parent immediately through console before the separately authorized passive read.
    print(json.dumps(dict(event='motion_ssh_returned', utc=now(), rc=rc,
                          state='必须核对原始反馈；机械停机未由runner证明', feedback=observations, automatic_retries=0)), flush=True)
    if rc != 0 or observations['operation_enabled_cleared'] is not True:
        save(BASE/'execution-summary.json', dict(motion_rc=rc, automatic_retries=0,
             passive_after_deferred=True, feedback=observations, mechanical_stop_confirmed=False))
        print('动作异常或未确认operation enabled清零：立即回报根；不自动追加板端命令。', flush=True)
        return rc or 1
    after_rc, raw = invoke('after-passive', passive(), 35)
    summary = dict(motion_rc=rc, passive_rc=after_rc, automatic_retries=0,
                   mechanical_stop_confirmed=False, source_sha256=config['files'])
    if after_rc == 0:
        after = json.loads(raw)
        save(BASE/'after-passive-summary.json', after)
        summary['passive_equal'] = compare(before, after)
        summary['logs'] = [{k:v for k,v in item.items() if k != 'content_base64'} for item in after['logs']]
    save(BASE/'execution-summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return rc if rc else after_rc


if __name__ == '__main__':
    raise SystemExit(main())
