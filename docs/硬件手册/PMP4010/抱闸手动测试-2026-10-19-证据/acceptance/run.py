#!/usr/bin/env python3
"""Record a literal acceptance command, raw streams, times, rc and frozen hashes."""
import datetime, hashlib, json, pathlib, subprocess, sys, time
base = pathlib.Path(__file__).resolve().parent
label, sep, *command = sys.argv[1:]
assert sep == '--' and command
manifest = json.loads((base.parent/'implementation/source-sha256-final.json').read_text())
def hashes():
    return {p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in manifest}
start = datetime.datetime.now(datetime.timezone.utc).isoformat()
before = hashes()
t0 = time.monotonic()
with (base/'raw'/f'{label}.stdout').open('wb') as out, (base/'raw'/f'{label}.stderr').open('wb') as err:
    result = subprocess.run(command, stdout=out, stderr=err)
after = hashes()
record = dict(command=command, cwd=str(pathlib.Path.cwd()), start_utc=start,
              end_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
              elapsed_seconds=time.monotonic()-t0, rc=result.returncode,
              source_before=before, source_after=after, expected=manifest,
              frozen_hashes_match=before == after == manifest)
(base/'raw'/f'{label}.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record, ensure_ascii=False))
print((base/'raw'/f'{label}.stderr').read_text(errors='replace')[-1800:])
sys.exit(result.returncode if record['frozen_hashes_match'] else 99)
