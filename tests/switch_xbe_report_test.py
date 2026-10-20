"""Compare synthetic XBE reports without original game files."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from report_switch_xbe import compare, inspect


def image() -> bytearray:
    data = bytearray(0x1020)
    data[:4] = b'XBEH'
    base = 0x10000
    fields = {0x104: base, 0x108: 0x1000, 0x10c: 0x3000, 0x118: base + 0x180,
              0x11c: 1, 0x120: base + 0x300, 0x128: (base + 0x1000) ^ 0xa8fc57ab,
              0x158: (base + 0x1010) ^ 0x5b6d40b6, 0x180: 0xb0, 0x188: 0x4d570029,
              0x180 + 0xa0: 7, 0x180 + 0xac: 3}
    for offset, value in fields.items():
        struct.pack_into('<I', data, offset, value)
    name = 'Synthetic fixture'.encode('utf-16le')
    data[0x18c:0x18c + len(name)] = name
    struct.pack_into('<6I', data, 0x300, 6, base + 0x1000, 0x20, 0x1000, 0x20, base + 0x350)
    data[0x350:0x356] = b'.text\0'
    data[0x1000:] = bytes(range(32))
    return data


class XbeReportTest(unittest.TestCase):
    def test_identical_image(self):
        report = inspect(bytes(image()))
        result = compare(report, report)
        self.assertTrue(result['exact_file_match'])
        self.assertTrue(result['layout_and_section_hashes_match'])
        self.assertFalse(result['runtime_compatibility_proven'])

    def test_header_change_does_not_imply_changed_machine_code(self):
        original = image()
        changed = bytearray(original)
        struct.pack_into('<I', changed, 0x180 + 0xa0, 1)
        result = compare(inspect(bytes(changed)), inspect(bytes(original)))
        self.assertFalse(result['exact_file_match'])
        self.assertFalse(result['header_matches'])
        self.assertTrue(result['layout_and_section_hashes_match'])
        self.assertFalse(result['runtime_compatibility_proven'])

    def test_instruction_change_is_detected(self):
        original = image()
        changed = bytearray(original)
        changed[0x1000] ^= 1
        result = compare(inspect(bytes(changed)), inspect(bytes(original)))
        self.assertFalse(result['layout_and_section_hashes_match'])
        self.assertEqual(result['different_section_indices'], [0])

    def test_entry_change_is_detected(self):
        original = image()
        changed = bytearray(original)
        struct.pack_into('<I', changed, 0x128, 0x11004 ^ 0xa8fc57ab)
        result = compare(inspect(bytes(changed)), inspect(bytes(original)))
        self.assertEqual(result['layout_differences'], ['entry'])

    def test_section_outside_file_is_rejected(self):
        changed = image()
        struct.pack_into('<I', changed, 0x300 + 12, 0x2000)
        with self.assertRaisesRegex(ValueError, 'out-of-range'):
            inspect(bytes(changed))

    def test_truncated_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'image/header size'):
            inspect(bytes(image()[:0x180]))


if __name__ == '__main__':
    unittest.main()
