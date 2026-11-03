"""Parent-authorized exact SHA runner. No retry. Local evidence only.

Requires --parent-authorized-8706cf-once. Does not create remote files.
After the one probe invocation, takes the separately authorized passive snapshot.
"""
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

BASE = pathlib.Path(__file__).resolve().parent
PASSIVE = BASE.parents[1] / 'passive'
EXPECTED = '8706cfa703015c589ebaec9f50acb354d20b527ddfb15a090b2affa0fc184891'


def invoke_once(prefix, script, remote_command, destination):
    output = destination / (prefix + '.stdout')
    if output.exists():
        raise ValueError('Evidence already exists: refusing another invocation')
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=5',
            'root@192.168.0.188', remote_command]
    raw = script.read_bytes()
    metadata = dict(argv=argv, stdin_script=str(script),
                    stdin_sha256=hashlib.sha256(raw).hexdigest(),
                    start_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    automatic_retries=0)
    # Mark the attempt before launching SSH, so a runner retry cannot resend.
    command_file = destination / (prefix + '.command.json')
    with command_file.open('x') as stream:
        stream.write(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    try:
        result = subprocess.run(argv, input=raw, capture_output=True, timeout=25)
        rc, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        rc, stdout, stderr = 124, exc.stdout or b'', exc.stderr or b''
        stderr += b'\nLocal SSH25s timeout; no retry.\n'
    output.write_bytes(stdout)
    (destination / (prefix + '.stderr')).write_bytes(stderr)
    (destination / (prefix + '.rc')).write_text(str(rc) + '\n')
    metadata.update(end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), rc=rc)
    command_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    return rc, stdout


def main():
    if sys.argv[1:] != ['--parent-authorized-8706cf-once']:
        raise ValueError('Exact parent authorization token required')
    source = BASE / 'readonly_once.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == EXPECTED
    rc, stdout = invoke_once('live-readonly-v2-once', source,
                            'python3 -I -B - --execute-readonly-once', BASE)
    after_rc, after_raw = invoke_once(
        'after-v2-passive', PASSIVE / 'passive.remote.py',
        'python3 -I -B - /var/tmp/km3-pmp4010-alarm-20261019-735538978204', PASSIVE)
    summary = dict(probe_rc=rc, after_passive_rc=after_rc, automatic_retries=0,
                   source_sha256=EXPECTED)
    events = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
    summary['tx_attempts'] = sum(e['event'] == 'tx_attempt' for e in events)
    summary['tx_successes'] = sum(e['event'] == 'tx_sent' for e in events)
    summary['rx_events'] = sum(e['event'] == 'rx' for e in events)
    summary['values'] = [e for e in events if e['event'] == 'value']
    summary['terminal_events'] = [e for e in events if e['event'] in ('ended_without_retry', 'read_only_complete')]
    if after_rc == 0:
        before = json.loads((PASSIVE / 'after-passive-summary.json').read_text())
        after = json.loads(after_raw)
        (PASSIVE / 'after-v2-passive-summary.json').write_text(json.dumps(after, ensure_ascii=False, indent=2) + '\n')
        keys = ('hostname', 'model', 'boot_id', 'device_id', 'config_sha', 'can_env_sha',
                'current', 'services', 'related_processes_excluding_snapshot_pid', 'can_rcvlists')
        summary['passive_equal'] = {key: before[key] == after[key] for key in keys}
        old_link, = json.loads(before['can_link']['stdout'])
        new_link, = json.loads(after['can_link']['stdout'])
        summary['can_state_equal'] = old_link['linkinfo'] == new_link['linkinfo']
        summary['counter_delta'] = {
            side: {name: value - old_link['stats64'][side][name]
                   for name, value in new_link['stats64'][side].items()}
            for side in ('rx', 'tx')}
    (BASE / 'live-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
