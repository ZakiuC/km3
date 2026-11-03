"""Exact velocity conversion through the sole C++ implementation (C ABI v1).

No library is loaded at import time. Install libpmp4010_velocity.so beside these
scripts, build it in the repository's build-host directory, or set the absolute
PMP4010_VELOCITY_LIBRARY path. There is no Python formula or build-on-use fallback.
"""
import ctypes
from fractions import Fraction
from functools import lru_cache
import math
import os
from pathlib import Path


LIBRARY_ENV = 'PMP4010_VELOCITY_LIBRARY'
ABI_VERSION = 1


def _candidate_paths():
    explicit = os.environ.get(LIBRARY_ENV)
    if explicit is not None:
        path = Path(explicit)
        if not explicit or not path.is_absolute():
            raise ValueError(LIBRARY_ENV + ' 必须指定本地换算库的绝对路径')
        return [path]
    directory = Path(__file__).resolve().parent
    paths = [directory / 'libpmp4010_velocity.so']
    if directory.parts[-4:] == ('device', 'drivers', 'pmp4010', 'tools'):
        paths.append(directory.parents[3] / 'build-host' / 'libpmp4010_velocity.so')
    return paths


@lru_cache(maxsize=4)
def _open_library(path):
    try:
        library = ctypes.CDLL(str(path))
        version = library.pmp4010_velocity_abi_version
        version.argtypes = []
        version.restype = ctypes.c_uint32
        actual = version()
        if actual != ABI_VERSION:
            raise ValueError('速度换算库 ABI 版本不匹配：需要 {}，实际 {}'.format(ABI_VERSION, actual))
        convert = library.pmp4010_velocity_convert
        convert.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
                            ctypes.POINTER(ctypes.c_char), ctypes.c_size_t]
        convert.restype = ctypes.c_int
        integer = library.pmp4010_velocity_require_int32
        integer.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32)]
        integer.restype = ctypes.c_int
        return library
    except OSError as exc:
        raise ValueError('无法加载本地速度换算库 {}；请检查文件、二进制架构及依赖：{}'.format(path, exc)) from exc
    except AttributeError as exc:
        raise ValueError('本地速度换算库缺少 ABI v1 接口：{}'.format(path)) from exc


def require_velocity_library():
    """Check the native dependency before any motion bus is opened."""
    paths = _candidate_paths()
    for path in paths:
        if path.is_file():
            return _open_library(str(path.resolve()))
    raise ValueError('缺少本地速度换算库；请安装 libpmp4010_velocity.so，或设置 {} 为绝对路径。已检查：{}'.format(
        LIBRARY_ENV, '、'.join(str(path) for path in paths)))


def _fraction(value):
    if isinstance(value, bool):
        raise ValueError('速度必须为有限有理数，不接受布尔值')
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('速度必须为有限有理数，不接受 NaN/Infinity')
        raise ValueError('速度须用整数、十进制字符串或 Fraction，不接受 float')
    try:
        return Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('速度必须为有限有理数（整数、十进制字符串或 Fraction）') from exc


def _calibration_values(cal):
    try:
        values = cal['values']
        valid = isinstance(values, (list, tuple)) and len(values) == 4 and all(isinstance(v, int) and not isinstance(v, bool)
                    and 1 <= v <= 0xffffffff for v in values)
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('608F/6091四项实读标定值均须为正Uint32，不猜测设备分辨率')
    return values


def _check_result(status, writing=False):
    messages = {
        1: '速度换算参数不是有效有限有理数',
        2: '608F/6091四项标定值均须为正Uint32',
        3: '本地速度换算输出缓冲区不足',
        4: '本地速度换算库内部错误',
        5: '60FFh 写值不是整数；拒绝隐式取整或截断',
        6: '60FFh 写值超出 Int32 范围' if writing else '速度换算超出本地128位精确分数范围',
    }
    if status:
        raise ValueError(messages.get(status, '本地速度换算返回未知错误：{}'.format(status)))


def _convert(value, cal, direction):
    value = _fraction(value)
    values = _calibration_values(cal)
    library = require_velocity_library()
    # ABI v1 bounds each exact numerator/denominator to 128 bits (39 digits).
    numerator, denominator = ctypes.create_string_buffer(64), ctypes.create_string_buffer(64)
    status = library.pmp4010_velocity_convert(direction,
        str(value.numerator).encode('ascii'), str(value.denominator).encode('ascii'),
        *values, numerator, len(numerator), denominator, len(denominator))
    _check_result(status)
    try:
        num, den = int(numerator.value), int(denominator.value)
        if den <= 0:
            raise ValueError('分母非正')
        return Fraction(num, den)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('本地速度换算库返回无效精确分数') from exc


def unit_to_velocity(units_per_s, cal):
    """Signed user units/s -> exact motor rpm, E06 §5.2; C++ owns the formula."""
    return _convert(units_per_s, cal, 0)


def velocity_to_unit(rpm, cal):
    """Exact rpm -> user units/s: algebraic inverse, not a manual quotation."""
    return _convert(rpm, cal, 1)


def velocity_to_int32(rpm, cal):
    """Explicit 60FFh write value: exact Int32 only, with no rounding policy."""
    result = velocity_to_unit(rpm, cal)
    output = ctypes.c_int32()
    status = require_velocity_library().pmp4010_velocity_require_int32(
        str(result.numerator).encode('ascii'), str(result.denominator).encode('ascii'),
        ctypes.byref(output))
    _check_result(status, writing=True)
    return output.value
