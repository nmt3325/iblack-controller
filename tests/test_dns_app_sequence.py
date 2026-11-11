import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('app_dns_camctl', ROOT / 'client/camctl.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)

def body(dhcp=1):
    b = bytearray(44)
    b[3] = dhcp
    for off, ip in [(4, (192,0,2,35)), (8, (255,255,255,0)), (12, (192,0,2,1)), (16, (8,8,8,8)), (20, (8,8,4,4))]:
        b[off:off+4] = bytes(ip)
    b[24:] = bytes(range(20))
    return bytes(b)

def camera(original=None):
    c = ctl.Camera.__new__(ctl.Camera)
    c.get = Mock(return_value=body() if original is None else original)
    c.set = Mock(return_value=b'\x00')
    c.backup = Mock(return_value=Path('/private/synthetic-backup.bin'))
    c.request = Mock(return_value=b'')
    return c

class AppDnsTests(unittest.TestCase):
    def test_static_preview_only_changes_documented_fields(self):
        c = camera()
        r = c.set_dns('192.0.2.201', '192.0.2.22', static=True, app_sequence=True)
        self.assertFalse(r['requested']['dhcp'])
        self.assertEqual(r['requested']['ip'], '192.0.2.35')
        self.assertTrue(set(r['changed_offsets']) <= {3, *range(16,24)})
        c.set.assert_not_called()
        c.request.assert_not_called()
        c.backup.assert_not_called()
    def test_static_requires_explicit_app_sequence(self):
        c = camera()
        with self.assertRaises(ValueError): c.set_dns('192.0.2.201', static=True)
        c.get.assert_not_called()
    def test_app_apply_requires_save(self):
        c = camera()
        with self.assertRaises(ValueError): c.set_dns('192.0.2.201', app_sequence=True, apply=True, experimental=True)
        c.get.assert_not_called()
    def test_app_order_is_set_save_then_reconnect_readback(self):
        c = camera()
        updated = bytearray(ctl.patch_network_dns(body(), '192.0.2.201')); updated[3] = 0; updated = bytes(updated)
        events=[]
        c.set.side_effect=lambda *args: (events.append('set') or b'\x00')
        c.request.side_effect=lambda *args: (events.append('save') or b'')
        c.wait_network_readback=Mock(side_effect=lambda expected: (events.append('readback') or expected))
        r=c.set_dns('192.0.2.201', static=True, app_sequence=True, apply=True, persist=True, experimental=True)
        self.assertEqual(events,['set','save','readback'])
        self.assertTrue(r['saved']); self.assertTrue(r['readback_verified'])
        self.assertFalse(r['effective_dns_verified']); self.assertFalse(r['reboot_persistence_verified'])
        c.wait_network_readback.assert_called_once_with(updated)
        self.assertEqual(c.get.call_count,1)
    def test_app_bad_set_ack_never_saves(self):
        c=camera(); c.set.return_value=b'\x01'
        with self.assertRaises(ctl.DNSUpdateError): c.set_dns('192.0.2.201', app_sequence=True, apply=True, persist=True, experimental=True)
        c.request.assert_not_called()
    def test_app_bad_save_ack_is_not_success(self):
        c=camera(); c.request.return_value=b'\x01'; c.wait_network_readback=Mock()
        with self.assertRaises(ctl.DNSUpdateError) as e: c.set_dns('192.0.2.201', app_sequence=True, apply=True, persist=True, experimental=True)
        self.assertEqual(e.exception.result['failed_stage'],'save')
        self.assertFalse(e.exception.result['saved']); c.wait_network_readback.assert_not_called()
    def test_app_failed_reconnect_reports_partial_state(self):
        c=camera(); c.wait_network_readback=Mock(side_effect=RuntimeError('not recovered'))
        with self.assertRaises(ctl.DNSUpdateError) as e: c.set_dns('192.0.2.201', app_sequence=True, apply=True, persist=True, experimental=True)
        self.assertTrue(e.exception.result['save_acknowledged'])
        self.assertFalse(e.exception.result['saved'])
        self.assertFalse(e.exception.result['applied'])
        self.assertEqual(e.exception.result['failed_stage'],'post_save_readback')
    def test_app_readback_mismatch_does_not_claim_saved(self):
        c=camera(); c.wait_network_readback=Mock(return_value=body())
        with self.assertRaises(ctl.DNSUpdateError) as e: c.set_dns('192.0.2.201', app_sequence=True, apply=True, persist=True, experimental=True)
        self.assertFalse(e.exception.result['saved'])
    def test_static_noop_does_not_write(self):
        c=camera(body(0))
        r=c.set_dns('8.8.8.8', static=True, app_sequence=True, apply=True, persist=True, experimental=True)
        self.assertTrue(r['unchanged']); c.set.assert_not_called(); c.request.assert_not_called()
    def test_reconnect_tolerates_one_timeout_and_closes_peer(self):
        c=camera(); c.s=Mock(); c.s.getpeername.return_value=('192.0.2.35',3357); c.close=Mock()
        peer=Mock(); peer.get.return_value=body(0)
        method=ctl.Camera.wait_network_readback
        with patch.object(ctl.time,'sleep'), patch.object(ctl,'Camera',side_effect=[TimeoutError(),peer]) as factory:
            self.assertEqual(method(c,body(0)),body(0))
            self.assertEqual(factory.call_count,2)
        c.close.assert_called_once(); peer.close.assert_called_once()
    def test_reconnect_mismatch_exhausts_without_writes(self):
        c=camera(); c.s=Mock(); c.s.getpeername.return_value=('192.0.2.35',3357); c.close=Mock()
        peer=Mock(); peer.get.return_value=body(1); method=ctl.Camera.wait_network_readback
        with patch.object(ctl.time,'sleep'), patch.object(ctl,'Camera',return_value=peer) as factory:
            with self.assertRaises(RuntimeError): method(c,body(0))
            self.assertEqual(factory.call_count,6)
        c.set.assert_not_called(); c.request.assert_not_called()

if __name__ == '__main__': unittest.main()
