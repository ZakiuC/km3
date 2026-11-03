"""Validate an explicitly approved archive before exclusive isolated extraction.

Prepared locally only. Running this writes the new stage; it never imports or
executes package code, opens CAN, changes configuration, or controls processes.
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import sys
import tarfile

expected_sha, stage_name = sys.argv[1:]
assert len(expected_sha) == 64 and all(c in '0123456789abcdef' for c in expected_sha)
base = Path(stage_name)
prefix = 'km3-pmp4010-state-20261019-'
assert base.parent == Path('/var/tmp') and base.name.startswith(prefix)
suffix = base.name[len(prefix):]
assert 12 <= len(suffix) <= 64 and expected_sha.startswith(suffix)
assert Path('/var/tmp').resolve(strict=True) == Path('/var/tmp')
assert not base.exists() and not base.is_symlink(), 'stage already exists; do not overwrite'
raw = sys.stdin.buffer.read()
assert hashlib.sha256(raw).hexdigest() == expected_sha, 'archive SHA mismatch'
archive = tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz')
members = archive.getmembers()
by_name = {}
for member in members:
    path = PurePosixPath(member.name)
    assert not path.is_absolute() and '..' not in path.parts
    assert str(path) == member.name.rstrip('/') and path.parts
    assert member.isfile() or member.issym() or member.isdir(), 'unsupported tar member'
    assert str(path) not in by_name, 'duplicate tar member'
    by_name[str(path)] = member
file_names = {name for name, member in by_name.items() if not member.isdir()}
manifest_raw = archive.extractfile(by_name['payload-manifest.json']).read()
manifest = json.loads(manifest_raw)
items = manifest['files']
assert len({item['path'] for item in items}) == len(items), 'duplicate manifest path'
assert file_names == {item['path'] for item in items} | {'payload-manifest.json'}
for item in items:
    member = by_name[item['path']]
    if 'symlink' in item:
        assert member.issym() and member.linkname == item['symlink']
        seen = {item['path']}
        target = item['path']
        while by_name[target].issym():
            link = by_name[target].linkname
            assert not PurePosixPath(link).is_absolute()
            target = posixpath.normpath(posixpath.join(posixpath.dirname(target), link))
            assert target in file_names and target not in seen, 'escaping/cyclic symlink'
            seen.add(target)
        assert by_name[target].isfile()
    else:
        assert member.isfile()
        data = archive.extractfile(member).read()
        assert len(data) == item['size']
        assert hashlib.sha256(data).hexdigest() == item['sha256']
for name in file_names:
    parents = PurePosixPath(name).parents
    assert all(str(parent) not in file_names for parent in parents), 'file used as parent'
required = {'load_smoke.py', 'run-manual.sh', 'drivers/pmp4010/pmp4010.py',
            'drivers/pmp4010/pmp4010_manual.py', 'drivers/pmp4010/pmp4010_simple.py'}
assert required <= file_names, 'manual package schema incomplete'
# All archive bytes, manifest entries and symlink paths are checked before writes.
base.mkdir(mode=0o700, exist_ok=False)
(base / 'frozen-payload.tar.gz').write_bytes(raw)
root = base / 'payload'
root.mkdir(mode=0o700)
for name, member in by_name.items():
    if member.isfile():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as stream:
            stream.write(archive.extractfile(member).read())
        dest.chmod(0o755 if member.mode & 0o111 else 0o644)
for name, member in by_name.items():
    if member.issym():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(member.linkname, dest)
for item in items:
    dest = root / item['path']
    assert dest.resolve(strict=True).is_relative_to(root.resolve(strict=True))
    if 'symlink' in item:
        assert dest.is_symlink() and os.readlink(dest) == item['symlink']
    else:
        assert dest.stat().st_size == item['size']
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == item['sha256']
print(json.dumps(dict(result='PASS',stage=str(base),payload_root=str(root),
                     archive_sha256=expected_sha,manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                     verified_manifest_entries=len(items),archive_members=len(members),
                     CAN_opens=0,CAN_sends=0,payload_executed=False)))
