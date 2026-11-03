"""纯本地：模拟SSH，远程wrapper只执行合成文件；不运行真实诊断文件、不打开CAN。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('staged_runner', BASE/'execute_staged_once.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
r = s.r


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=BASE, prefix='local-check-')
        self.base = Path(self.tmp.name)
        self.old = s.BASE, r.BASE, r.SOURCE, r.BASELINE, r.invoke
        s.BASE = r.BASE = self.base
        r.SOURCE = self.base/'source'
        r.SOURCE.mkdir()
        for name in r.NAMES:
            (r.SOURCE/name).write_text("print('synthetic-only')\n")
        (r.SOURCE/'run_once_stdin.py').write_text("print('fixture executed')\n")
        (self.base/'passive_snapshot.py').write_bytes((BASE/'passive_snapshot.py').read_bytes())
        self.state = dict(hostname='fixture', model='fixture', boot_id='fixture', device_id='fixture',
            config_sha='fixture', can_env_sha='fixture', current='fixture', services={},
            related_processes_excluding_snapshot_pid=[], can_rcvlists={}, log_root_exists=False, logs=[],
            can_link=dict(rc=0, stdout=json.dumps([dict(linkinfo={}, flags=['UP'], stats64=dict(rx={},tx={}))])))
        r.BASELINE = self.base/'baseline.json'
        r.BASELINE.write_text(json.dumps(self.state))
        files = {name:r.digest((r.SOURCE/name).read_bytes()) for name in r.NAMES}
        self.config = dict(files=files,
            argv=['--execute-once', '--od-session-ack', 'fixture', '--motion-confirm', 'fixture'],
            remote_stage='/var/tmp/km3-pmp4010-alarm-20261019-735538978204/motion-compare-proposal',
            runner_sha256=r.digest(Path(r.__file__).read_bytes()),
            passive_sha256=r.digest((self.base/'passive_snapshot.py').read_bytes()),
            bootstrap_sha256=r.digest((r.SOURCE/'run_once_stdin.py').read_bytes()),
            baseline_sha256=r.digest(r.BASELINE.read_bytes()))
        self.freeze = self.base/'frozen.json'
        self.freeze.write_text(json.dumps(self.config))
        for name in ('stage','cold-load'):
            (self.base/(name+'.rc')).write_text('0\n')
        (self.base/'stage.stdout').write_text(json.dumps(dict(stage=self.config['remote_stage'],sha256=files)))
        self.calls = []
        self.motion_rc = 1
        self.motion_raw = b''
        def fake(prefix, raw, timeout):
            self.calls.append(prefix)
            if prefix=='motion-once':return self.motion_rc,self.motion_raw
            return 0,json.dumps(self.state).encode()
        r.invoke = fake

    def tearDown(self):
        s.BASE, r.BASE, r.SOURCE, r.BASELINE, r.invoke = self.old
        self.tmp.cleanup()

    def run_main(self, execute=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return s.main(['--frozen-input', str(self.freeze), '--runner-sha256',
                           r.digest(Path(s.__file__).read_bytes()), '--frozen-sha256', r.digest(self.freeze.read_bytes())]
                          +(['--parent-authorized-staged-once'] if execute else []))

    def test_default_plan_has_no_ssh(self):
        self.assertEqual(self.run_main(),0)
        self.assertEqual(self.calls,[])
        self.assertFalse((self.base/'staged-execution.attempt.json').exists())

    def test_hash_change_blocks_before_any_ssh(self):
        (r.SOURCE/r.NAMES[0]).write_text('changed')
        with self.assertRaises(ValueError):self.run_main(True)
        self.assertEqual(self.calls,[])

    def test_failed_motion_preserves_rc_no_after_no_reentry(self):
        self.assertEqual(self.run_main(True),1)
        self.assertEqual(self.calls,['before-motion-passive','motion-once'])
        with self.assertRaises(FileExistsError):self.run_main(True)
        self.assertEqual(len(self.calls),2)

    def test_zero_exit_without_disable_confirmation_no_after(self):
        self.motion_rc=0
        self.assertEqual(self.run_main(True),1)
        self.assertEqual(self.calls,['before-motion-passive','motion-once'])

    def test_normal_success_collects_after_only_once(self):
        self.motion_rc=0
        self.motion_raw=json.dumps(dict(event='单次退出',operation_enabled_cleared=True)).encode()
        self.assertEqual(self.run_main(True),0)
        self.assertEqual(self.calls,['before-motion-passive','motion-once','after-motion-passive'])

    def test_passive_change_consumes_chance_without_motion(self):
        self.state['boot_id']='changed'
        with self.assertRaises(RuntimeError):self.run_main(True)
        self.assertEqual(self.calls,['before-motion-passive'])
        with self.assertRaises(FileExistsError):self.run_main(True)

    def test_missing_stage_receipt_blocks_before_ssh(self):
        (self.base/'stage.rc').unlink()
        with self.assertRaises(FileNotFoundError):self.run_main(True)
        self.assertEqual(self.calls,[])

    def test_remote_wrapper_single_execution_with_synthetic_files(self):
        stage=self.base/'remote-stage'
        stage.mkdir()
        for name in r.NAMES:(stage/name).write_text('# fixture\n')
        cfg=dict(self.config,remote_stage=str(stage),files={name:r.digest((stage/name).read_bytes()) for name in r.NAMES})
        source=r.execute_source(cfg)
        compile(source,'<generated-stdin>','exec')
        argv=[sys.executable,'-I','-B','-']
        first=subprocess.run(argv,input=source,capture_output=True)
        self.assertEqual(first.returncode,0,first.stderr)
        self.assertIn(b'fixture executed',first.stdout)
        second=subprocess.run(argv,input=source,capture_output=True)
        self.assertNotEqual(second.returncode,0)
        self.assertNotIn(b'fixture executed',second.stdout)
        self.assertEqual(r.SSH[-1],'python3 -I -B -')

    def test_remote_hash_mismatch_no_bootstrap_no_attempt(self):
        stage=self.base/'remote-stage';stage.mkdir()
        for name in r.NAMES:(stage/name).write_text('# fixture\n')
        cfg=dict(self.config,remote_stage=str(stage))
        p=subprocess.run([sys.executable,'-I','-B','-'],input=r.execute_source(cfg),capture_output=True)
        self.assertNotEqual(p.returncode,0)
        self.assertNotIn(b'fixture executed',p.stdout)
        self.assertFalse((stage/'execution.attempt').exists())


if __name__=='__main__':unittest.main(verbosity=2)
