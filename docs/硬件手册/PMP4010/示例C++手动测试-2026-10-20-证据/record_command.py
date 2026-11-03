#!/usr/bin/env python3
"""Record one exact local acceptance command without a shell or hidden rc."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
evidence = Path(__file__).resolve().parent
label, *command = sys.argv[1:]
if not label or not command or Path(label).name != label:
    raise SystemExit("usage: record_command.py LABEL PROGRAM [ARG ...]")
sources = sorted((root / "docs/硬件手册/PMP4010/示例").glob("*"))
sources += [root / "device/cmake/aarch64-linux-gnu.cmake"]
sources += sorted((root / "tools/pmp4010_cpp_test").rglob("*"))
sources += [root / "device/drivers/pmp4010/driver/pmp4010_velocity.cpp",
            root / "device/drivers/pmp4010/driver/pmp4010_velocity.hpp"]
for module in ("device/drivers/pmp4010", "tools/hwtui", "tools/tests"):
    sources += sorted((root / module).rglob("*"))
sources += [root / "device/CMakeLists.txt"]
hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sources if path.is_file() and "__pycache__" not in path.parts}
record = {"argv": command, "cwd": str(root), "source_sha256_before": hashes,
          "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
    result = subprocess.run(command, cwd=root, capture_output=True, check=False)
    rc, stdout, stderr = result.returncode, result.stdout, result.stderr
except OSError as error:
    rc, stdout, stderr = 127, b"", str(error).encode()
(evidence / (label + ".stdout.log")).write_bytes(stdout)
(evidence / (label + ".stderr.log")).write_bytes(stderr)
record.update(returncode=rc, finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
record["source_sha256_after"] = {
    str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sources if path.is_file() and "__pycache__" not in path.parts}
(evidence / (label + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
sys.stdout.buffer.write(stdout)
sys.stderr.buffer.write(stderr)
print(f"\nrecorded {label}: rc={rc}", flush=True)
raise SystemExit(rc if rc >= 0 else 128 - rc)
