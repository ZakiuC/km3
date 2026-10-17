"""Reject invalid mapping metadata before it can become a wire constant."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('gen_od_pdo', ROOT / 'tools/hwdriver/gen-od.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class PdoGenerationTest(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / 'device/drivers/pmp4010/driver/od.json').read_text())
        self.entry = next(e for e in self.doc['entries'] if e['key'] == 'pdo_default_mapping')

    def test_all_lengths_follow_active_entry_count(self):
        layouts = generator.pdo_layouts(self.doc)
        self.assertEqual([sum(int(v, 0) & 255 for v in p['entries']) // 8 for p in layouts],
                         [3, 8, 6, 2, 3, 8, 4, 2])

    def test_reject_count_width_overflow_duplicate_and_unverified_source(self):
        original = copy.deepcopy(self.entry['pdo_layouts'])
        for key, value in [('entry_count', 1), ('entry_count', True),
                           ('entries', ['0x6040000f', '0x60600008']),
                           ('entries', ['0x60400040', '0x60600008']),
                           ('entries', ['0x00000010', '0x60600008']),
                           ('cob_base', '0x800'), ('mapping_index', '0x10000')]:
            self.entry['pdo_layouts'] = copy.deepcopy(original)
            self.entry['pdo_layouts'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                generator.pdo_layouts(self.doc)
        self.entry['pdo_layouts'] = original + [original[0]]
        with self.assertRaises(ValueError):
            generator.pdo_layouts(self.doc)
        self.entry['pdo_layouts'] = original
        self.entry['status'] = 'assumed'
        with self.assertRaises(ValueError):
            generator.pdo_layouts(self.doc)


if __name__ == '__main__':
    unittest.main()
