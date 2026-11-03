# Prepared only. Execute after parent freeze; each line records the actual child argv and rc.
set -e
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-model python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-hwtui python3 -I -m unittest discover -s tools/hwtui -p 'test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-tools-tests python3 -I -m unittest discover -s tools/tests -p 'test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-host-build cmake --build build-host -j4
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-host-ctest ctest --test-dir build-host --output-on-failure
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-asan-build cmake --build build-asan -j4
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-asan-ctest ctest --test-dir build-asan --output-on-failure
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-tsan-build cmake --build build-tsan -j4
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-tsan-ctest setarch -R ctest --test-dir build-tsan --output-on-failure
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-arm-build cmake --build build-arm64 -j4
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-drivers bash tools/check-drivers.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-docs bash tools/check-docs.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-deps bash tools/check-deps.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-citations bash tools/check-manual-citations.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-gen-od python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-gen-canopen python3 -I tools/hwdriver/gen-canopen.py --check
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/run.py probe-gen-access python3 -I tools/hwdriver/gen-pmp4010-access.py --check
