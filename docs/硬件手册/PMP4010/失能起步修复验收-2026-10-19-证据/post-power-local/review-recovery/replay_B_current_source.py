import sys, pathlib
sys.dont_write_bytecode = True
path = pathlib.Path('docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/stage2-local-acceptance/review-message-transfer/replay.py')
source = path.read_text()
old = "manifest = Path('/tmp/km3-pp-disabled-start-20261019-impl/switched-on-stage2/freeze/source-sha256.json')\nexpected = json.loads(manifest.read_text())['files']"
new = "manifest = Path('docs/硬件手册/PMP4010/失能起步修复验收-2026-10-19-证据/stage2-local-acceptance/freeze-source-sha256.json')\nexpected = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in json.loads(manifest.read_text())['files']}"
assert source.count(old) == 1
source = source.replace(old, new)
exec(compile(source, str(path) + ':in-memory-current-source-adaptation', 'exec'), {'__name__': '__main__'})
