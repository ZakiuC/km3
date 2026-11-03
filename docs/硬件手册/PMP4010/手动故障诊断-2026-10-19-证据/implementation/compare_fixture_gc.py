"""Compare the same existing MockBus case with archived/current manual module."""
import gc
import importlib.util
from pathlib import Path
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0,str(REPO/'device/drivers/pmp4010/tools'))
import _test_bench_support  # Install the same shared-tool paths as the fixture.
variant = sys.argv[1]
if variant == 'baseline':
    spec = importlib.util.spec_from_file_location('pmp4010_manual',HERE/'baseline/pmp4010_manual.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['pmp4010_manual'] = module
    spec.loader.exec_module(module)
starts = {}
def callback(phase, info):
    if phase == 'start':
        starts[info['generation']] = time.monotonic()
    else:
        elapsed = time.monotonic()-starts[info['generation']]
        if elapsed > .005:
            print('GC_DURATION',info,'elapsed_s=',elapsed,flush=True)
gc.callbacks.append(callback)
suite=unittest.defaultTestLoader.loadTestsFromName('_test_manual.ManualTests.test_enabled_edge_moving_handshake_all_protected_guards_cleanup')
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
