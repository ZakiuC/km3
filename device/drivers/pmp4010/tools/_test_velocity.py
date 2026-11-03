"""Shared exact native vectors, binding refusal, and unchanged CLI units."""
import contextlib
from fractions import Fraction
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pmp4010_velocity as velocity


class VelocityTests(unittest.TestCase):
    def test_shared_exact_vectors(self):
        vectors = json.loads((HERE.parent / 'tests' / 'velocity_vectors.json').read_text())
        if isinstance(vectors, dict):
            vectors = vectors['vectors']
        for vector in vectors:
            with self.subTest(vector=vector['name']):
                cal = {'values': vector['calibration']}
                convert = velocity.unit_to_velocity if vector['direction'] == 0 else velocity.velocity_to_unit
                if 'error' in vector:
                    try:
                        value = Fraction(*map(int, vector['input']))
                    except (ValueError, ZeroDivisionError):
                        value = '/'.join(vector['input'])
                    with self.assertRaises(ValueError):
                        convert(value, cal)
                    print('Python shared vector {}: rejected (expected error={})'.format(vector['name'], vector['error']))
                    continue
                value = Fraction(*map(int, vector['input']))
                expected = Fraction(*map(int, vector['expected']))
                actual = convert(value, cal)
                self.assertEqual(actual, expected)
                self.assertIsInstance(actual, Fraction)
                if 'int32_error' in vector:
                    if vector['int32_error']:
                        with self.assertRaises(ValueError):
                            velocity.velocity_to_int32(value, cal)
                    else:
                        self.assertEqual(velocity.velocity_to_int32(value, cal), expected)
                print('Python shared vector {}: {} -> {} exact'.format(vector['name'], value, actual))

    def test_integer_samples_and_rational_speeds_are_mutual_inverses(self):
        for values in ([10000, 1, 1, 1], [131072, 1, 1, 1], [36000, 1, 3, 1],
                       [0xffffffff, 0xfffffffe, 0xfffffffd, 0xfffffffc]):
            cal = {'values': values}
            for units in (-0x80000000, -6553600, -50000, -1, 0, 1, 50000, 6553600, 0x7fffffff):
                rpm = velocity.unit_to_velocity(units, cal)
                self.assertEqual(velocity.velocity_to_unit(rpm, cal), units)
                self.assertEqual(velocity.velocity_to_int32(rpm, cal), units)
            for rpm in (Fraction(-2, 3), Fraction(0), Fraction(3, 7), Fraction(3000)):
                self.assertEqual(velocity.unit_to_velocity(velocity.velocity_to_unit(rpm, cal), cal), rpm)
        print('Python inverse samples: 36 signed Int32 write roundtrips + 16 rational rpm roundtrips')

    def test_int32_write_contract_never_rounds(self):
        cal = {'values': [10000, 1, 1, 1]}
        self.assertEqual(velocity.velocity_to_int32('300', cal), 50000)
        self.assertEqual(velocity.velocity_to_unit('0.1', cal), Fraction(50, 3))
        with self.assertRaisesRegex(ValueError, '不是整数'):
            velocity.velocity_to_int32('0.1', cal)
        for units in (-0x80000001, 0x80000000):
            rpm = velocity.unit_to_velocity(units, cal)
            with self.assertRaisesRegex(ValueError, 'Int32'):
                velocity.velocity_to_int32(rpm, cal)

    def test_invalid_numbers_and_calibration_are_explicit(self):
        cal = {'values': [10000, 1, 1, 1]}
        for value in (True, False, float('nan'), float('inf'), -float('inf'), 1.5,
                      'NaN', 'Infinity', '1/0', None):
            for convert in (velocity.unit_to_velocity, velocity.velocity_to_unit):
                with self.subTest(value=value, convert=convert.__name__):
                    with self.assertRaises(ValueError):
                        convert(value, cal)
        for bad in (None, {}, {'resolution': '10000', 'gear_ratio': '1'}, {'values': [1, 1, 1]},
                    {'values': [True, 1, 1, 1]}, {'values': ['10000', 1, 1, 1]},
                    {'values': [Fraction(1), 1, 1, 1]}):
            with self.assertRaisesRegex(ValueError, '正Uint32'):
                velocity.unit_to_velocity(0, bad)
        for index in range(4):
            for invalid in (-1, 0, 0x100000000):
                values = [10000, 1, 1, 1]
                values[index] = invalid
                with self.assertRaisesRegex(ValueError, '正Uint32'):
                    velocity.unit_to_velocity(0, {'values': values})

    def test_raw_calibration_is_authoritative(self):
        cal = {'values': [10000, 1, 1, 1], 'resolution': '1', 'gear_ratio': '99999'}
        self.assertEqual(velocity.unit_to_velocity(50000, cal), 300)
        self.assertEqual(velocity.velocity_to_int32(300, cal), 50000)


