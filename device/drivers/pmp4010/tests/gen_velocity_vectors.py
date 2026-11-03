#!/usr/bin/env python3
"""Adapt the shared exact test data to C++; contains no conversion formula."""
import json
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
vectors = json.loads(source.read_text())['vectors']
lines = ['// Build-time adapter for velocity_vectors.json; do not edit.', '#pragma once',
         'namespace velocity_vectors {',
         'struct Case { const char* name; int direction; unsigned calibration[4]; const char* numerator; const char* denominator; const char* expected_numerator; const char* expected_denominator; int error; int int32_error; };',
         'inline constexpr Case cases[] = {']
for vector in vectors:
    cal = vector['calibration']
    assert len(cal) == 4 and all(type(value) is int and 0 <= value <= 0xffffffff for value in cal)
    expected = vector.get('expected', ['', ''])
    values = [json.dumps(vector['name']), str(vector['direction']),
              '{' + ','.join(str(value) + 'u' for value in cal) + '}',
              *(json.dumps(value) for value in vector['input']),
              *(json.dumps(value) for value in expected), str(vector.get('error', 0)), str(vector.get('int32_error', -1))]
    lines.append('    {' + ','.join(values) + '},')
lines.extend(['};', '}  // namespace velocity_vectors', ''])
target.write_text('\n'.join(lines))
