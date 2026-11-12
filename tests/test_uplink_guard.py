import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr

SOURCE = Path(__file__).resolve().parents[1] / 'deployment/sbin/iermu-home-uplink-guard'
loader = importlib.machinery.SourceFileLoader('uplink_guard', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
guard = importlib.util.module_from_spec(spec)
loader.exec_module(guard)


class RouteGuardTests(unittest.TestCase):
    def row(self, **changes):
        row = {'dev': guard.IFACE, 'prefsrc': guard.SOURCE_IP}
        row.update(changes)
        return json.dumps([row])

    def test_direct_route(self):
        self.assertTrue(guard.route_matches(self.row()))

    def test_wrong_interface(self):
        self.assertFalse(guard.route_matches(self.row(dev='ens18')))

    def test_unrelated_gateway(self):
        self.assertFalse(guard.route_matches(self.row(gateway='192.168.1.1')))

    def test_wrong_source(self):
        self.assertFalse(guard.route_matches(self.row(prefsrc='192.168.1.201')))

    def test_empty_rows(self):
        self.assertFalse(guard.route_matches('[]'))

    def test_invalid_json(self):
        self.assertFalse(guard.route_matches('not json'))

    def test_null_row(self):
        self.assertFalse(guard.route_matches('[null]'))

    def test_missing_interface(self):
        self.assertFalse(guard.route_matches(json.dumps([{'prefsrc': guard.SOURCE_IP}])))

    def test_missing_source(self):
        self.assertFalse(guard.route_matches(json.dumps([{'dev': guard.IFACE}])))

    def test_carrier_down_does_not_query(self):
        with patch.object(guard.Path, 'read_text', return_value='0\n'), patch.object(guard, 'cmd') as cmd:
            self.assertFalse(guard.route_ready())
            cmd.assert_not_called()

    def test_missing_interface_does_not_query(self):
        with patch.object(guard.Path, 'read_text', side_effect=FileNotFoundError), patch.object(guard, 'cmd') as cmd:
            self.assertFalse(guard.route_ready())
            cmd.assert_not_called()

    def test_route_query_failure(self):
        with patch.object(guard.Path, 'read_text', return_value='1\n'), patch.object(guard, 'cmd', return_value=subprocess.CompletedProcess([], 1, self.row(), '')):
            self.assertFalse(guard.route_ready())

    def test_ready_queries_only_route(self):
        with patch.object(guard.Path, 'read_text', return_value='1\n'), patch.object(guard, 'cmd', return_value=subprocess.CompletedProcess([], 0, self.row(), '')) as cmd:
            self.assertTrue(guard.route_ready())
            cmd.assert_called_once_with([guard.IP, '-j', '-4', 'route', 'get', guard.CAMERA_IP])

    def test_command_timeout(self):
        with patch.object(guard.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ip', 12)):
            self.assertEqual(guard.cmd(['ip']).returncode, 124)

    def test_check_success_without_root(self):
        with patch.object(sys, 'argv', ['guard', '--check']), patch.object(guard, 'route_ready', return_value=True):
            self.assertEqual(guard.main(), 0)

    def test_check_not_ready(self):
        with patch.object(sys, 'argv', ['guard', '--check']), patch.object(guard, 'route_ready', return_value=False):
            self.assertEqual(guard.main(), 1)

    def test_no_recovery_mode(self):
        with patch.object(sys, 'argv', ['guard']), patch.object(guard, 'route_ready') as ready, redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(), 2)
            ready.assert_not_called()


if __name__ == '__main__':
    unittest.main()
