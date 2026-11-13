import struct
import unittest
from offline_inspect import model_nameservers, inspect_update_header

class DnsModelTests(unittest.TestCase):
    def test_configured_dns_first(self):
        self.assertEqual(model_nameservers('192.0.2.1','192.0.2.2'),['192.0.2.2','114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_absent_configured_dns(self):
        self.assertEqual(model_nameservers('192.0.2.1','0.0.0.0'),['114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_dns_equals_gateway(self):
        self.assertEqual(model_nameservers('192.0.2.1','192.0.2.1'),['114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_google_special_case(self):
        self.assertEqual(model_nameservers('192.0.2.1','8.8.8.8'),['114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_public114_special_case(self):
        self.assertEqual(model_nameservers('192.0.2.1','114.114.114.114'),['114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_dhcp_filtered_and_terminated(self):
        self.assertEqual(model_nameservers('192.0.2.1','192.0.2.2',['8.8.8.8','192.0.2.1','192.0.2.2','192.0.2.3','114.114.114.114','0.0.0.0','192.0.2.4']),['192.0.2.2','192.0.2.3','114.114.114.114','8.8.8.8','192.0.2.1'])
    def test_gateway_not_deduplicated_after_public(self):
        self.assertEqual(model_nameservers('8.8.8.8','0.0.0.0'),['114.114.114.114','8.8.8.8','8.8.8.8'])
    def test_invalid_ipv4(self):
        with self.assertRaises(ValueError):model_nameservers('not-ip','0.0.0.0')

class HeaderTests(unittest.TestCase):
    def sample(self):
        words=[0x02000001,2,3,4,5,6,0x12340005,8]
        return struct.pack('<9I',*words,sum(words)&0xffffffff)
    def test_short(self):
        with self.assertRaises(ValueError):inspect_update_header(bytes(35))
    def test_additive_checksum(self):
        r=inspect_update_header(self.sample());self.assertTrue(r['header_sum_matches']);self.assertFalse(r['whole_update_validated'])
    def test_changed_word_fails(self):
        data=bytearray(self.sample());data[4]^=1;self.assertFalse(inspect_update_header(data)['header_sum_matches'])
    def test_field_offsets(self):
        r=inspect_update_header(self.sample());self.assertEqual((r['type_byte_at_3'],r['compat_id_at_0x14'],r['compat_selector_low_nibble_at_0x18'],r['compat_revision_u16_at_0x1a']),(2,6,5,0x1234))

if __name__=='__main__':unittest.main()
