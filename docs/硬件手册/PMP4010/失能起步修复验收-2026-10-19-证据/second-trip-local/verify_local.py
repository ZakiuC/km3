"""Read persisted evidence only; do not run tests, board tools, or CAN IO."""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
BASE = OUT.parent
ROOT = BASE.parents[3]
LOCAL = BASE / 'post-power-local'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path):
    return json.loads(path.read_text())

freeze = load(LOCAL / 'freeze-source-sha256.json')
source = [{'path': p, 'expected': h, 'actual': sha(ROOT / p)}
          for p, h in freeze['files'].items()]
index = load(LOCAL / 'index-final.json')
records = []
for r in index['records'] + index.get('additional_records', []):
    checks = {}
    for stream in ('stdout', 'stderr'):
        p = LOCAL / r[stream]
        checks[stream] = {'path': str(p.relative_to(BASE)),
                          'exists': p.is_file(),
                          'sha256_match': p.is_file() and sha(p) == r[stream + '_sha256']}
    checks['metadata_present'] = all((LOCAL / (r['name'] + '.' + ext)).is_file()
                                     for ext in ('command.json', 'result.json', 'exitcode'))
    checks['record_rc'] = r['returncode']
    checks['saved_rc'] = int((LOCAL / (r['name'] + '.exitcode')).read_text())
    records.append({'name': r['name'], **checks})
artifacts = []
for r in load(LOCAL / 'artifact-index-final.json')['files']:
    p = LOCAL / r['path']
    artifacts.append({'path': r['path'], 'exists': p.is_file(),
                      'expected': r['sha256'], 'actual': sha(p) if p.is_file() else None})
session = BASE / 'post-power-board/board-move-session-1'
session_files = [{ 'path': str(p.relative_to(BASE)), 'bytes': p.stat().st_size,
                   'sha256': sha(p)} for p in sorted(session.iterdir()) if p.is_file()]
keys = [json.loads(line) for line in (session / 'keys.jsonl').read_text().splitlines()]
package = BASE / 'stage3-recovered-package/pmp4010-board-payload.tar.gz'
result = {
    'checked_utc': datetime.now(timezone.utc).isoformat(),
    'route': {'requested_role': 'light_worker', 'requested_model': 'gpt-6.1-sol',
              'requested_effort': 'medium', 'actual_model': 'unknown',
              'actual_effort': 'unknown', 'last_token_usage_input_tokens': 'unknown'},
    'freeze_manifest_sha256': sha(LOCAL / 'freeze-source-sha256.json'),
    'source': source, 'records': records, 'artifact_checks': artifacts,
    'package': {'bytes': package.stat().st_size, 'sha256': sha(package)},
    'session_files': session_files, 'last_keys': keys[-3:],
    'motion_result': 'unknown; persisted screen remains running, no recovered motion journal',
    'expected_behavior': 'retained command raw hashes and frozen source remain unchanged',
    'artifact_index_stale_entries': [r['path'] for r in artifacts if r['actual'] != r['expected']],
}
(OUT / 'integrity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
assert all(r['expected'] == r['actual'] for r in source)
assert all(r['metadata_present'] and r['record_rc'] == r['saved_rc'] == 0
           and r['stdout']['sha256_match'] and r['stderr']['sha256_match'] for r in records)
assert result['artifact_index_stale_entries'] == ['index-final.json']
assert result['package'] == {'bytes': 193340, 'sha256': 'b70396b228cb594793b44ad6d6f4dfc65a0167fd0efd90325d8feec276f69c5e'}
print(f"Frozen source {len(source)}/{len(source)}; command/raw records {len(records)}/{len(records)} matched")
print(f"Old artifact index: {len(artifacts)-1}/{len(artifacts)} matched; index-final.json stale hash retained")
print('Package 193340 bytes/SHA matched; motion result remains unknown')
