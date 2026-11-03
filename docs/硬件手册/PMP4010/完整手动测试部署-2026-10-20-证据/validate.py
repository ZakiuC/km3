import capture
base='/opt/km3/hwtools/manual-test-20261020-e137c603'
capture.run('arm-load-smoke',capture.SSH+['python3 -I -B '+base+'/load_smoke.py --check-missing-library'])
capture.run('launcher-syntax',capture.SSH+['sh -n '+base+'/run-manual.sh'])
capture.run('direct-help',capture.SSH+['python3 -I -B '+base+'/drivers/pmp4010/pmp4010.py --help'])
