"""Fresh-process pure load check: no production preload and no real transport."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = (HERE.parent / 'package/payload').resolve(strict=True)
BLOCKED = {'socket.__new__', 'subprocess.Popen', 'os.system', 'os.posix_spawn'}
events = []


def audit(event, args):
    if event in BLOCKED:
        events.append(event)
        raise AssertionError('cold load forbids transport/process creation: ' + event)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sys.addaudithook(audit)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, default=HERE / 'pinned_payload.py')
    args = parser.parse_args()
    before = [name for name in sys.modules if name.startswith('pmp4010')
              or name in ('negative_once_pmp4010', 'canbus', 'canopen', 'tui', 'mockbus')]
    assert not before, 'production modules were already loaded: ' + repr(before)
    helper_path = args.helper.resolve(strict=True)
    spec = importlib.util.spec_from_file_location('cold_payload_helper', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert not [name for name in sys.modules if name.startswith('pmp4010')
                or name in ('negative_once_pmp4010', 'canbus', 'canopen', 'tui', 'mockbus')]
    # The board-only absolute ROOT is the sole test substitution. No production
    # source, manifest, import order, standard layer or controller is replaced.
    helper.ROOT = ROOT
    print(json.dumps(dict(event='cold_load_begin', helper=str(helper_path),
                          helper_sha256=sha(helper_path), root=str(ROOT),
                          production_modules_before=before), ensure_ascii=False), flush=True)
    tool, controller_type = helper.load_pinned()
    log_base = HERE / 'cold-load-unused-motion.jsonl'
    assert not log_base.exists()
    controller = helper.make_controller(tool, controller_type, log_base)
    assert controller.bus is None and not controller.refresh_clients and not controller.scan_clients
    assert controller.node == 1 and controller.linear and controller.mm_per_motor_revolution == 4
    assert tool._canopen is sys.modules['canopen'] and tool._canbus is sys.modules['canbus']
    modules = {}
    manifest = json.loads((ROOT / 'payload-manifest.json').read_bytes())
    expected = {str((ROOT / item['path']).resolve()): item['sha256']
                for item in manifest['files'] if 'sha256' in item}
    for name, module in list(sys.modules.items()):
        if name.startswith('pmp4010') or name in ('negative_once_pmp4010', 'canbus', 'canopen', 'tui'):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(ROOT) and sha(path) == expected[str(path)], name
            modules[name] = dict(file=str(path), sha256=sha(path))
    assert not events and not log_base.exists()
    print(json.dumps(dict(event='cold_load_pass', CAN_opens=0, CAN_sends=0,
                          production_modules=modules, blocked_io_attempts=events,
                          controller_bus=None, native_library_loaded=False,
                          hardware_execution=False), ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