class BindingRefusalTests(unittest.TestCase):
    def setUp(self):
        velocity._open_library.cache_clear()
        self.addCleanup(velocity._open_library.cache_clear)

    def test_missing_explicit_library_never_falls_back(self):
        with tempfile.TemporaryDirectory(prefix='km3-velocity-missing-') as directory:
            path = str(Path(directory) / 'missing.so')
            with mock.patch.dict(os.environ, {velocity.LIBRARY_ENV: path}), \
                    mock.patch.object(velocity.ctypes, 'CDLL') as loader:
                with self.assertRaisesRegex(ValueError, '缺少本地速度换算库'):
                    velocity.unit_to_velocity(50000, {'values': [10000, 1, 1, 1]})
                loader.assert_not_called()

    def test_library_path_never_implicitly_uses_current_directory(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            paths = velocity._candidate_paths()
        self.assertEqual(paths, [HERE / 'libpmp4010_velocity.so',
                                 HERE.parents[3] / 'build-host' / 'libpmp4010_velocity.so'])
        for path in ('', 'relative.so'):
            with mock.patch.dict(os.environ, {velocity.LIBRARY_ENV: path}):
                with self.assertRaisesRegex(ValueError, '绝对路径'):
                    velocity.require_velocity_library()

    def test_wrong_architecture_or_loader_error_is_chinese(self):
        with mock.patch.object(velocity.ctypes, 'CDLL', side_effect=OSError('wrong ELF class')):
            with self.assertRaisesRegex(ValueError, '二进制架构'):
                velocity._open_library('/absolute/wrong-architecture.so')

    def test_wrong_abi_and_missing_symbols_are_refused(self):
        wrong = mock.Mock()
        wrong.pmp4010_velocity_abi_version.return_value = 99
        with mock.patch.object(velocity.ctypes, 'CDLL', return_value=wrong):
            with self.assertRaisesRegex(ValueError, 'ABI 版本不匹配'):
                velocity._open_library('/absolute/wrong-abi.so')
        missing = mock.Mock(spec=['pmp4010_velocity_abi_version'])
        missing.pmp4010_velocity_abi_version.return_value = velocity.ABI_VERSION
        with mock.patch.object(velocity.ctypes, 'CDLL', return_value=missing):
            with self.assertRaisesRegex(ValueError, '缺少 ABI'):
                velocity._open_library('/absolute/missing-symbol.so')

    def test_native_error_and_invalid_output_never_get_python_fallback(self):
        library = mock.Mock()
        library.pmp4010_velocity_convert.return_value = 6
        with mock.patch.object(velocity, 'require_velocity_library', return_value=library):
            with self.assertRaisesRegex(ValueError, '128位'):
                velocity.unit_to_velocity(1, {'values': [10000, 1, 1, 1]})
        library.pmp4010_velocity_convert.return_value = 0
        with mock.patch.object(velocity, 'require_velocity_library', return_value=library):
            with self.assertRaisesRegex(ValueError, '无效精确分数'):
                velocity.unit_to_velocity(1, {'values': [10000, 1, 1, 1]})

    def test_missing_library_preserves_import_help_and_readonly_calibration(self):
        with tempfile.TemporaryDirectory(prefix='km3-velocity-import-') as directory:
            environment = dict(os.environ, **{velocity.LIBRARY_ENV: str(Path(directory) / 'missing.so')})
            help_result = subprocess.run([sys.executable, '-I', str(HERE / 'pmp4010.py'), '--help'],
                env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            calibration = subprocess.run([sys.executable, '-I', str(HERE / 'pmp4010.py'),
                '--calibrate', '--node', '1', '--offline-calibration', '10000', '1', '1', '1'],
                env=environment, capture_output=True, text=True, check=False)
            self.assertEqual(calibration.returncode, 0, calibration.stdout + calibration.stderr)
            self.assertIn('真实发送计数=0', calibration.stdout)

    def test_missing_library_refuses_position_before_bus_or_cleanup(self):
        spec = importlib.util.spec_from_file_location('velocity_cli_test', HERE / 'pmp4010.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        import pmp4010_position as position
        with mock.patch.object(position, 'require_velocity_library', side_effect=ValueError('缺少本地速度换算库')), \
                mock.patch.object(tool, 'connect_bus') as connect, contextlib.redirect_stdout(io.StringIO()):
            rc = tool.main(['--position', '--node', '1', '--target', '10', '--velocity', '100',
                '--acceleration', '100', '--deceleration', '100', '--soft-min', '-30', '--soft-max', '30',
                '--max-displacement', '30', '--motion-log', '/unused/no-velocity-log.jsonl', '--apply'])
            self.assertNotEqual(rc, 0)
            connect.assert_not_called()
        session = mock.Mock()
        with mock.patch.object(position, 'require_velocity_library', side_effect=ValueError('缺少本地速度换算库')):
            with self.assertRaisesRegex(ValueError, '缺少本地'):
                position.run(session, mock.Mock(), {}, mock.Mock())
        self.assertNotIn('needs_stop', session.__dict__)
        session.stop_after_failure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
