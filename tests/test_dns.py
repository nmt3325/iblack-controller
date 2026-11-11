import contextlib
import importlib.util
import io
import ipaddress
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dns_camctl', ROOT / 'client/camctl.py')
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def sample(dhcp=1):
    body = bytearray(range(44))  # Synthetic tail, never the device's hardware identity.
    body[3] = dhcp
    for offset, value in [(4, '192.0.2.34'), (8, '255.255.255.0'),
                          (12, '192.0.2.1'), (16, '8.8.8.8'), (20, '8.8.4.4')]:
        body[offset:offset + 4] = ipaddress.IPv4Address(value).packed
    return bytes(body)


def fake_camera(original=None, after=None):
    original = original if original is not None else sample()
    after = after if after is not None else ctl.patch_network_dns(original, '1.1.1.1')
    camera = ctl.Camera.__new__(ctl.Camera)
    camera.get = Mock(side_effect=[original, after, after])
    camera.set = Mock(return_value=b'\x00')
    camera.request = Mock(return_value=b'')
    camera.backup = Mock(return_value=Path('/private/backup-param-8.bin'))
    return camera


class DnsTests(unittest.TestCase):
    def test_parse_redacts_unknown_tail(self):
        result = ctl.parse_network_config(sample())
        self.assertEqual(result['primary_dns'], '8.8.8.8')
        self.assertEqual(result['secondary_dns'], '8.8.4.4')
        self.assertTrue(result['dhcp'])
        self.assertEqual(set(result), {'dhcp', 'ip', 'netmask', 'gateway', 'primary_dns', 'secondary_dns'})

    def test_patch_primary_preserves_every_other_byte(self):
        original = sample()
        result = ctl.patch_network_dns(original, '1.1.1.1')
        self.assertEqual(result[:16], original[:16])
        self.assertEqual(result[20:], original[20:])
        self.assertEqual(result[16:20], b'\x01' * 4)

    def test_patch_two_dns_fields_preserves_network_and_identity(self):
        original = sample()
        result = ctl.patch_network_dns(original, '1.1.1.1', '9.9.9.9')
        self.assertEqual(result[:16] + result[24:], original[:16] + original[24:])
        self.assertEqual(result[20:24], b'\x09' * 4)

    def test_secondary_zero_is_allowed(self):
        result = ctl.patch_network_dns(sample(), '1.1.1.1', '0.0.0.0')
        self.assertEqual(result[20:24], bytes(4))

    def test_bad_primary_values(self):
        for value in ('0.0.0.0', '255.255.255.255', '224.0.0.1', 'example.com', '::1', '1.2.3.999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ctl.patch_network_dns(sample(), value)

    def test_unknown_layout_is_rejected(self):
        for body in (bytes(43), bytes(45), sample(2), b'\x4a\x08\x00\x2c' + sample()):
            with self.subTest(length=len(body)), self.assertRaises(RuntimeError):
                ctl.parse_network_config(body)

    def test_preview_never_writes_or_creates_backup(self):
        camera = fake_camera()
        result = camera.set_dns('1.1.1.1')
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['applied'])
        self.assertFalse(result['effective_dns_verified'])
        self.assertTrue(set(result['changed_offsets']) <= set(range(16, 24)))
        camera.set.assert_not_called()
        camera.request.assert_not_called()
        camera.backup.assert_not_called()

    def test_live_apply_requires_explicit_experimental_flag(self):
        camera = fake_camera()
        with self.assertRaises(ValueError):
            camera.set_dns('1.1.1.1', apply=True)
        camera.get.assert_not_called()

    def test_save_requires_apply(self):
        camera = fake_camera()
        with self.assertRaises(ValueError):
            camera.set_dns('1.1.1.1', persist=True)
        camera.get.assert_not_called()

    def test_invalid_input_is_rejected_before_get(self):
        camera = fake_camera()
        with self.assertRaises(ValueError):
            camera.set_dns('::1')
        camera.get.assert_not_called()

    def test_noop_does_not_write_or_save(self):
        camera = fake_camera()
        result = camera.set_dns('8.8.8.8', apply=True, persist=True, experimental=True)
        self.assertTrue(result['unchanged'])
        camera.backup.assert_not_called()
        camera.set.assert_not_called()
        camera.request.assert_not_called()

    def test_apply_exact_readback_without_implicit_save(self):
        camera = fake_camera()
        result = camera.set_dns('1.1.1.1', apply=True, experimental=True)
        self.assertTrue(result['applied'])
        self.assertTrue(result['readback_verified'])
        self.assertFalse(result['saved'])
        self.assertFalse(result['effective_dns_verified'])
        self.assertTrue(result['warnings'])
        camera.backup.assert_called_once_with(8, sample())
        camera.set.assert_called_once_with(8, ctl.patch_network_dns(sample(), '1.1.1.1'))
        camera.request.assert_not_called()

    def test_save_only_after_exact_readback(self):
        camera = fake_camera()
        events = []
        camera.set.side_effect = lambda *args: (events.append('set') or b'\x00')
        camera.backup.side_effect = lambda *args: (events.append('backup') or Path('/private/saved.bin'))
        camera.request.side_effect = lambda *args: (events.append('save') or b'')
        result = camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        self.assertEqual(events, ['backup', 'set', 'save'])
        self.assertTrue(result['saved'])
        self.assertTrue(result['save_acknowledged'])
        self.assertFalse(result['reboot_persistence_verified'])
        camera.request.assert_called_once_with(65, 3)
        self.assertEqual(camera.get.call_count, 3)

    def test_bad_ack_never_saves(self):
        camera = fake_camera()
        camera.set.return_value = b'\x01'
        with self.assertRaises(ctl.DNSUpdateError) as raised:
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        self.assertEqual(raised.exception.result['failed_stage'], 'set')
        camera.request.assert_not_called()

    def test_readback_mismatch_never_saves(self):
        camera = fake_camera(after=sample())
        with self.assertRaises(ctl.DNSUpdateError) as raised:
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        self.assertEqual(raised.exception.result['failed_stage'], 'readback')
        camera.request.assert_not_called()

    def test_nondns_readback_change_never_saves(self):
        after = bytearray(ctl.patch_network_dns(sample(), '1.1.1.1'))
        after[3] = 0
        camera = fake_camera(after=bytes(after))
        with self.assertRaises(ctl.DNSUpdateError):
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        camera.request.assert_not_called()

    def test_save_failure_reports_partial_state(self):
        camera = fake_camera()
        camera.request.side_effect = RuntimeError('CMS rejected save')
        with self.assertRaises(ctl.DNSUpdateError) as raised:
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        result = raised.exception.result
        self.assertTrue(result['applied'])
        self.assertFalse(result['saved'])
        self.assertEqual(result['failed_stage'], 'save')
        self.assertIn('backup', result)

    def test_disconnect_does_not_invent_success(self):
        camera = fake_camera()
        camera.get.side_effect = [sample(), OSError('connection lost')]
        with self.assertRaises(ctl.DNSUpdateError) as raised:
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        self.assertFalse(raised.exception.result['applied'])
        camera.request.assert_not_called()

    def test_post_save_mismatch_is_not_saved_success(self):
        camera = fake_camera()
        camera.get.side_effect = [sample(), ctl.patch_network_dns(sample(), '1.1.1.1'), sample()]
        with self.assertRaises(ctl.DNSUpdateError) as raised:
            camera.set_dns('1.1.1.1', apply=True, persist=True, experimental=True)
        self.assertEqual(raised.exception.result['failed_stage'], 'post_save_readback')
        self.assertTrue(raised.exception.result['save_acknowledged'])
        self.assertFalse(raised.exception.result['saved'])

    def test_restore_only_uses_backup_dns_not_old_ip(self):
        old = bytearray(sample())
        old[4:8] = ipaddress.IPv4Address('198.51.100.7').packed
        current = ctl.patch_network_dns(sample(), '1.1.1.1', '9.9.9.9')
        camera = fake_camera(original=current)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'backup.bin'
            path.write_bytes(old)
            result = camera.restore_dns(path)
        self.assertEqual(result['requested']['primary_dns'], '8.8.8.8')
        self.assertEqual(result['requested']['secondary_dns'], '8.8.4.4')
        self.assertEqual(result['requested']['ip'], '192.0.2.34')
        camera.set.assert_not_called()

    def test_backups_are_unique_and_private(self):
        camera = ctl.Camera.__new__(ctl.Camera)
        with tempfile.TemporaryDirectory() as directory, patch.object(ctl, 'D', Path(directory)):
            first = camera.backup(8, sample())
            second = camera.backup(8, sample())
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), sample())
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)

    def test_bad_cli_is_rejected_without_connecting(self):
        commands = [['dns-set', '::1'], ['dns-set', '1.1.1.1', '--save'],
                    ['dns-set', '1.1.1.1', '--apply'], ['get', '8'],
                    ['dns', '--apply'], ['night', 'wrong']]
        for command in commands:
            with self.subTest(command=command), patch.object(ctl, 'Camera') as camera, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    ctl.main(command)
                self.assertEqual(raised.exception.code, 2)
                camera.assert_not_called()


if __name__ == '__main__':
    unittest.main()
