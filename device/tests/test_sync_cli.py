#!/usr/bin/env python3
"""Hardware-free integration of the production config validator and opt-in gate."""
import json
import pathlib
import subprocess
import sys
import tempfile

binary = pathlib.Path(sys.argv[1]).resolve()
validator = pathlib.Path(sys.argv[2]).resolve()
checks = 0


def check(condition, detail):
    global checks
    checks += 1
    if not condition:
        raise AssertionError(detail)


base = {
    "device": {"id": "km3-test-001"},
    "network": {"server_url": "wss://example.invalid/api/v1/device/ws"},
    "storage": {"data_root": "/data"},
}

with tempfile.TemporaryDirectory(prefix="km3-sync-cli-") as directory:
    config = pathlib.Path(directory) / "device.json"

    def run(data, *args, script=False):
        config.write_text(json.dumps(data), encoding="utf-8")
        command = [str(validator), str(binary), str(config)] if script else [
            str(binary), "--config", str(config), *args]
        result = subprocess.run(command, text=True, capture_output=True, timeout=15)
        return result, result.stdout + result.stderr

    result, output = run(base, "--validate-config-only")
    check(result.returncode == 0, output)
    check("configuration valid" in output, output)
    check("realtime    :" not in output and "SYNC        : enabled" not in output, output)
    result, output = run(base, script=True)
    check(result.returncode == 0, output)
    result, output = run(base, "--validate-config-only", "--enable-sync")
    check(result.returncode != 0 and "SYNC can-2 endpoint" in output, output)

    with_can = json.loads(json.dumps(base))
    # This name is intentionally nonexistent. Validation never resolves it, and
    # the default controller run must not attempt to open it.
    with_can["bus"] = {"can": [{"logical": "can-2", "iface": "sync-no-device",
                                "bitrate": 1000000, "protocol": "canopen"}]}
    result, output = run(with_can, "--enable-sync", "--validate-config-only")
    check(result.returncode == 0 and "configuration valid" in output, output)
    result, output = run(with_can, "--duration-s", "1")
    check(result.returncode == 0, output)
    check("SYNC        : disabled" in output and "SYNC device open" not in output, output)

    for value in [0, -1, 1.5, 4000.0, "4000", True, None, 1000001, 2**64]:
        data = json.loads(json.dumps(with_can))
        data["network"]["sync_period_us"] = value
        for args in [("--validate-config-only",), ("--enable-sync",), ()]:
            # Default development fallback is still allowed, but cannot send.
            result, output = run(data, *args, *(('--duration-s', '1') if not args else ()))
            if args:
                check(result.returncode != 0, output)
                check("以默认参数继续" not in output, output)
            else:
                check(result.returncode == 0 and "SYNC        : disabled" in output, output)
            check("SYNC        : enabled" not in output and "SYNC device open" not in output, output)
        result, output = run(data, script=True)
        check(result.returncode != 0, output)

    missing = subprocess.run([str(binary), "--enable-sync", "--config", str(config) + ".missing"],
                             text=True, capture_output=True, timeout=10)
    check(missing.returncode != 0, missing.stdout + missing.stderr)
    check("以默认参数继续" not in missing.stdout, missing.stdout)

print(f"{checks} check(s), 0 failure(s)")
