#!/usr/bin/env python3
"""Offline liveness regressions: read-only diagnostics do not stop vendor services,
metadata is confirmed before bulk reads, and standby status remains observable.
"""
from __future__ import annotations
import io
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import make_bundle, private_tmp, rmtree, sm, synthetic_files
from sb_fixture import BUSYBOX, Camera, sl


@unittest.skipUnless(BUSYBOX, "needs a host BusyBox")
class DiagnosisLiveness(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-diag-live")
        self.addCleanup(rmtree, self.tmp)
        self.cam = Camera(self.tmp)
        self.trace = self.tmp / "hook.trace"
        self.saved = self.tmp / "saved"
        self.saved.mkdir(mode=0o700)
        self.hook = self.tmp / "hook.sh"
        self.hook.write_text('''#!/bin/sh
printf '%s %s\\n' "${1:-}" "${2:-}" >> "$TRACE_FILE"
if [ "$1" = progress ] && [ "${2:-}" = diag-pass-1-part-0 ]; then
  [ ! -f "$SAVED_DIR/diag-info" ] || : > "$SAVED_DIR/info-before-hash"
fi
if [ "$1" = upload ]; then
  [ "${FAIL_INFO:-0}:$2" != 1:diag-info ] || exit 1
  [ "${FAIL_BLOCKS:-0}:$2" != 1:diag-blocks ] || exit 1
  cp "$3" "$SAVED_DIR/$2" || exit 1
fi
exit 0
''')

    def run_action(self, action="diagnose", **env):
        return self.cam.run(action, extra=("--hook", str(self.hook)),
                            env={"TRACE_FILE": str(self.trace), "SAVED_DIR": str(self.saved), **env})

    def calls(self):
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def test_diagnose_keeps_vendor_services_running(self):
        rc, out = self.run_action()
        self.assertEqual(rc, 0, out)
        self.assertFalse(any(s.split()[0] in ("quiesce", "resume") for s in self.calls()))
        self.assertEqual(self.cam.flash(), self.cam.original)

    def test_info_is_confirmed_before_first_bulk_read(self):
        rc, out = self.run_action()
        self.assertEqual(rc, 0, out)
        calls = self.calls()
        self.assertIn("progress diag-pass-1-part-0", calls)
        self.assertLess(calls.index("upload diag-info"), calls.index("progress diag-pass-1-part-0"))
        self.assertTrue((self.saved / "info-before-hash").exists())
        self.assertIn("== uptime\n", (self.saved / "diag-info").read_text())
        for n in range(1, 4):
            for part in range(6):
                self.assertIn(f"progress diag-pass-{n}-part-{part}", calls)

    def test_info_upload_failure_stops_before_bulk_reads(self):
        rc, out = self.run_action(FAIL_INFO="1")
        self.assertNotEqual(rc, 0, out)
        self.assertFalse(any(s.startswith("progress diag-pass-") for s in self.calls()))
        self.assertFalse((self.saved / "diag-blocks").exists())
        self.assertEqual(self.cam.flash(), self.cam.original)

    def test_later_upload_failure_preserves_early_info(self):
        rc, out = self.run_action(FAIL_BLOCKS="1")
        self.assertNotEqual(rc, 0, out)
        self.assertTrue((self.saved / "diag-info").is_file())
        self.assertTrue((self.saved / "info-before-hash").exists())
        self.assertEqual(self.cam.flash(), self.cam.original)

    def test_write_action_still_requires_quiesce(self):
        self.cam.payload({"env-probe.img": sl.build_probe(self.cam.e0)[0]})
        rc, out = self.run_action("probe-arm")
        self.assertEqual(rc, 0, out)
        self.assertTrue(any(s.split()[0] == "quiesce" for s in self.calls()))
        self.assertNotEqual(self.cam.senv(), self.cam.e0)


class StandbyObservability(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-standby-live")
        self.addCleanup(rmtree, self.tmp)
        root = self.tmp / "bundle"
        make_bundle(root, synthetic_files())
        self.bundle = sm.load_bundle(root)
        self.backups = self.tmp / "events"

    def server(self, mode="standby", **kw):
        srv = sm.MigrationServer(self.bundle, mode, bind="127.0.0.1", port=0,
                                 out=io.StringIO(), backup_dir=self.backups, **kw)
        self.addCleanup(srv.httpd.server_close)
        return srv

    def test_standby_logs_without_enabling_an_action(self):
        srv = self.server()
        srv.record("1", "standby")
        event_file = self.backups / "events.log"
        self.assertTrue(event_file.is_file())
        self.assertIn(" 1 standby\n", event_file.read_text())
        self.assertEqual(event_file.stat().st_mode & 0o777, 0o600)
        self.assertFalse(srv.go or srv.write or srv.reboot)
        self.assertEqual(srv.servable("runtime/busybox"), None)
        self.assertIn(b"mode=standby", srv.manifest())

    def test_standby_rejects_nonprivate_event_directory(self):
        self.backups.mkdir(mode=0o755)
        self.backups.chmod(0o755)  # independent of another test's process-wide umask
        with self.assertRaises((sm.BundleError, ValueError, OSError)):
            self.server()

    def test_standby_does_not_follow_event_log_symlink(self):
        self.backups.mkdir(mode=0o700)
        other = self.tmp / "untouched"
        other.write_text("unchanged")
        (self.backups / "events.log").symlink_to(other)
        srv = self.server()
        with self.assertRaises(OSError):
            srv.record("1", "standby")
        self.assertEqual(other.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
