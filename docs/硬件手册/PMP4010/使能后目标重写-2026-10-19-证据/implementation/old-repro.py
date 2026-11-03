"""Intentional old-source failure reproduction, expected rc=1; no source writes.

python3 -I docs/硬件手册/PMP4010/使能后目标重写-2026-10-19-证据/implementation/old-repro.py

The test model intentionally assumes executable target data must be refreshed
after enable. This is a controlled protocol hypothesis, not a firmware fact.
"""
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

evidence = Path(__file__).resolve().parent
root = Path(__file__).resolve().parents[5]
sys.path.insert(0,str(root/'device/drivers/pmp4010/tools'))
import _test_manual as tests
before = evidence/'pmp4010_manual.before.py'
digest = hashlib.sha256(before.read_bytes()).hexdigest()
if digest != 'e03704a93873dad486f3d4ab75eb0b5bd7c1561d5189b3aa2643834a5123702d':
    raise RuntimeError('Saved c20f source SHA256 mismatch: '+digest)
spec = importlib.util.spec_from_file_location('manual_before_enabled_target_rewrite',before)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)
tests.ManualController = old.ManualController
print('EXPECTED OLD FAILURE: isolated c20f manual '+digest,flush=True)
print('Controlled hypothesis model, not firmware acceptance; expected rc=1.',flush=True)
suite = unittest.TestSuite([tests.ManualTests(
    'test_enabled_target_rewrite_hypothesis_moves_same_frozen_target')])
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
