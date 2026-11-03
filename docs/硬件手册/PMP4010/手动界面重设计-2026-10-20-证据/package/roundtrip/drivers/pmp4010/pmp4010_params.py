"""Offline plans and guarded parameter transactions; SDO wire belongs to hwtui.

Object addresses, signed widths and legal ranges come from driver/od.json.
No function here opens hardware. The CLI supplies one exclusively owned client.
"""
import datetime
import json
from pathlib import Path
import shlex


class ParameterError(ValueError):
    pass


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError('必须为整数（不接受布尔/小数）')
    return value


def object_key(index, sub):
    return '{:04X}:{:02X}'.format(index, sub)


TYPES = {'uint8': (1, False), 'int8': (1, True),
         'uint16': (2, False), 'int16': (2, True),
         'uint32': (4, False), 'int32': (4, True)}


def specifications(od):
    result = {}
    for entry in od['entries']:
        if 'parameter_objects' not in entry:
            continue
        if entry['status'] != 'verified' or entry['access'] != 'rw':
            raise ParameterError('参数声明必须来自 verified/rw 条目')
        for spec in entry['parameter_objects']:
            index = int(spec['index'], 0)
            sub = integer(spec['sub'])
            if not 1 <= index <= 0xffff or not 0 <= sub <= 255:
                raise ParameterError('非法对象地址')
            key = object_key(index, sub)
            if key in result or spec['type'] not in TYPES:
                raise ParameterError('重复对象或不支持的数据类型')
            size, signed = TYPES[spec['type']]
            low, high = (-(1 << (size*8-1)), (1 << (size*8-1))-1) if signed else (0, (1 << (size*8))-1)
            ranges = spec['ranges']
            if not isinstance(ranges, list) or not ranges:
                raise ParameterError('缺少OD合法范围')
            for pair in ranges:
                if (not isinstance(pair, list) or len(pair) != 2 or
                        not low <= integer(pair[0]) <= integer(pair[1]) <= high):
                    raise ParameterError('OD范围超出数据宽度')
            if spec['group'] not in ('interpolation', 'transmission', 'save') or spec['role'] not in ('parameter', 'command'):
                raise ParameterError('非法参数分组/角色')
            if (spec['group'] == 'save') != (spec['role'] == 'command'):
                raise ParameterError('保存对象必须为命令')
            result[key] = dict(spec, index_value=index, size=size, signed=signed)
    if not result:
        raise ParameterError('缺少结构化参数声明')
    return result


def encode(spec, value, check_range=True):
    integer(value)
    try:
        data = value.to_bytes(spec['size'], 'little', signed=spec['signed'])
    except OverflowError as exc:
        raise ParameterError('值超出OD宽度: {}'.format(value)) from exc
    if check_range and not any(low <= value <= high for low, high in spec['ranges']):
        raise ParameterError('值不在OD合法范围: {}'.format(value))
    return data


def read_value(client, spec):
    raw = bytes(client.read(spec['index_value'], spec['sub']))
    if len(raw) != spec['size']:
        raise ParameterError('{} SDO读回宽度{}与OD{}不符'.format(
            object_key(spec['index_value'], spec['sub']), len(raw), spec['size']))
    return int.from_bytes(raw, 'little', signed=spec['signed'])


def identity(client, od):
    entry = next(e for e in od['entries'] if e['key'] == 'identity_object')
    index = int(entry['index'], 0)
    result = {}
    for sub in entry['identity_subindices']:
        raw = bytes(client.read(index, sub))
        if len(raw) != 4:
            raise ParameterError('1018身份字段宽度不符')
        result[str(sub)] = int.from_bytes(raw, 'little')
    if set(result) != {'1', '2', '3', '4'}:
        raise ParameterError('缺少身份四字段')
    return result


def capture(client, od, specs, iface, bitrate, node):
    values = {}
    who = identity(client, od)
    for key, spec in specs.items():
        value = read_value(client, spec)
        values[key] = dict(type=spec['type'], value=value,
                           raw_hex=encode(spec, value, spec['role'] != 'command').hex())
    return dict(schema='km3.pmp4010.parameters.v1',
                timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                model='pmp4010', iface=iface, bitrate=bitrate, node=node,
                identity=who, objects=values)


