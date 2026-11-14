import gzip
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_inventory import kernel_evidence, read_regular, wifi_profile


class KernelTests(unittest.TestCase):
    def image(self, inner):
        zimage = bytearray(64)
        struct.pack_into('<I', zimage, 0x24, 0x016F2818)
        return bytes(64) + zimage + gzip.compress(inner)

    def test_split_evidence_is_not_bootproof(self):
        data = self.image(b'Linux version 3.0.8\0mtd_split_layout_register\0'
                          b'split NOR layout: root on mtdblock%d\0rootfs_data\0')
        result = kernel_evidence(data)
        self.assertTrue(all(result['split_markers'].values()))
        self.assertTrue(result['markers_do_not_prove_actual_boot'])
        self.assertEqual(result['linux_version'], '3.0.8')

    def test_missing_magic_rejected(self):
        with self.assertRaises(ValueError):
            kernel_evidence(bytes(128))

    def test_missing_kernel_rejected(self):
        with self.assertRaises(ValueError):
            kernel_evidence(self.image(b'not a Linux kernel'))


class ProfileTests(unittest.TestCase):
    def test_profile_names_are_not_interchangeable(self):
        text = ('if [ "$1" = "mt7601u-generic" ]; then\n\tmodprobe mt7601u\nfi\n'
                'if [ "$1" = "mt7601u-hi3518ev100-camhi" ]; then\n\tmodprobe mt7601sta\nfi\n')
        self.assertEqual(wifi_profile(text, 'mt7601u-generic')['modules'], ['mt7601u'])
        self.assertEqual(wifi_profile(text, 'mt7601u-hi3518ev100-camhi')['modules'], ['mt7601sta'])

    def test_absent_profile_rejected(self):
        with self.assertRaises(ValueError):
            wifi_profile('', 'mt7601u-hi3518ev100-camhi')

    def test_device_input_rejected(self):
        with self.assertRaises(ValueError):
            read_regular(Path('/dev/null'), 4096)


if __name__ == '__main__':
    unittest.main()
