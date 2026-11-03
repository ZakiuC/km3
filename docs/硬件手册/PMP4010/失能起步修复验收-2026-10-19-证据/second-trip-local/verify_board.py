"""Check saved board logs only; this script opens no device or network."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
BOARD = OUT.parent / 'second-trip-board'

def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

motion_path = next(BOARD.glob('immediately-after-once-pmp4010-motion-*.jsonl'))
motion = rows(motion_path)
audit_path = BOARD / 'after-q-final-app-audit.jsonl'
audit = rows(audit_path)
downloads = [r for r in audit if r['event'] == 'sdo_download_begin']
value = lambda r: int.from_bytes(bytes.fromhex(r['raw_hex']), 'little', signed=True)
controls = [value(r) for r in downloads if r['index'] == 0x6040]
targets = [value(r) for r in downloads if r['index'] == 0x607A]
assert controls == [0x106, 0x107, 0x127, 0x137, 0x127, 0x12F, 2, 0, 6]
assert targets == [-1470]
halt = next(r for r in downloads if r['index'] == 0x6040 and value(r) == 0x12F)
first = next(r for r in audit if r['event'] == 'sdo_upload_done'
             and r['index'] == 0x6064 and r['monotonic'] > halt['monotonic'])
assert value(first) == -1457
result = next(r for r in motion if r['event'] == 'result')
assert result['completed'] is False and result['cleanup_confirmed'] is True
assert result['halt_release_attempted'] is False and result['travel_trigger_attempted'] is False
cleanup = next(r for r in motion if r['event'] == 'after_cleanup')
assert (cleanup['statusword'], cleanup['actual_position'], cleanup['actual_velocity']) == (0x240, -1467, 0)
stable = [r for r in motion if r.get('phase', '').startswith('Halt保持核证采样')]
assert len(stable) == 5
assert all(r['values']['actual_position'] == r['values']['position_demand'] ==
           r['values']['target_position'] == -1470 and r['values']['actual_velocity'] == 0
           and not r['values']['statusword'] & 4 for r in stable)
exit_result = json.loads((BOARD / 'board-move-session-1/exit.json').read_text())
assert exit_result['child_rc'] == 0 and exit_result['forced_kill'] is False
post = json.loads((BOARD / 'postcheck-summary.json').read_text())
assert not post['passive_changed_keys'] and not post['related_runners']
assert post['last_uploads']['6041:00']['signed'] == 0x231
summary = {
    'scope': 'saved local files only; no fresh board IO',
    'controls_hex': [hex(x) for x in controls], 'actual_target_downloads': targets,
    'preload_stable_samples': len(stable),
    'first_post_12f_position': value(first), 'first_offset_u': 13,
    'first_read_ms_after_12f': (first['monotonic'] - halt['monotonic']) * 1000,
    'cleanup': cleanup, 'result': result, 'q_exit': exit_result,
    'raw_sha256': {str(p.relative_to(OUT.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [motion_path, audit_path, BOARD / 'postcheck-summary.json']},
}
(OUT / 'board-review.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print('Saved logs: preload 5/5; first post-12F offset +13U; action failed; cleanup strict-disabled')
print('No final target/002F/003F; q rc0/Ready 0x0231; passive_changed_keys=[]')