def validate_backup(data, specs):
    if not isinstance(data, dict) or data.get('schema') != 'km3.pmp4010.parameters.v1' or data.get('model') != 'pmp4010':
        raise ParameterError('不支持的备份格式')
    try:
        stamp = datetime.datetime.fromisoformat(data['timestamp_utc'])
        if stamp.utcoffset() != datetime.timedelta(0):
            raise ParameterError('备份时间必须为UTC')
        if not isinstance(data['iface'], str) or not data['iface'].strip():
            raise ParameterError('备份缺少接口')
        if integer(data['bitrate']) <= 0 or not 1 <= integer(data['node']) <= 127:
            raise ParameterError('备份位速率/节点非法')
        if set(data['identity']) != {'1', '2', '3', '4'}:
            raise ParameterError('备份身份四字段不完整')
        for value in data['identity'].values():
            if not 0 <= integer(value) <= 0xffffffff:
                raise ParameterError('备份身份值越界')
        if set(data['objects']) != set(specs):
            raise ParameterError('备份必须完整包含全部待改对象和1010状态')
        for key, spec in specs.items():
            row = data['objects'][key]
            raw = encode(spec, row['value'], spec['role'] != 'command')
            if row['type'] != spec['type'] or row['raw_hex'] != raw.hex():
                raise ParameterError('备份数值/类型/原始字节不一致: '+key)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ParameterError('备份缺少合法字段') from exc
    return data


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ParameterError('备份JSON存在重复字段: ' + key)
        result[key] = value
    return result


def load_backup(path, specs):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError) as exc:
        raise ParameterError('无法读取备份: {}'.format(exc)) from exc
    return validate_backup(data, specs)


