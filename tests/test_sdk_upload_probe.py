import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sdk_upload_probe', ROOT / 'tools/sdk_upload_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SyntheticReplyTests(unittest.TestCase):
    def test_length_and_command(self):
        p = probe.synthetic_reply()
        self.assertEqual(len(p), 76)
        self.assertEqual(struct.unpack_from('<I', p, 4)[0], 0x01010818)

    def test_crc32_fixture(self):
        p = probe.synthetic_reply()
        self.assertEqual(struct.unpack_from('<I', p)[0], zlib.crc32(p[4:]))

    def test_corrupted_crc_is_explicit_fixture(self):
        p = probe.synthetic_reply(corrupt_crc=True)
        self.assertNotEqual(struct.unpack_from('<I', p)[0], zlib.crc32(p[4:]))

    def test_endpoint_layout(self):
        self.assertEqual(probe.synthetic_reply()[12:18], probe.ENDPOINT)
        self.assertEqual(struct.unpack('<IH', probe.ENDPOINT), (0xC6336407, 1935))

    def test_wrong_command_and_status_fixtures(self):
        p = probe.synthetic_reply(command=0x01010819, status=1)
        self.assertEqual(p[8], 1)
        self.assertEqual(struct.unpack_from('<I', p, 4)[0], 0x01010819)


class NativeFirmwareTests(unittest.TestCase):
    def test_arm_instruction_probes(self):
        try:
            import unicorn  # noqa: F401
            import elftools  # noqa: F401
        except ImportError:
            self.skipTest('Optional offline analysis dependencies: unicorn and pyelftools')
        result = probe.FirmwareProbe().run_all()
        self.assertTrue(result['passed'])
        self.assertEqual(result['tests'], 23)
        self.assertFalse(result['network_allowed'])

    def test_wrong_firmware_hash_rejected(self):
        try:
            import unicorn  # noqa: F401
            import elftools  # noqa: F401
        except ImportError:
            self.skipTest('Optional offline analysis dependencies unavailable')
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'not-firmware'
            p.write_bytes(b'not an ELF')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                probe.FirmwareProbe(p)
