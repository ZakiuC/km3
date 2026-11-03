#!/bin/bash
set -eu
cd /home/zakiu/km3
rec=docs/硬件手册/PMP4010/手动界面重设计-2026-10-20-证据/acceptance/record.py
python3 "$rec" python-tools python3 -I -m unittest discover -s tools/tests -p 'test_*.py'
python3 "$rec" gate-drivers bash tools/check-drivers.sh
python3 "$rec" gate-docs bash tools/check-docs.sh
python3 "$rec" gate-deps bash tools/check-deps.sh
python3 "$rec" gate-citations bash tools/check-manual-citations.sh
python3 "$rec" generator-od python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
python3 "$rec" generator-canopen python3 -I tools/hwdriver/gen-canopen.py --check
python3 "$rec" generator-access python3 -I tools/hwdriver/gen-pmp4010-access.py --check
