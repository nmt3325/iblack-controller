#!/usr/bin/env python3
"""Offline tests for serve_migration.py (loopback HTTP only; no camera)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import unittest

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import (HOOK, INSTALLER, STAGE2, bma, http, make_bundle, private_tmp, rbytes, rmtree, sm,
                        start_server, synthetic_files)


class BundleLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-srv")
        self.files = synthetic_files()
        self.bundle = self.tmp / "bundle"
        self.run_id, _ = make_bundle(self.bundle, self.files)

    def tearDown(self):
        rmtree(self.tmp)

    def test_valid_bundle_loads_in_both_env_modes(self):
        b = sm.load_bundle(self.bundle)
        self.assertEqual((b.run_id, b.env_mode, b.port), (self.run_id, "image", 8608))
        self.assertEqual({rel: f.data for rel, f in b.files.items()}, self.files)
        erase = self.tmp / "erase"
        make_bundle(erase, synthetic_files("erase"), env_mode="erase")
        self.assertNotIn(sm.ENV_FILE, sm.load_bundle(erase).files)

    def test_tampered_served_file_is_rejected(self):
        p = self.bundle / "serve" / "runtime" / "busybox"
        p.write_bytes(p.read_bytes() + b"x")
        with self.assertRaisesRegex(sm.BundleError, "differs from bundle.json"):
            sm.load_bundle(self.bundle)

    def test_conf_pin_mismatch_is_rejected(self):
        p = self.bundle / "conf" / "migrate.conf"
        text = p.read_text()
        fw = sm.parse_conf(text)["OM_FW_SHA256"]
        p.write_text(text.replace(fw, "0" * 64))
        with self.assertRaisesRegex(sm.BundleError, "OM_FW_SHA256"):
            sm.load_bundle(self.bundle)

    def test_file_list_and_schema_must_match_the_protocol(self):
        meta_path = self.bundle / "bundle.json"
        meta = json.loads(meta_path.read_text())
        meta["files"].pop("payload/env.img")
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(sm.BundleError, "file list"):
            sm.load_bundle(self.bundle)
        meta["schema"] = "other/1"
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(sm.BundleError, "schema"):
            sm.load_bundle(self.bundle)

    def test_bundle_inside_the_repository_is_refused(self):
        with self.assertRaisesRegex(sm.BundleError, "inside the Git repository"):
            sm.load_bundle(sm.REPO_ROOT / "research")


class ServerConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-cfg")
        make_bundle(self.tmp / "bundle", synthetic_files())
        self.bundle = sm.load_bundle(self.tmp / "bundle")

    def tearDown(self):
        rmtree(self.tmp)

    def make(self, mode, **kw):
        srv = sm.MigrationServer(self.bundle, mode, bind="127.0.0.1", port=0, **kw)
        srv.httpd.server_close()
        return srv

    def test_phrases_and_backup_dir_are_enforced(self):
        bd = self.tmp / "backups"
        with self.assertRaises(ValueError):
            self.make("write", backup_dir=bd)
        with self.assertRaises(ValueError):
            self.make("write", backup_dir=bd, write_confirm="yes")
        with self.assertRaises(ValueError):
            self.make("rehearse", backup_dir=bd, write_confirm=sm.WRITE_PHRASE)
        with self.assertRaises(ValueError):
            self.make("rehearse", backup_dir=bd, reboot_confirm=sm.REBOOT_PHRASE)
        with self.assertRaises(ValueError):
            self.make("rehearse")
        with self.assertRaises(ValueError):
            self.make("write", backup_dir=bd, write_confirm=sm.WRITE_PHRASE, reboot_confirm="yes")
        with self.assertRaises(ValueError):
            self.make("rehearse", backup_dir=sm.REPO_ROOT / "research" / "x")
        loose = self.tmp / "loose"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        with self.assertRaises(ValueError):
            self.make("rehearse", backup_dir=loose)
        self.make("standby")
        self.make("write", backup_dir=bd, write_confirm=sm.WRITE_PHRASE, reboot_confirm=sm.REBOOT_PHRASE)

    def test_manifest_per_mode(self):
        run = f"run={self.bundle.run_id}"
        bd = self.tmp / "backups"
        cases = {
            ("standby", ()): [run, "mode=standby"],
            ("abort", ()): [run, "mode=abort"],
            ("rehearse", ("bd",)): [run, "mode=go", f"go={sm.GO_PHRASE}"],
            ("write", ("bd", "w")): [run, "mode=go", f"go={sm.GO_PHRASE}", f"write={sm.WRITE_PHRASE}"],
            ("write", ("bd", "w", "r")): [run, "mode=go", f"go={sm.GO_PHRASE}", f"write={sm.WRITE_PHRASE}",
                                          f"reboot={sm.REBOOT_PHRASE}"],
        }
        for (mode, opts), lines in cases.items():
            kw = {}
            if "bd" in opts:
                kw["backup_dir"] = bd
            if "w" in opts:
                kw["write_confirm"] = sm.WRITE_PHRASE
            if "r" in opts:
                kw["reboot_confirm"] = sm.REBOOT_PHRASE
            self.assertEqual(self.make(mode, **kw).manifest().decode().splitlines(), lines, (mode, opts))


class HTTPBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-http")
        self.files = synthetic_files()
        self.run_id, _ = make_bundle(self.tmp / "bundle", self.files)
        self.backups = self.tmp / "backups"
        self.srv, self.out = start_server(self.tmp / "bundle", "rehearse", backup_dir=self.backups)
        host, port = self.srv.address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.srv.stop()
        rmtree(self.tmp)

    def url(self, path, run=None, extra=""):
        return f"{self.base}/{path}?run={self.run_id if run is None else run}{extra}"

    def test_every_endpoint_requires_the_run_id(self):
        self.assertEqual(http("GET", f"{self.base}/manifest")[0], 403)
        self.assertEqual(http("GET", self.url("manifest", run="0" * 32))[0], 403)
        self.assertEqual(http("GET", self.url("runtime/busybox", run=self.run_id[:-1]))[0], 403)
        self.assertEqual(http("POST", self.url("report", run="x"), b"log")[0], 403)
        self.assertEqual(self.srv.refused, 4)
        self.assertEqual(self.srv.served, {})

    def test_manifest_status_and_unknown_paths(self):
        code, body = http("GET", self.url("manifest"))
        self.assertEqual((code, body), (200, self.srv.manifest()))
        self.assertEqual(http("GET", self.url("status", extra="&phase=1&event=standby"))[0], 200)
        self.assertEqual(http("GET", self.url("status", extra="&phase=9&event=standby"))[0], 400)
        self.assertEqual(http("GET", self.url("status", extra="&phase=2&event=a%20b"))[0], 400)
        self.assertEqual([(p, e) for _, p, e in self.srv.events], [("1", "standby")])
        for path in ("bundle.json", "conf/migrate.conf", "serve/runtime/busybox", "appfs/migration-appfs.squashfs"):
            self.assertEqual(http("GET", self.url(path))[0], 404, path)
        self.assertEqual(http("POST", self.url("upload/mdat3"), b"AAAA")[0], 404)

    def test_files_are_served_exactly(self):
        for rel, data in self.files.items():
            self.assertEqual(http("GET", self.url(rel)), (200, data), rel)
        self.assertEqual(self.srv.served, {rel: 1 for rel in self.files})

    def test_standby_serves_nothing_and_accepts_no_uploads(self):
        srv, _ = start_server(self.tmp / "bundle", "standby")
        try:
            host, port = srv.address
            base = f"http://{host}:{port}"
            self.assertEqual(http("GET", f"{base}/runtime/busybox?run={self.run_id}")[0], 403)
            self.assertEqual(http("POST", f"{base}/upload/mdat1?run={self.run_id}", b"AAAA")[0], 403)
            self.assertIn(b"mode=standby", http("GET", f"{base}/manifest?run={self.run_id}")[1])
            self.assertEqual(srv.served, {})
        finally:
            srv.stop()

    def test_base64_upload_round_trip_keeps_nul_bytes(self):
        data = b"\0\0" + rbytes(5, sm.MDAT_BYTES - 4) + b"\0\0"
        b64 = base64.encodebytes(data)  # 76-column lines, as BusyBox base64 writes them
        code, body = http("POST", self.url("upload/mdat1"), b64)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual((code, body), (200, (digest + "\n").encode()))
        saved = [p for p in self.backups.iterdir() if p.name.startswith("mdat1-")]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), data)
        self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)
        self.assertEqual(self.srv.backups, {"mdat1": digest})

    def test_upload_rejections(self):
        self.assertEqual(http("POST", self.url("upload/mdat1"), b"!!notbase64!!")[0], 400)
        self.assertEqual(http("POST", self.url("upload/mdat1"), base64.b64encode(b"x" * 1000))[0], 422)
        self.assertEqual(http("POST", self.url("upload/mdat2"), b"")[0], 413)
        self.assertEqual(http("POST", self.url("upload/mdat2"), b"A" * (sm.MAX_UPLOAD_BYTES + 4))[0], 413)
        self.srv.fail_uploads = True
        good = base64.b64encode(rbytes(6, sm.MDAT_BYTES))
        self.assertEqual(http("POST", self.url("upload/mdat2"), good)[0], 500)
        self.assertEqual(self.srv.backups, {})
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_report_is_stored_privately(self):
        self.assertEqual(http("POST", self.url("report"), b"[migrate] DRY RUN complete\n")[0], 200)
        logs = list(self.backups.glob("migrate-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(stat.S_IMODE(logs[0].stat().st_mode), 0o600)
        self.assertEqual(self.srv.reports, [b"[migrate] DRY RUN complete\n"])

    def test_console_output_never_contains_run_id_or_digests(self):
        http("GET", self.url("manifest"))
        http("GET", self.url("runtime/busybox"))
        http("GET", self.url("manifest", run="1" * 32))
        data = rbytes(8, sm.MDAT_BYTES)
        http("POST", self.url("upload/mdat2"), base64.b64encode(data))
        http("GET", self.url("status", extra="&phase=2&event=rehearsal-ok"))
        text = self.out.getvalue()
        self.assertIn("rehearsal finished", text)
        self.assertNotIn(self.run_id, text)
        self.assertNotIn(hashlib.sha256(data).hexdigest(), text)
        for f in self.srv.bundle.files.values():
            for kind in ("sha256", "sha1", "md5"):
                self.assertNotIn(f.digests[kind], text)


EVENT_CALL = r"(?:p1_report|p2_report|p2_stop|p2_hold|hook_best progress) \"?([a-z0-9.$-]+)"


class PhraseConsistency(unittest.TestCase):
    def test_server_camera_and_installer_use_the_same_phrases(self):
        s2, inst, hook = STAGE2.read_text(), INSTALLER.read_text(), HOOK.read_text()
        self.assertIn(f'GO_PHRASE="{sm.GO_PHRASE}"', s2)
        self.assertIn(f'WRITE_PHRASE="{sm.WRITE_PHRASE}"', s2)
        self.assertIn(f'REBOOT_PHRASE="{sm.REBOOT_PHRASE}"', s2)
        self.assertIn(f'CONFIRM_PHRASE="{sm.WRITE_PHRASE}"', inst)
        self.assertIn(f'REBOOT_PHRASE="{sm.REBOOT_PHRASE}"', inst)
        for phrase in (sm.GO_PHRASE, sm.WRITE_PHRASE, sm.REBOOT_PHRASE):
            self.assertNotIn(phrase, hook)

    def test_camera_events_fit_the_server_event_syntax(self):
        events = set()
        for text in (STAGE2.read_text(), HOOK.read_text(), INSTALLER.read_text()):
            events |= set(re.findall(EVENT_CALL, text))
        for ev in events:
            sample = re.sub(r"\$[a-z0-9_]+", "x", ev)  # shell variables expand to short words
            self.assertTrue(sm.EVENT_RE.fullmatch(sample), ev)
        self.assertIn("migrated-reboot-withheld", STAGE2.read_text())
        self.assertTrue(set(sm.NOTES) <= {e for e in events} | {"standby-timeout", "aborted"})


class CommandLine(unittest.TestCase):
    def test_bad_bundle_or_mode_options_exit_2(self):
        tmp = private_tmp("om-cli")
        try:
            self.assertEqual(sm.main(["--bundle", str(tmp / "missing")]), 2)
            make_bundle(tmp / "bundle", synthetic_files())
            self.assertEqual(sm.main(["--bundle", str(tmp / "bundle"), "--mode", "write", "--port", "0",
                                      "--bind", "127.0.0.1", "--backup-dir", str(tmp / "b")]), 2)
            self.assertEqual(sm.main(["--bundle", str(tmp / "bundle"), "--mode", "rehearse", "--port", "0",
                                      "--bind", "127.0.0.1"]), 2)
        finally:
            rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
