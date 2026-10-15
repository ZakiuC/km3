"""Exercise deployment staging with a local SSH stand-in; never contact a host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy_hwtools.sh'


class DeployHwtoolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='km3-hwtools-package-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'tools').mkdir(parents=True)
        shutil.copy2(SCRIPT, self.repo / 'tools/deploy_hwtools.sh')
        hwtui = self.repo / 'tools/hwtui'
        hwtui.mkdir()
        for name in ('canopen', 'canbus', 'mockbus', 'canopen_consts', 'tui', 'discover'):
            (hwtui / (name + '.py')).write_text('# package fixture\n')
        (hwtui / '__pycache__').mkdir()
        (hwtui / '__pycache__/excluded.pyc').write_bytes(b'cache')
        model = self.repo / 'device/drivers/pmp4010'
        (model / 'tools').mkdir(parents=True)
        (model / 'driver').mkdir()
        (model / 'tools/pmp4010.py').write_text('# tool fixture\n')
        self.od = model / 'driver/od.json'
        self.od.write_text(json.dumps({'model': 'PMP4010', 'entries': []}))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'ssh.log'
        ssh = self.bin / 'ssh'
        ssh.write_text('''#!/usr/bin/env python3
import os, subprocess, sys
with open(os.environ['PACKAGE_SSH_LOG'], 'a') as f:
    f.write(repr(sys.argv[1:]) + '\\n')
a = sys.argv[1:]
while a and a[0] == '-o':
    a = a[2:]
if len(a) != 2:
    raise SystemExit('unexpected ssh arguments')
raise SystemExit(subprocess.call(['bash', '-c', a[1]]))
''')
        ssh.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        PACKAGE_SSH_LOG=str(self.log))
        self.target = self.root / 'remote'

    def run_script(self, *extra):
        return subprocess.run(['bash', str(self.repo / 'tools/deploy_hwtools.sh'),
                               '--host', 'unused.invalid', '--version', 'package-test',
                               '--root', str(self.target), *extra],
                              cwd=self.repo, env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_dry_run_never_invokes_ssh(self):
        result = self.run_script('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists(), result.stdout)
        self.assertFalse(self.target.exists())
        self.assertIn('driver/od.json', result.stdout)

    def test_versioned_package_contains_od_and_shared_modules(self):
        previous = self.target / 'releases/previous'
        previous.mkdir(parents=True)
        (previous / 'preserved.txt').write_text('previous release')
        (self.target / 'current').symlink_to(previous)
        result = self.run_script('--apply', '--allow-dirty')
        self.assertEqual(result.returncode, 0, result.stderr)
        release = self.target / 'releases/package-test'
        self.assertEqual((self.target / 'current').resolve(), release)
        self.assertEqual((release / 'drivers/pmp4010/od.json').read_bytes(), self.od.read_bytes())
        self.assertTrue((release / 'drivers/pmp4010/pmp4010.py').is_file())
        for name in ('canopen', 'canbus', 'mockbus', 'canopen_consts', 'tui', 'discover'):
            self.assertTrue((release / 'hwtui' / (name + '.py')).is_file())
        self.assertFalse((release / 'hwtui/__pycache__/excluded.pyc').exists())
        self.assertTrue((previous / 'preserved.txt').exists())

    def test_missing_od_fails_before_ssh(self):
        self.od.unlink()
        result = self.run_script('--apply', '--allow-dirty')
        self.assertEqual(result.returncode, 2)
        self.assertIn('od.json', result.stderr)
        self.assertFalse(self.log.exists())

    def test_failed_import_restores_previous_release(self):
        previous = self.target / 'releases/previous'
        previous.mkdir(parents=True)
        (self.target / 'current').symlink_to(previous)
        (self.repo / 'tools/hwtui/canopen.py').write_text('raise RuntimeError("broken fixture")\n')
        result = self.run_script('--apply', '--allow-dirty')
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.target / 'current').resolve(), previous)
        self.assertIn('已回滚到 previous', result.stderr)


if __name__ == '__main__':
    unittest.main()
