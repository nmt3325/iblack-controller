"""No hardware/network access; optional Unicorn tests execute isolated ARM math."""
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import iermu_telnet as telnet


class PacketTests(unittest.TestCase):
    def test_crc_empty(self):
        self.assertEqual(telnet.crc32(b''), 0)

    def test_crc_independent_reference(self):
        for data in (b'123456789', bytes(range(255)), bytes(12), b'\xff'*16):
            c = 0xffffffef
            for b in data:
                c ^= b
                for _ in range(8):
                    c = (c >> 1) ^ (0xedb88320 if c & 1 else 0)
            self.assertEqual(telnet.crc32(data), c ^ 0xffffffef)

    def test_header_layout(self):
        n = bytes(range(16))
        h = telnet.make_header(0x92, n, 48)
        self.assertEqual(len(h), 28)
        self.assertEqual(h[:4], b'\x92\0\1\0')
        self.assertEqual(h[4:8], b'\0\0\0\x30')
        self.assertEqual(h[8:24], n)
        self.assertEqual(struct.unpack('>I', h[24:])[0], telnet.crc32(h[:24]))

    def test_only_control_and_ack_opcodes(self):
        for op in range(256):
            if op in (0x92, 0x93, 0xfe):
                self.assertEqual(len(telnet.make_header(op, bytes(16))), 28)
            else:
                with self.assertRaises(ValueError):
                    telnet.make_header(op, bytes(16))

    def test_nonce_timestamp_positions(self):
        n = telnet.make_nonce(0x12345678)
        self.assertEqual(bytes([n[6], n[8], n[7], n[3]]), b'\x12\x34\x56\x78')

    def test_nonce_time_range(self):
        for stamp in (-1, 0x100000000):
            with self.assertRaises(ValueError):
                telnet.make_nonce(stamp)

    def test_maintenance_flag_little_endian(self):
        h = telnet.make_header(0x93, bytes(16), 48, days=0xfead)
        self.assertEqual(h[2:4], b'\xad\xfe')

    def test_private_unicast_ipv4_only(self):
        for host in ('192.168.10.29', '10.2.3.4', '172.20.1.2'):
            self.assertEqual(telnet.private_ipv4(host), host)
        for host in ('8.8.8.8', '100.87.20.49', '127.0.0.1', '0.0.0.0', '224.0.0.1',
                     '192.168.10.255', '192.168.10.0', 'camera.local', '::1'):
            with self.assertRaises(ValueError):
                telnet.private_ipv4(host)

    def test_unknown_network_action_blocked(self):
        with self.assertRaises(ValueError):
            telnet.apply_request('192.168.10.29', 'update', Path('/not-used'), 1)

    def test_malformed_nonce(self):
        with self.assertRaises(ValueError):
            telnet.make_header(0x92, b'short')


@unittest.skipUnless(importlib.util.find_spec('unicorn'), 'optional unicorn dependency not installed')
class IsolatedFirmwareTests(unittest.TestCase):
    def test_fingerprint_rejects_other_binary(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'not-firmware'
            p.write_bytes(b'not the known firmware')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                telnet.FirmwareMath(p)

    def test_positive_negative_and_expiry_paths(self):
        result = telnet.offline_selftest(telnet.DEFAULT_FIRMWARE)
        self.assertTrue(result['passed'])
        self.assertEqual(result['network_calls'], 0)
        self.assertEqual(result['host_commands_executed'], 0)
        self.assertEqual(result['tests']['enable']['commands'], ['telnetd'])
        self.assertEqual(result['tests']['disable']['commands'], ['killall -9 telnetd'])
        self.assertEqual(result['tests']['bad_cipher_crc']['reply'], [(255, 69)])
        self.assertEqual(result['tests']['bad_plain_crc']['reply'], [(255, 70)])
        self.assertEqual(result['tests']['expired_time']['reply'], [(255, 73)])
        self.assertEqual(result['tests']['maintenance_time']['reply'], [(254, 0)])

    def test_arbitrary_entrypoint_blocked(self):
        math = telnet.FirmwareMath(telnet.DEFAULT_FIRMWARE)
        with self.assertRaisesRegex(ValueError, 'allowlisted'):
            math.call(0xc000)

    def test_embedded_nul_nonce(self):
        math = telnet.FirmwareMath(telnet.DEFAULT_FIRMWARE)
        nonce = b'\0' + b'A'*15
        plain = bytes(range(16))
        encrypted = math.crypt(nonce, plain)
        self.assertEqual(math.crypt(nonce, encrypted, decrypt=True), plain)


if __name__ == '__main__':
    unittest.main()
