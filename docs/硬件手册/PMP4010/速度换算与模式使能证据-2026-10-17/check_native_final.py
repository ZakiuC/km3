import importlib.util, json, os
from pathlib import Path
root=Path('/home/zakiu/km3')
assert 'PMP4010_VELOCITY_LIBRARY' not in os.environ, 'unexpected explicit library environment'
path=root/'device/drivers/pmp4010/tools/pmp4010_velocity.py'
spec=importlib.util.spec_from_file_location('pmp4010_velocity',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
library=module.require_velocity_library()
assert Path(library._name).resolve()==(root/'build-host/libpmp4010_velocity.so').resolve(), library._name
print('PMP4010_VELOCITY_LIBRARY absent; loaded default:',library._name)
print('ABI:',library.pmp4010_velocity_abi_version())
print('50000 user units/s =>',module.unit_to_velocity(50000, {'values':[10000,1,1,1]}),'rpm')
stdout=Path('/tmp/km3-pmp4010-a-20261017-evidence/final/python-driver.stdout.log').read_text()
assert sum(x.startswith('Python shared vector ') for x in stdout.splitlines())==33
print('Python shared vector output: 33 cases')
cmake=(root/'device/drivers/pmp4010/native/CMakeLists.txt').read_text()
assert 'target_link_libraries' not in cmake
print('native/CMakeLists.txt declares no linked CAN/HAL/OD targets')
readme=(root/'device/drivers/pmp4010/native/README.md').read_text()
assert 'lib/libpmp4010_velocity.so*' in readme and 'include/km3/pmp4010/pmp4010_velocity_c.h' in readme
prefix=Path('/tmp/km3-pmp4010-a-20261017-evidence/native-host-install')
assert (prefix/'lib/libpmp4010_velocity.so').is_file()
assert (prefix/'include/km3/pmp4010/pmp4010_velocity_c.h').read_bytes()==(root/'device/drivers/pmp4010/driver/pmp4010_velocity_c.h').read_bytes()
print('Staged native install layout matches README: lib/libpmp4010_velocity.so*, include/km3/pmp4010/pmp4010_velocity_c.h')
