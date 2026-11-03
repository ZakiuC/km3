#!/usr/bin/env python3
"""Build only after parent-provided brake source freeze and local acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
BASE = REPO / 'docs/硬件手册/PMP4010/报警根因诊断-2026-10-19-证据/product-package'
ARCHIVE_SHA = 'ca634a034cc538a5a0eee819404404da89a950a803a1b4a6d29c9aabd85e5ad2'
MANIFEST_SHA = '0922f0ecacd08f856b2c213e5c737e78fa41c005917f8584c2ad7d5d85f5d3f0'
ARM_SHA = '59ab5e0554b12cc21d47ea8b8113387bd4c02c0766ada28def0051986b726404'
SOURCE = 'device/drivers/pmp4010/tools/pmp4010_brake.py'
TEST = 'device/drivers/pmp4010/tools/_test_brake.py'
UNCHANGED_SOURCES = {
    'device/drivers/pmp4010/tools/pmp4010_manual.py': 'ca37ecb04116d8a08b279f1ea9964c9374dda411504c4e91479bc5bf09c110b9',
    'device/drivers/pmp4010/tools/pmp4010_simple.py': 'd1d277431b014fcc888dc16fa994a8b16f2d7e060f384f55c1db431317a166f5',
}
COMMANDS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def check_freeze(freeze):
    require(set(freeze['source_hashes']) == {SOURCE, TEST}, 'require exact brake source + test freeze')
    for path, digest in (freeze['source_hashes'] | UNCHANGED_SOURCES).items():
        require(sha(REPO / path) == digest, 'source freeze mismatch: ' + path)
    require(freeze['local_acceptance'].get('status') == 'passed' and freeze['local_acceptance'].get('evidence'), 'local acceptance evidence required')


def inventory(directory, entries, regular):
    paths = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() or p.is_symlink()}
    require(paths == {x['path'] for x in entries} | {'payload-manifest.json'}, 'inventory paths mismatch')
    require(sum('symlink' not in x for x in entries) + 1 == regular and sum('symlink' in x for x in entries) == 2, 'wrong file counts')
    for item in entries:
        path = directory / item['path']
        require(not Path(item['path']).is_absolute() and '..' not in Path(item['path']).parts, 'unsafe path')
        if 'symlink' in item:
            require(path.is_symlink() and os.readlink(path) == item['symlink'], 'symlink drift')
        else:
            require(not path.is_symlink() and sha(path) == item['sha256'], 'file hash drift: ' + str(path))


def run(name, argv, expected=0):
    result = subprocess.run(argv, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (ROOT / (name + '.stdout.log')).write_bytes(result.stdout)
    (ROOT / (name + '.stderr.log')).write_bytes(result.stderr)
    COMMANDS.append({'name': name, 'argv': argv, 'cwd': str(REPO), 'returncode': result.returncode, 'expected_returncode': expected})
    write_json(ROOT / 'command-index.json', COMMANDS)
    require(result.returncode == expected, name + ': unexpected returncode')
    return result.stdout.decode()


def coldload(name, path):
    output = run(name, [sys.executable, '-I', '-B', str(path / 'load_smoke.py'), '--library', str(REPO / 'build-host/libpmp4010_velocity.so'), '--check-missing-library'])
    stats = json.loads(output.strip().splitlines()[-1])
    require(output.count('import=') == 20, 'require 20 package imports')
    require(stats == {'result': 'PASS', 'native_vectors': 33, 'python_vectors': 33, 'native_int32_vectors': 14, 'forbidden_io_attempts': 0, 'hardware_acceptance': False}, 'coldload vector/safety checks mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-input', type=Path, required=True)
    args = parser.parse_args()
    freeze_path = args.freeze_input.resolve(strict=True)
    freeze = json.loads(freeze_path.read_text())
    require(REPO == Path('/home/zakiu/km3'), 'wrong checkout')
    check_freeze(freeze)
    require(sha(BASE / 'pmp4010-manual-reset-offline-payload.tar.gz') == ARCHIVE_SHA, 'baseline archive changed')
    require(sha(BASE / 'payload/payload-manifest.json') == MANIFEST_SHA, 'baseline manifest changed')
    old = json.loads((BASE / 'payload/payload-manifest.json').read_text())
    inventory(BASE / 'payload', old['files'], 23)
    payload, roundtrip = ROOT / 'payload', ROOT / 'roundtrip'
    require(not payload.exists() and not roundtrip.exists(), 'preserve existing outputs; do not overwrite')
    payload.mkdir()
    entries = []
    for item in old['files']:
        dest = payload / item['path']
        dest.parent.mkdir(parents=True, exist_ok=True)
        record = {'path': item['path'], 'provenance': 'unchanged trusted manual-reset baseline'}
        if 'symlink' in item:
            dest.symlink_to(item['symlink'])
            record['symlink'] = item['symlink']
        elif item['path'] == 'load_smoke.py':
            content = (BASE / 'payload/load_smoke.py').read_text()
            needle = "'pmp4010_faults')"
            require(content.count(needle) == 1, 'unexpected loader import tuple')
            dest.write_text(content.replace(needle, "'pmp4010_faults', 'pmp4010_brake')"))
            record['provenance'] = 'baseline loader; only added pmp4010_brake import under same I/O audit'
            record['baseline_sha256'] = item['sha256']
        else:
            shutil.copy2(BASE / 'payload' / item['path'], dest)
        if 'symlink' not in record:
            record.update(sha256=sha(dest), size=dest.stat().st_size)
        entries.append(record)
    for relative, source in [('drivers/pmp4010/pmp4010_brake.py', REPO / SOURCE), ('run-brake.sh', ROOT / 'run-brake.sh'), ('README-抱闸测试.md', ROOT / 'README-抱闸测试.md')]:
        dest = payload / relative
        shutil.copy2(source, dest)
        entries.append({'path': relative, 'sha256': sha(dest), 'size': dest.stat().st_size, 'provenance': str(source.relative_to(REPO))})
    head = run('source-head', ['git', 'rev-parse', 'HEAD']).strip()
    manifest = {'stage': 'manual_brake_sound_local_candidate_2026_10_19', 'source_head': head,
                'source_freeze': freeze['source_hashes'], 'unchanged_manual_simple': UNCHANGED_SOURCES,
                'freeze_input_sha256': sha(freeze_path), 'local_acceptance': freeze['local_acceptance'],
                'baseline_archive_sha256': ARCHIVE_SHA, 'baseline_manifest_sha256': MANIFEST_SHA,
                'files': entries, 'arm_execution': False, 'hardware_acceptance': False,
                'arm_board_loading': 'pending human operation', 'mechanical_brake_effect': 'pending human listening', 'physical_stall_resolved': False}
    write_json(payload / 'payload-manifest.json', manifest)
    inventory(payload, entries, 26)
    require(sha(payload / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0') == ARM_SHA, 'ARM library changed')
    run('launcher-shell-syntax', ['sh', '-n', str(payload / 'run-brake.sh')])
    run('launcher-help', ['sh', str(payload / 'run-brake.sh'), '--help'])
    run('launcher-no-tty', ['sh', str(payload / 'run-brake.sh')], expected=2)
    run('launcher-parameter-refused', ['sh', str(payload / 'run-brake.sh'), '--node', '2'], expected=2)
    coldload('host-load-smoke', payload)
    check_freeze(freeze)
    archive = ROOT / 'pmp4010-brake-offline-payload.tar.gz'
    members = [x['path'] for x in entries] + ['payload-manifest.json']
    with tarfile.open(archive, 'w:gz', dereference=False) as tar:
        for relative in members:
            tar.add(payload / relative, arcname=relative, recursive=False)
    roundtrip.mkdir()
    with tarfile.open(archive) as tar:
        require(len(tar.getmembers()) == 28, 'archive count mismatch')
        tar.extractall(roundtrip, filter='data')
    inventory(roundtrip, entries, 26)
    require(sha(roundtrip / 'payload-manifest.json') == sha(payload / 'payload-manifest.json'), 'manifest roundtrip mismatch')
    coldload('roundtrip-smoke', roundtrip)
    run('archive-list', ['tar', '-tzf', str(archive)])
    check_freeze(freeze)
    shutil.copy2(freeze_path, ROOT / 'source-freeze-input.json')
    write_json(ROOT / 'source-manifest.json', manifest)
    archive_members = entries + [{'path': 'payload-manifest.json', 'sha256': sha(payload / 'payload-manifest.json'), 'size': (payload / 'payload-manifest.json').stat().st_size}]
    summary = {'archive': str(archive), 'archive_sha256': sha(archive), 'archive_bytes': archive.stat().st_size,
               'manifest_sha256': sha(payload / 'payload-manifest.json'), 'regular_files': 26, 'symlinks': 2,
               'imports': 20, 'native_vectors': 33, 'python_vectors': 33, 'int32_vectors': 14, 'forbidden_io_attempts': 0,
               'arm_library_sha256': ARM_SHA, 'arm_execution': False, 'hardware_acceptance': False,
               'arm_board_loading': 'pending human operation', 'mechanical_brake_effect': 'pending human listening'}
    write_json(ROOT / 'archive-manifest.json', {'archive': archive.name, 'sha256': sha(archive), 'members': archive_members})
    write_json(ROOT / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
