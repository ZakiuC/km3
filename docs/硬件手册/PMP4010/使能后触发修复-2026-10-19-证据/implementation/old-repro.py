"""Isolated reproduction of the pre-fix enabled-edge failure; expected rc=1.

Run from any directory:
  python3 -I /home/zakiu/km3/docs/硬件手册/PMP4010/使能后触发修复-2026-10-19-证据/implementation/old-repro.py

This process loads the saved old controller under a separate module name and
injects it only into the current test module's ManualController reference.
It never overwrites production files or changes another process's imports.
Standard MockBus fixtures forbid real CAN sockets. This is an intentional old
failure reproduction, not a failure of the frozen implementation's acceptance.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest


def main():
    evidence = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[5]
    sys.path.insert(0, str(root / 'device/drivers/pmp4010/tools'))
    import _test_manual as tests

    before = evidence / 'pmp4010_manual.before.py'
    digest = hashlib.sha256(before.read_bytes()).hexdigest()
    expected = 'bee76eaeed270e6d66d1b2d4791e6c5778305b246136ec4e90cd9788bf8642b9'
    if digest != expected:
        raise RuntimeError('Saved pre-fix manual SHA256 mismatch: ' + digest)
    spec = importlib.util.spec_from_file_location('pmp4010_manual_before_repro', before)
    old_manual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_manual)
    tests.ManualController = old_manual.ManualController

    print('EXPECTED OLD FAILURE REPRODUCTION: isolated saved manual SHA256=' + digest, flush=True)
    print('Current production source remains unchanged; expected exit code is 1.', flush=True)
    suite = unittest.TestSuite([tests.ManualTests(
        'test_enabled_edge_behavior_moves_frozen_target_not_disabled_ack')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
