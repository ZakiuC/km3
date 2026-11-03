import os, platform, pathlib, subprocess, json
print('hostname='+platform.node())
print('machine_id='+pathlib.Path('/etc/machine-id').read_text().strip())
print('architecture='+platform.machine())
print('glibc='+os.confstr('CS_GNU_LIBC_VERSION'))
print(pathlib.Path('/etc/os-release').read_text())
print(subprocess.check_output(['df','-h','/opt/km3/hwtools'],text=True))
print('target_exists='+str(pathlib.Path('/opt/km3/hwtools/pmp4010-cpp-test-20261020-758f9107').exists()))
assert platform.machine()=='aarch64'
version=tuple(map(int,os.confstr('CS_GNU_LIBC_VERSION').split()[-1].split('.')))
assert version >= (2,34)
