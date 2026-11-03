#!/usr/bin/env python3
"""Parse archived JSONL only; no device, network or production imports."""
import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'motion-live' / 'motion-once.stdout'

def main():
    raw = SOURCE.read_bytes()
    events = [(n, json.loads(line)) for n, line in enumerate(raw.decode().splitlines(), 1)]
    assert len(events) == 711
    go = next(e for _, e in events if e['event'] == 'Go观察计时起点')['monotonic_ns']
    review = next(e for _, e in events if e['event'] == '单次冻结审阅')
    batches, pending = [], []
    for line, e in events:
        if e['event'] == '诊断实读':
            assert e['simultaneous_sample'] is False
            pending.append(dict(e, source_line=line,
                                seconds_from_go=(e['monotonic_ns'] - go) / 1e9))
        elif e['event'] == '诊断批次完成':
            assert len(pending) == e['reads']
            assert all(s['phase'] == e['phase'] for s in pending)
            batches.append({'phase': e['phase'], 'ordinal': e['ordinal'],
                            'completion_line': line, 'completion_utc': e['utc'],
                            'completion_seconds_from_go': (e['monotonic_ns'] - go) / 1e9,
                            'elapsed_seconds': e['elapsed_seconds'],
                            'simultaneous_sample': False, 'samples': pending})
            pending = []
        elif e['event'] == '原反馈判据采样':
            assert batches[-1]['phase'] == 'Go后运行'
            batches[-1]['feedback'] = dict(e, source_line=line,
                                           seconds_from_go=(e['monotonic_ns'] - go) / 1e9)
    assert not pending and len(batches) == 21
    assert [b['ordinal'] for b in batches] == list(range(21))
    samples = [s for b in batches for s in b['samples']]
    assert len(samples) == 260
    assert len([b for b in batches if 'feedback' in b]) == 20
    assert all(s['value'] == 0 for s in samples if s['index'] in ('603F', '1001'))
    assert sum(s['index'] == '200B' for s in samples) == 8
    rows = []
    for b in batches:
        for s in b['samples']:
            rows.append({'phase': b['phase'], 'ordinal': b['ordinal'],
                         **{k: s[k] for k in ('source_line', 'utc', 'monotonic_ns',
                            'seconds_from_go', 'index', 'subindex', 'raw_value_hex',
                            'value', 'request_start_monotonic', 'response_monotonic',
                            'simultaneous_sample')}})
    with (HERE / 'samples.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    output = {'source': '../motion-live/motion-once.stdout',
              'source_sha256': hashlib.sha256(raw).hexdigest(), 'raw_json_events': len(events),
              'diagnostic_reads': len(samples), 'batches': batches,
              'unit_caution': '606B/6069/6078 remain raw; rows are sequential SDO samples, not simultaneous.',
              'frozen_start_units': review['start'], 'target_units': review['target'],
              'last_feedback_net_units': batches[-1]['feedback']['position'] - review['start'],
              'last_feedback_net_mm': (batches[-1]['feedback']['position'] - review['start']) / 32768,
              'mechanical_stop_confirmed': False}
    (HERE / 'batches.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
    table = ['# 一次运动的离线逐批汇总', '',
             '来源为原始JSONL；行号从1开始。各对象顺序读取，表格一行不代表同时采样。',
             '完整逐对象UTC、monotonic、请求/应答时刻、raw字节及行号见 samples.csv / batches.json。',
             '606B、6069、6078仅列raw，未将606B当轨迹速度，未将6078换算安培。', '',
             '|批次|完成UTC|Go偏移s|60FC|606B raw|6069 raw|60F4|6077 raw|6078 raw|原反馈位置/速度/6041|原始行|',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
    for b in batches:
        values = {s['index']: s['value'] for s in b['samples']}
        fb = b.get('feedback')
        feedback = f"{fb['position']}/{fb['velocity']}/{fb['statusword']:04X}" if fb else 'Halt无原反馈组'
        cells = [str(b['ordinal']), b['completion_utc'], f"{b['completion_seconds_from_go']:.6f}",
                 *[str(values[k]) for k in ('60FC', '606B', '6069', '60F4', '6077', '6078')],
                 feedback, f"{b['samples'][0]['source_line']}–{b['completion_line']}" +
                 (f"；反馈{fb['source_line']}" if fb else '')]
        table.append('|' + '|'.join(cells) + '|')
    table += ['', '260次读取=Halt 16 + Go首批16 + 其余19批×12；200B:12–15仅Halt和Go首批各一组。',
              '20组原反馈取自各批诊断之前的控制器快照，事件在该批之后输出；事件时间不是6064独立实读时刻。', '']
    (HERE / 'batches.md').write_text('\n'.join(table))
    print(json.dumps({'result': 'PASS', 'raw_json_events': 711, 'batches': 21,
                      'Go_batches': 20, 'diagnostic_reads': 260,
                      'feedback_samples': 20, 'all_603F_1001_zero': True,
                      'last_feedback_net_units': output['last_feedback_net_units'],
                      'last_feedback_net_mm': output['last_feedback_net_mm'],
                      'physical_CAN_opens': 0, 'physical_CAN_sends': 0}, ensure_ascii=False))

if __name__ == '__main__':
    main()
