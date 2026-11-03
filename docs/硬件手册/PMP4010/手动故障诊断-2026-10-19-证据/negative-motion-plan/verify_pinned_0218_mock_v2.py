"""Isolated Mock acceptance of the frozen wrappers against manifest-pinned 0218.

Production Python modules come only from the verified payload. Existing workspace
test fixtures are reused without edits. The sole native substitution is the
already-built host velocity ABI library; no ARM execution or hardware acceptance.
"""
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
ROOT = (HERE.parent / 'package/payload').resolve(strict=True)
MANIFEST_SHA = 'a41b88269819e718e1486876be8d99502a773a401ef084343b57d597fb818b71'
FROZEN = {
    'pinned_payload.py': 'a742ff99e107bc056bd33a74d1a103e1d83c500609344ac3d01045b25ebd841c',
    'relative_minus10_once.py': '04a97fd5118b9caa0130b3c7c232770b4d26fcd69dc82e1f2d1bc68bb60630d0',
    'reset_only_once.py': '6176b8d66ab93fcb232aed8bb15e095bfc05bb27fe7b43de0be392dfe5ec021d',
    'test_prepared.py': '4aacfeac4d828993c3ef017e240e285f2dd7ec59264307cded1e2451ab408508',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def offline_only(event, args):
    if event in ('socket.__new__', 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
        raise AssertionError('pinned Mock forbids real socket/process creation: ' + event)


def main():
    sys.addaudithook(offline_only)
    manifest_path = ROOT / 'payload-manifest.json'
    assert sha(manifest_path) == MANIFEST_SHA, '0218 manifest changed'
    manifest = json.loads(manifest_path.read_bytes())
    expected = {}
    for item in manifest['files']:
        path = ROOT / item['path']
        assert path.resolve(strict=True).is_relative_to(ROOT), str(path)
        if 'symlink' in item:
            assert path.is_symlink() and path.readlink().as_posix() == item['symlink']
        else:
            assert sha(path) == item['sha256'], 'payload member changed: ' + str(path)
            expected[path.resolve()] = item['sha256']
    for name, digest in FROZEN.items():
        assert sha(HERE / name) == digest, 'frozen input changed: ' + name

    host_library = (REPO / 'build-host/libpmp4010_velocity.so').resolve(strict=True)
    host_header = host_library.read_bytes()[:20]
    assert host_header[:6] == b'\x7fELF\x02\x01', 'host library must be ELF64 little-endian'
    assert struct.unpack('<H', host_header[18:20])[0] == 62, 'host library must be x86_64'
    arm_library = ROOT / 'drivers/pmp4010/libpmp4010_velocity.so.1.0.0'
    assert struct.unpack('<H', arm_library.read_bytes()[18:20])[0] == 183
    os.environ['PMP4010_VELOCITY_LIBRARY'] = str(host_library)
    sys.path[:0] = [str(ROOT / 'drivers/pmp4010'), str(ROOT / 'hwtui'), str(HERE)]
    # Preload every shipped production Python module before fixtures add their
    # workspace directory to sys.path. Their dependency identities stay pinned.
    production_names = sorted(path.stem for path in expected
                              if path.suffix == '.py' and path.parent != ROOT)
    for name in production_names:
        importlib.import_module(name)
    velocity = sys.modules['pmp4010_velocity']
    loaded_library = velocity.require_velocity_library()
    assert Path(loaded_library._name).resolve() == host_library

    # Two existing fixtures explicitly load their adjacent pmp4010.py by spec.
    # Redirect only those documented aliases; their test classes remain intact.
    original_spec = importlib.util.spec_from_file_location
    redirected = []
    def pinned_spec(name, location, *args, **kwargs):
        source = Path(location).resolve()
        if name in ('bench_acceptance_cli', 'position_cli_test'):
            assert source == REPO / 'device/drivers/pmp4010/tools/pmp4010.py'
            replacement = ROOT / 'drivers/pmp4010/pmp4010.py'
            redirected.append(dict(name=name, fixture_requested=str(source), loaded=str(replacement)))
            return original_spec(name, replacement, *args, **kwargs)
        return original_spec(name, location, *args, **kwargs)
    importlib.util.spec_from_file_location = pinned_spec
    try:
        prepared = importlib.import_module('test_prepared')
    finally:
        importlib.util.spec_from_file_location = original_spec

    production = {}
    for name in production_names:
        module_path = Path(sys.modules[name].__file__).resolve()
        assert module_path in expected and sha(module_path) == expected[module_path], name
        production[name] = dict(file=str(module_path), sha256=sha(module_path))
    for alias, module in [('bench_acceptance_cli', prepared.tool),
                          ('position_cli_test', sys.modules['_test_position'].tool)]:
        module_path = Path(module.__file__).resolve()
        assert module_path == ROOT / 'drivers/pmp4010/pmp4010.py'
        assert sha(module_path) == expected[module_path]
        production[alias] = dict(file=str(module_path), sha256=sha(module_path))
    assert prepared.SimpleController is sys.modules['pmp4010_simple'].SimpleController
    assert prepared.tool._canbus is sys.modules['canbus']
    assert prepared.tool._canopen is sys.modules['canopen']
    fixtures = {name: dict(file=str(Path(module.__file__).resolve()),
                           sha256=sha(Path(module.__file__).resolve()))
                for name, module in list(sys.modules.items())
                if name.startswith('_test_') and getattr(module, '__file__', None)}
    evidence = dict(
        payload_root=str(ROOT), manifest_sha256=MANIFEST_SHA,
        verified_manifest_entries=len(manifest['files']), production_modules=production,
        fixture_modules=fixtures, fixture_tool_redirects=redirected,
        wrapper_sha256=FROZEN,
        native_substitution=dict(reason='x86_64 Mock host cannot execute packaged ARM64 ELF',
                                 host_file=str(host_library), host_sha256=sha(host_library),
                                 host_elf_machine=62, packaged_file=str(arm_library),
                                 packaged_sha256=sha(arm_library), packaged_elf_machine=183,
                                 abi_version=1),
        hardware_execution=False, real_sockets_forbidden=True,
        actual_model='unknown', last_token_usage_input_tokens='unknown',
        parent_conservative_input_estimate='<200K, not measured')
    print(json.dumps(dict(event='pinned_0218_provenance', **evidence), ensure_ascii=False), flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(prepared.PreparedTests)
    test_names = [case.id() for case in suite]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence.update(argv=sys.argv, cwd=os.getcwd(), tests=test_names,
                    tests_run=result.testsRun, success=result.wasSuccessful(),
                    failures=len(result.failures), errors=len(result.errors),
                    skipped=len(result.skipped))
    (HERE / 'pinned-0218-mock-v2-result.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
