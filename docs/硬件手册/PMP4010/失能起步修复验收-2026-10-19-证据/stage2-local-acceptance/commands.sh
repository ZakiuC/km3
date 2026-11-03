set -e
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-model python3 -I -m unittest discover -s device/drivers/pmp4010/tools -p '_test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-hwtui python3 -I -m unittest discover -s tools/hwtui -p 'test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-tools-tests python3 -I -m unittest discover -s tools/tests -p 'test_*.py'
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-drivers bash tools/check-drivers.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-docs bash tools/check-docs.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-deps bash tools/check-deps.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-citations bash tools/check-manual-citations.sh
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-gen-od python3 -I tools/hwdriver/gen-od.py --model pmp4010 --check
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-gen-canopen python3 -I tools/hwdriver/gen-canopen.py --check
python3 -I -B /tmp/km3-pp-disabled-start-20261019-acceptance/stage2/run.py stage2-gen-access python3 -I tools/hwdriver/gen-pmp4010-access.py --check