def save_backup(path, data, specs):
    validate_backup(data, specs)
    # Exclusive creation: a backup may never overwrite the only recovery copy.
    with open(path, 'x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def interpolation_values(period_us):
    integer(period_us)
    if not 1 <= period_us <= 1000000:
        raise ParameterError('sync_period_us必须为1..1000000整数')
    # Express microseconds as t * 10**n seconds, without floating point.
    t, exponent = period_us, -6
    while t % 10 == 0:
        t //= 10
        exponent += 1
    if t > 255:
        raise ParameterError('配置周期不能精确表达为Uint8 t × 10^Int8 n')
    return t, exponent


def plan(action, specs, backup=None, period_us=None, overrides=()):
    if action == 'backup':
        if overrides:
            raise ParameterError('backup不可写入')
        return []
    if action == 'rollback':
        if backup is None or overrides:
            raise ParameterError('rollback须读备份，不接受--set')
        result = [(key, backup['objects'][key]['value']) for key, spec in specs.items()
                  if spec['role'] != 'command']
    elif action == 'save':
        if overrides:
            raise ParameterError('save必须独立，不接受--set')
        candidates = [(key, spec) for key, spec in specs.items()
                      if spec['group'] == 'save' and spec['sub'] == 1]
        if len(candidates) != 1:
            raise ParameterError('缺少唯一保存命令声明')
        key, spec = candidates[0]
        result = [(key, spec['ranges'][0][0])]
    else:
        group = [(key, spec) for key, spec in specs.items() if spec['group'] == action]
        if action == 'interpolation':
            t, exponent = interpolation_values(period_us)
            result = [(key, t if spec['sub'] == 1 else exponent) for key, spec in group]
        elif action == 'transmission':
            result = [(key, 0) for key, _ in group]
        else:
            raise ParameterError('未知参数操作')
        if overrides:
            result = []
            for text in overrides:
                try:
                    address, value = text.split('=')
                    index, sub = address.split(':')
                    key = object_key(int(index, 16), int(sub, 16))
                    value = int(value, 0)
                except (ValueError, TypeError) as exc:
                    raise ParameterError('--set格式应为60C2:02=-3') from exc
                if key not in specs or specs[key]['group'] != action:
                    raise ParameterError('--set不得跨批次或写命令对象')
                result.append((key, value))
    if not result or len({key for key, _ in result}) != len(result):
        raise ParameterError('空计划或重复写入')
    # Validate the ENTIRE batch before the first bus open or SDO upload.
    for key, value in result:
        encode(specs[key], value)
    return result


def check_target(backup, iface, bitrate, node):
    if (backup['iface'], backup['bitrate'], backup['node']) != (iface, bitrate, node):
        raise ParameterError('备份接口/位速率/节点与目标不匹配')


def confirmation(backup):
    return 'SAVE {} {} {}'.format(backup['iface'], backup['node'], backup['identity']['4'])


def require_disabled(client, od):
    """Passive upload: OD statusword cites E06 10.3 table 10-7 (Uint16);
    OD statusword_bits cites E06 4.1.3 table 4-4 (bit2 operation enabled).
    """
    entry = next(e for e in od['entries'] if e['key'] == 'statusword')
    if entry['status'] != 'verified' or entry['type'] != 'uint16':
        raise ParameterError('状态字缺少已核对的Uint16声明，零写入')
    status = read_value(client, dict(index_value=int(entry['index'], 0),
                                    sub=entry['sub'], size=2, signed=False))
    if status & (1 << 2):
        raise ParameterError('电机处于使能状态，请先失能')


def restore_attempted(client, od, specs, attempted, before, out):
    """Restore even the failed download: it may have taken effect without ACK.

    Keep the same client and its quarantine. Never recover the SDO stream here,
    retry a failed restore, or turn a RAM recovery into a save command.
    """
    remaining = list(reversed(attempted))
    failures = []
    if client.quarantined:
        return ['SDO会话已隔离，禁止继续发帧；{} 当前值未知'.format(', '.join(remaining))]
    try:
        require_disabled(client, od)
    except (Exception, KeyboardInterrupt) as exc:
        return ['自动回滚前状态检查失败: {}；{} 当前值未知'.format(exc, ', '.join(remaining))]
    for position, key in enumerate(remaining):
        spec = specs[key]
        actual = '未知'
        try:
            client.write(spec['index_value'], spec['sub'], encode(spec, before[key]))
            actual = read_value(client, spec)
            if actual != before[key]:
                raise ParameterError('恢复写读不一致')
            out.info('{} 自动回滚读回确认={}'.format(key, actual))
        except (Exception, KeyboardInterrupt) as exc:
            failures.append('{} 恢复期望{}，读回{}: {}'.format(key, before[key], actual, exc))
            if client.quarantined or isinstance(exc, KeyboardInterrupt):
                if remaining[position+1:]:
                    failures.append('停止恢复；{} 当前值未知'.format(', '.join(remaining[position+1:])))
                break
    return failures


def execute(client, od, specs, backup, writes, action, out, backup_path=None):
    def event(name, **data):
        if hasattr(out, 'event'):
            out.event(name, data)

    def cancelled():
        if hasattr(out, 'check_cancel'):
            out.check_cancel()

    cancelled()
    if identity(client, od) != backup['identity']:
        raise ParameterError('1018身份四字段与备份不匹配，零写入')
    require_disabled(client, od)
    # Snapshot the entire batch before any download. Recovery uses this invocation's
    # live values, which may differ from the older human-supplied backup.
    before = {}
    for key, value in writes:
        spec = specs[key]
        before[key] = read_value(client, spec)
        out.info('{} pre-read={}'.format(key, before[key]))
        if spec['role'] == 'command' and before[key] != 1:
            raise ParameterError('保存状态不是1，拒绝写保存命令')
    attempted = []
    try:
        for key, value in writes:
            spec = specs[key]
            actual = '未知'
            expected = 1 if spec['role'] == 'command' else value
            cancelled()
            attempted.append(key)  # A failed/aborted download may already have changed RAM.
            client.write(spec['index_value'], spec['sub'], encode(spec, value))
            actual = read_value(client, spec)
            if actual != expected:
                raise ParameterError('写读不一致')
            out.ok('{} readback={}'.format(key, actual))
            event('parameter_readback', object=key, value=actual)
    except (Exception, KeyboardInterrupt) as exc:
        original = '{} 失败: 期望{}，读回{}；原始错误: {}'.format(key, expected, actual, exc)
        out.bad(original)
        if not attempted:
            event('parameter_recovery', recovery='no_writes')
            detail = '取消发生在首项下载前；未尝试写入。'
        elif action == 'save':
            event('parameter_recovery', recovery='unknown', persistent=True)
            detail = '保存命令可能已生效，持久保存结果未知且不可自动撤销；不重试、不自动回滚或save，需人工独立核验。'
        else:
            failures = restore_attempted(client, od, specs, attempted, before, out)
            if failures:
                event('parameter_recovery', recovery='unknown', failures=failures)
                recovery = shlex.quote(str(backup_path)) if backup_path is not None else '<file>'
                detail = ('设备处于混合状态或写入结果未知；{}；需人工先审阅 --params rollback --backup {}，'
                          '确认外部恢复及失能后另行授权 --apply；未自动save。').format('; '.join(failures), recovery)
            else:
                event('parameter_recovery', recovery='restored_confirmed')
                detail = '已自动回滚本次全部已尝试参数并读回确认到本次pre-read值；未自动save。'
        if isinstance(exc, KeyboardInterrupt):
            out.bad(detail)
            raise
        raise ParameterError(original + '；' + detail) from exc
