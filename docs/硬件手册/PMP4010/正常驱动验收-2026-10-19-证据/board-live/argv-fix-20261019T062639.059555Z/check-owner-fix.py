"""Only the two requested owner-check cases; mocked proc files, no CAN/SSH."""
import importlib.util
import json
from pathlib import Path
from unittest import mock

base = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('checked_runner', base/'controlled-forward.runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
protocols = 'protocol size sockets memory\nCAN_RAW 1056 0 -1\nCAN_BCM 1072 0 -1\n'

def text(path, *args, **kwargs):
    if str(path) == '/proc/net/protocols':
        return protocols
    if str(path) == '/proc/net/can/rcvlist_all':
        return "receive list 'rx_all':\n  (can1: no entry)\n"
    raise AssertionError('unexpected read: '+str(path))

with mock.patch.object(Path, 'read_text', text), \
     mock.patch.object(Path, 'iterdir', lambda p: iter([])), \
     mock.patch.object(Path, 'glob', lambda p, pattern: iter([Path('/proc/net/can/rcvlist_all')])):
    for phase in (0, 1):
        result = runner.passive_owner_check(phase)
        assert result['protocol_socket_counts_diagnostic_only'] == {'CAN_RAW': 0, 'CAN_BCM': 0}
        assert result['controller_bus_open_phase'] is bool(phase)
        assert not result['related_test_processes']
    print(json.dumps({'case': 'zero-counter-before-open-and-before-execute', 'result': 'PASS'}))

with mock.patch.object(Path, 'read_text', text), \
     mock.patch.object(Path, 'iterdir', lambda p: iter([Path('/proc/987654321')])), \
     mock.patch.object(Path, 'read_bytes', lambda p: b'/usr/bin/candump\0can1\0'):
    try:
        runner.passive_owner_check(1)
    except ValueError as exc:
        assert '存在外部测试进程' in str(exc)
        assert 'candump' in str(exc)
        print(json.dumps({'case': 'known-external-candump-rejected', 'result': 'PASS', 'error': str(exc)}, ensure_ascii=False))
    else:
        raise AssertionError('external tester must still be rejected')

with mock.patch.object(Path, 'read_text', text), \
     mock.patch.object(Path, 'iterdir', lambda p: iter([Path('/proc/987654321')])), \
     mock.patch.object(Path, 'read_bytes', lambda p: b'/usr/bin/python3\0-I\0-B\0/path/controlled-forward.runner.py\0--mode\0live\0'):
    try:
        runner.passive_owner_check(1)
    except ValueError as exc:
        assert '存在外部测试进程' in str(exc)
        assert 'controlled-forward.runner.py' in str(exc)
        print(json.dumps({'case': 'external-python3-I-B-controlled-runner-rejected', 'result': 'PASS', 'error': str(exc)}, ensure_ascii=False))
    else:
        raise AssertionError('external isolated Python runner must be rejected')
