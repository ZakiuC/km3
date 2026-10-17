"""Structured parameter widths/ranges may not silently turn into unsafe plans."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('gen_od_params', ROOT / 'tools/hwdriver/gen-od.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class ParameterMetadataTest(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / 'device/drivers/pmp4010/driver/od.json').read_text())
        self.entry = next(e for e in self.doc['entries'] if e['key'] == 'interpolation_period')

    def test_complete_subobjects_and_signed_exponent(self):
        objects = generator.parameter_objects(self.doc)
        self.assertEqual(len(objects), 12)
        exponent = next(o for o in objects if o['type'] == 'int8')
        self.assertEqual(exponent['ranges'], [[-128, 127]])
        self.assertEqual(exponent['sub'], 2)
        commands = [o for o in objects if o['role'] == 'command']
        self.assertEqual([o['sub'] for o in commands], [1, 11])

    def test_reject_invalid_width_range_address_source_and_duplicates(self):
        original = copy.deepcopy(self.entry['parameter_objects'])
        for key, value in [('type', 'int7'), ('ranges', [[-1, 255]]),
                           ('ranges', [[0, 256]]), ('ranges', [[False, 1]]),
                           ('ranges', []), ('ranges', [[3, 1]]),
                           ('index', '0x10000'), ('sub', True), ('role', 'command')]:
            self.entry['parameter_objects'] = copy.deepcopy(original)
            self.entry['parameter_objects'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                generator.parameter_objects(self.doc)
        self.entry['parameter_objects'] = original + [original[0]]
        with self.assertRaises(ValueError):
            generator.parameter_objects(self.doc)
        self.entry['parameter_objects'] = original
        self.entry['status'] = 'assumed'
        with self.assertRaises(ValueError):
            generator.parameter_objects(self.doc)


if __name__ == '__main__':
    unittest.main()
