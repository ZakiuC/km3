"""603F display vocabulary is checked against the authoritative local manual."""
from pathlib import Path
import unittest

from pmp4010_faults import FAULT_DESCRIPTIONS, fault_description, fault_fields, format_fault


class FaultDescriptionsTests(unittest.TestCase):
    def test_mapping_matches_all_table_rows_and_duplicate_is_synonymous(self):
        root = Path(__file__).resolve().parents[4]
        manual = root / 'docs/硬件手册/PMP4010/协议/一体化低压伺服电机CANopen通信用户手册-E06.md'
        section = manual.read_text().split('### 9.1 报警代码', 1)[1].split('### 9.2', 1)[0]
        expected, rows = {}, 0
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.split('|')]
            if len(cells) == 8 and cells[1].startswith('0x'):
                rows += 1
                code, description = int(cells[6], 16), cells[2].rstrip('*_')
                if code in expected:
                    self.assertEqual(expected[code], description)
                expected[code] = description
        self.assertEqual((rows, len(expected)), (50, 49))
        self.assertEqual(FAULT_DESCRIPTIONS, expected)

    def test_known_unknown_zero_and_missing_preserve_numeric_value(self):
        for code, expected in ((0x2312, '0x2312（电机堵转）'),
                               (0xA123, '0xA123（未知报警）'),
                               (0, '0x0000（无错误码）'), (None, '未知'),
                               (0x3002312, '0x3002312（未知报警）')):
            with self.subTest(code=code):
                self.assertEqual(format_fault(code), expected)
                self.assertEqual(fault_fields(code)['fault_code'], code)
                self.assertEqual(fault_fields(code)['fault_description'], fault_description(code))
        self.assertIsNone(fault_fields(None)['fault_code_hex'])
