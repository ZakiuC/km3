"""Compare already captured passive snapshots; no network or hardware calls."""
import hashlib
import json
from pathlib import Path

B = Path(__file__).resolve().parent
keys = ('hostname', 'model', 'boot_id', 'device_id', 'config_sha', 'can_env_sha',
        'current', 'services', 'can_rcvlists', 'related_processes_excluding_snapshot_pid',
        'can_link')
before = json.loads((B / 'before-passive.stdout').read_text())
after = json.loads((B / 'after-passive.stdout').read_text())
assert (B / 'before-passive.rc').read_text().strip() == '0'
assert (B / 'after-passive.rc').read_text().strip() == '0'
equal = {key: before[key] == after[key] for key in keys}
summary = dict(result='PASS' if all(equal.values()) else 'FAIL',
               compared_keys=list(keys), compared_count=len(keys), equal=equal,
               before_utc=before['utc'], after_utc=after['utc'],
               stage_absent_before_present_after=not before['stage_exists'] and after['stage_exists'],
               driver_realtime_state='unknown: no CAN/SDO reads performed')
assert summary['stage_absent_before_present_after'], 'new isolated stage absent/present boundary failed'
(B / 'postcheck-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
assert all(equal.values()), 'passive snapshot fields changed; inspect raw evidence'
manifest = {p.name: dict(size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(B.iterdir()) if p.is_file() and p.name != 'evidence-manifest.json'}
(B / 'evidence-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(summary, indent=2))
