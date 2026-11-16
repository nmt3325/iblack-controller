#!/usr/bin/env python3
"""Offline tests for serve_migration.py (loopback HTTP only; no camera): bundle and action-dir validation,
per-mode manifests with one-shot withdrawal, confirmation phrases, served files and private uploads."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import (HOOK, INSTALLER, STAGE2, http, make_bundle, private_tmp, rbytes, rmtree, sm,  # noqa: E402
                        start_server, stock_wget_form, synthetic_files, write_action_set)
import sb_fixture as fx  # noqa: E402
from sb_fixture import sl  # noqa: E402

E0 = sl.build_env(fx.E0_ENTRIES)
UBOOT, ENVLIVE = "a" * 64, hashlib.sha256(E0).hexdigest()
TRIAL_SH = Path(__file__).resolve().parents[1] / "camera" / "trial.sh"


def action_files(action: str) -> dict:
    return {"probe-arm": {"env-probe.img": sl.build_probe(E0)[0]},
            "trial-arm": {"trial.img": fx.trial_image(), "env-armed.img": sl.build_trial(E0)[0]},
            "restore-env": {"env-base.img": E0},
            "final": {"env-final.img": sl.build_final(E0)[0]}}[action]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-srv")
        self.addCleanup(rmtree, self.tmp)
        self.files = synthetic_files()
        self.bundle_dir = self.tmp / "bundle"
        self.run_id, _ = make_bundle(self.bundle_dir, self.files)
        self.bundle = sm.load_bundle(self.bundle_dir)
        self.backups = self.tmp / "backups"
        self.n = 0

    def action_dir(self, action: str, files=None) -> Path:
        self.n += 1
        return write_action_set(self.tmp / f"act-{self.n}", action, files or action_files(action), ENVLIVE, UBOOT)

    def make(self, mode, **kw):
        srv = sm.MigrationServer(self.bundle, mode, bind="127.0.0.1", port=0, out=io.StringIO(), **kw)
        self.addCleanup(srv.httpd.server_close)
        return srv

    def armed_kw(self, mode, reboot=False):
        w, r, needed = sm.ACTIONS[mode]
        kw = {"backup_dir": self.backups}
        if needed:
            kw["action_set"] = sm.load_action_dir(self.action_dir(mode), mode)
        if w:
            kw["write_confirm"] = w
        if reboot and r:
            kw["reboot_confirm"] = r
        return kw


class BundleLoading(Base):
    def test_valid_bundle(self):
        self.assertEqual((self.bundle.run_id, self.bundle.port), (self.run_id, 8608))
        self.assertEqual({rel: f.data for rel, f in self.bundle.files.items()}, self.files)

    def test_tampered_served_file_is_rejected(self):
        p = self.bundle_dir / "serve" / "runtime" / "busybox"
        p.write_bytes(p.read_bytes() + b"x")
        with self.assertRaisesRegex(sm.BundleError, "differs from bundle.json"):
            sm.load_bundle(self.bundle_dir)

    def test_conf_pin_mismatch_is_rejected(self):
        p = self.bundle_dir / "conf" / "migrate.conf"
        text = p.read_text()
        for key in ("OM_FW_SHA256", "OM_OENV_SHA256", "OM_INSTALLER_SHA256"):
            p.write_text(text.replace(sm.parse_conf(text)[key], "0" * 64))
            with self.assertRaisesRegex(sm.BundleError, key):
                sm.load_bundle(self.bundle_dir)
        p.write_text(text)

    def test_file_list_and_schema_must_match_the_protocol(self):
        meta_path = self.bundle_dir / "bundle.json"
        meta = json.loads(meta_path.read_text())
        meta["files"].pop("payload/env-openipc.img")
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(sm.BundleError, "file list"):
            sm.load_bundle(self.bundle_dir)
        meta["schema"] = "iblack-openipc-migrate-bundle/1"            # the OpenIPC-U-Boot bundle format
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(sm.BundleError, "schema"):
            sm.load_bundle(self.bundle_dir)

    def test_payload_contents_are_validated(self):
        legacy = sl.build_env([("bootcmd", "run bootcmd_openipc"), ("mtdparts", "hi_sfc:256k(boot)"),
                               ("wlandev", "x"), ("wlanssid", "s"), ("wlanpass", "p" * 8)], sl.OIPC_ENV_LEN)
        no_wifi = sl.build_env([("bootcmd", sl.FINAL_BOOTCMD), ("mtdparts", sl.OIPC_ENV_LAYOUT["mtdparts"])],
                               sl.OIPC_ENV_LEN)
        fw = bytearray(self.files["payload/firmware.bin"])
        fw[sl.FW_KERNEL_LEN:sl.FW_KERNEL_LEN + 4] = b"\xff" * 4
        cases = {"payload/env-openipc.img": ((legacy, "final layout"), (no_wifi, "Wi-Fi only"),
                                             (E0, "env-openipc.img")),
                 "payload/firmware.bin": ((bytes(fw), "SquashFS"), (b"\0" * 64, "firmware.bin"))}
        for rel, variants in cases.items():
            for data, why in variants:
                files = dict(self.files, **{rel: data})
                out = self.tmp / f"b-{self.n}"
                self.n += 1
                make_bundle(out, files)
                with self.assertRaisesRegex(sm.BundleError, why):
                    sm.load_bundle(out)

    def test_bundle_inside_the_repository_is_refused(self):
        with self.assertRaisesRegex(sm.BundleError, "inside the Git repository"):
            sm.load_bundle(sm.REPO_ROOT / "research")


class ActionDirs(Base):
    def test_valid_sets_load(self):
        for action in ("probe-arm", "trial-arm", "restore-env", "final"):
            a = sm.load_action_dir(self.action_dir(action), action)
            self.assertEqual(a.files, action_files(action))
            self.assertEqual((a.envlive, a.uboot), (ENVLIVE, UBOOT))

    def test_rejections(self):
        d = self.action_dir("probe-arm")
        with self.assertRaisesRegex(sm.BundleError, "not a final set"):
            sm.load_action_dir(d, "final")
        (d / "env-probe.img").write_bytes(E0)
        with self.assertRaisesRegex(sm.BundleError, "differs from action.json"):
            sm.load_action_dir(d, "probe-arm")
        d = self.action_dir("probe-arm", {"env-probe.img": E0, "extra.img": b"x"})
        with self.assertRaisesRegex(sm.BundleError, "file list"):
            sm.load_action_dir(d, "probe-arm")
        d = write_action_set(self.tmp / "bad-pin", "restore-env", {"env-base.img": E0}, "x", UBOOT)
        with self.assertRaisesRegex(sm.BundleError, "envlive"):
            sm.load_action_dir(d, "restore-env")
        with self.assertRaisesRegex(sm.BundleError, "inside the Git repository"):
            sm.load_action_dir(sm.REPO_ROOT / "research", "final")

    def test_action_files_are_checked_against_the_plan(self):
        armed = sl.build_trial(E0)[0]
        final_no_verify = sl.build_env(sl.replace_values(sl.parse_env(sl.build_final(E0)[0]), {"verify": "n"}))
        marked = sl.build_env(sl.replace_values(fx.E0_ENTRIES, {"x": sl.TRIAL_MARK}))
        trial = bytearray(fx.trial_image())
        big = bytearray(trial)
        big[sl.TK_LEN + 40:sl.TK_LEN + 48] = (sl.TP_LEN + 1).to_bytes(8, "little")
        no_sq = bytearray(trial)
        no_sq[sl.TK_LEN:sl.TK_LEN + 4] = b"\xff" * 4
        bad = [("env-probe.img", E0), ("env-armed.img", E0), ("env-base.img", armed), ("env-base.img", marked),
               ("env-final.img", final_no_verify), ("env-final.img", E0[:-1]), ("trial.img", bytes(trial[:-1])),
               ("trial.img", bytes(big)), ("trial.img", bytes(no_sq))]
        for name, data in bad:
            with self.assertRaises(sm.BundleError, msg=name):
                sm.validate_action_file(name, data)
        for action in ("probe-arm", "trial-arm", "restore-env", "final"):
            for name, data in action_files(action).items():
                sm.validate_action_file(name, data)


class ServerConfiguration(Base):
    def test_phrases_action_dirs_and_backup_dir_are_enforced(self):
        bd = self.backups
        for mode in ("probe-arm", "trial-arm", "restore-env", "final"):
            kw = self.armed_kw(mode)
            for drop in ("write_confirm", "action_set", "backup_dir"):
                with self.assertRaises(ValueError, msg=(mode, drop)):
                    self.make(mode, **{k: v for k, v in kw.items() if k != drop})
            with self.assertRaises(ValueError):
                self.make(mode, **dict(kw, write_confirm="yes"))
            with self.assertRaises(ValueError):
                self.make(mode, **dict(kw, reboot_confirm="yes"))
            self.make(mode, **kw)
            self.make(mode, **self.armed_kw(mode, reboot=True))
        with self.assertRaises(ValueError):                         # restore-env never reboots
            self.make("restore-env", **dict(self.armed_kw("restore-env"), reboot_confirm="yes-reboot-into-openipc"))
        with self.assertRaises(ValueError):                         # a phrase of another mode
            self.make("final", **dict(self.armed_kw("final"), write_confirm=sm.ACTIONS["trial-arm"][0]))
        for mode in ("diagnose", "collect"):
            with self.assertRaises(ValueError):
                self.make(mode, backup_dir=bd, write_confirm=sm.ACTIONS["final"][0])
            with self.assertRaises(ValueError):
                self.make(mode, backup_dir=bd, action_set=sm.load_action_dir(self.action_dir("final"), "final"))
            with self.assertRaises(ValueError):
                self.make(mode)
            self.make(mode, backup_dir=bd)
        with self.assertRaises(ValueError):
            self.make("diagnose", backup_dir=sm.REPO_ROOT / "research" / "x")
        loose = self.tmp / "loose"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        with self.assertRaises(ValueError):
            self.make("diagnose", backup_dir=loose)
        self.make("standby")
        self.make("abort")

    def test_manifest_per_mode_and_one_shot(self):
        run = f"run={self.bundle.run_id}"
        self.assertEqual(self.make("standby").manifest().decode().splitlines(), [run, "mode=standby"])
        self.assertEqual(self.make("abort").manifest().decode().splitlines(), [run, "mode=abort"])
        srv = self.make("diagnose", backup_dir=self.backups)
        self.assertEqual(srv.manifest().decode().splitlines(),
                         [run, "mode=go", f"go={sm.GO_PHRASE}", "action=diagnose", f"seq={srv.seq}"])
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", srv.seq))
        kw = self.armed_kw("probe-arm", reboot=True)
        srv = self.make("probe-arm", **kw)
        img = kw["action_set"].files["env-probe.img"]
        w, r, _ = sm.ACTIONS["probe-arm"]
        self.assertEqual(srv.manifest().decode().splitlines(),
                         [run, "mode=go", f"go={sm.GO_PHRASE}", "action=probe-arm", f"seq={srv.seq}",
                          f"uboot={UBOOT}", f"envlive={ENVLIVE}", f"f.env-probe.img={hashlib.sha256(img).hexdigest()}",
                          f"write={w}", f"reboot={r}"])
        srv.record("2", "action-start-diagnose")                   # another action does not consume it
        srv.record("1", "action-start-probe-arm")                  # nor does phase 1
        self.assertIn(b"mode=go", srv.manifest())
        srv.record("2", "action-start-probe-arm")
        self.assertEqual(srv.manifest().decode().splitlines(), [run, "mode=standby"])
        srv.record("2", "action-rc-probe-arm-0")
        self.assertEqual(srv.action_rc, 0)
        self.assertEqual(stat.S_IMODE((self.backups / "events.log").stat().st_mode), 0o600)
        self.assertEqual(len((self.backups / "events.log").read_text().splitlines()), 4)


class HTTPBehaviour(Base):
    def serve(self, mode, **kw):
        srv, out = start_server(self.bundle_dir, mode, **kw)
        self.addCleanup(srv.stop)
        host, port = srv.address
        return srv, out, f"http://{host}:{port}"

    def url(self, base, path, run=None, extra=""):
        return f"{base}/{path}?run={self.run_id if run is None else run}{extra}"

    def test_every_endpoint_requires_the_run_id(self):
        srv, _, base = self.serve("diagnose", backup_dir=self.backups)
        self.assertEqual(http("GET", f"{base}/manifest")[0], 403)
        self.assertEqual(http("GET", self.url(base, "manifest", run="0" * 32))[0], 403)
        self.assertEqual(http("GET", self.url(base, "runtime/busybox", run=self.run_id[:-1]))[0], 403)
        self.assertEqual(http("POST", self.url(base, "upload/diag-info", run="x"), b"QUFB")[0], 403)
        self.assertEqual(srv.refused, 4)
        self.assertEqual((srv.served, srv.uploads), ({}, {}))

    def test_paths_and_status(self):
        srv, _, base = self.serve("diagnose", backup_dir=self.backups)
        self.assertEqual(http("GET", self.url(base, "manifest")), (200, srv.manifest()))
        self.assertEqual(http("GET", self.url(base, "status", extra="&phase=installer&event=diagnose-done"))[0], 200)
        self.assertEqual(http("GET", self.url(base, "status", extra="&phase=trial&event=trial-network-up"))[0], 200)
        self.assertEqual(http("GET", self.url(base, "status", extra="&phase=9&event=x"))[0], 400)
        self.assertEqual(http("GET", self.url(base, "status", extra="&phase=2&event=a%20b"))[0], 400)
        self.assertEqual([(p, e) for _, p, e in srv.events], [("installer", "diagnose-done"),
                                                                 ("trial", "trial-network-up")])
        for path in ("bundle.json", "conf/migrate.conf", "serve/runtime/busybox", "payload/firmware.bin",
                     "action/env-probe.img", "runtime/../bundle.json"):
            self.assertEqual(http("GET", self.url(base, path))[0], 403, path)
        self.assertEqual(http("POST", self.url(base, "upload/mdat3"), b"AAAA")[0], 404)

    def test_runtime_payload_and_action_files_are_served_exactly(self):
        kw = self.armed_kw("final")
        srv, _, base = self.serve("final", backup_dir=self.backups, action_dir=self.action_dir("final"),
                                  write_confirm=kw["write_confirm"])
        for rel, data in self.files.items():
            self.assertEqual(http("GET", self.url(base, rel)), (200, data), rel)
        img = action_files("final")["env-final.img"]
        self.assertEqual(http("GET", self.url(base, "action/env-final.img")), (200, img))
        self.assertEqual(http("GET", self.url(base, "action/env-probe.img"))[0], 403)
        self.assertEqual(srv.served, {**{rel: 1 for rel in self.files}, "action/env-final.img": 1})
        srv2, _, base2 = self.serve("trial-arm", backup_dir=self.backups, action_dir=self.action_dir("trial-arm"),
                                    write_confirm=sm.ACTIONS["trial-arm"][0])
        self.assertEqual(http("GET", self.url(base2, "payload/firmware.bin"))[0], 403)   # final mode only
        self.assertEqual(http("GET", self.url(base2, "action/trial.img")), (200, fx.trial_image()))

    def test_standby_serves_nothing_and_accepts_no_uploads(self):
        srv, _, base = self.serve("standby")
        self.assertEqual(http("GET", self.url(base, "runtime/busybox"))[0], 404)
        self.assertEqual(http("POST", self.url(base, "upload/diag-env"), b"AAAA")[0], 403)
        self.assertIn(b"mode=standby", http("GET", self.url(base, "manifest"))[1])
        self.assertEqual(srv.served, {})

    def test_base64_upload_round_trip_keeps_nul_bytes(self):
        srv, _, base = self.serve("diagnose", backup_dir=self.backups)
        data = b"\0\0" + rbytes(5, sl.SENV_LEN - 4) + b"\0\0"
        code, body = http("POST", self.url(base, "upload/diag-env"), base64.encodebytes(data))  # 76-col lines
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual((code, body), (200, (digest + "\n").encode()))
        saved = list(self.backups.glob("diag-env-*.bin"))
        self.assertEqual(len(saved), 1)
        self.assertTrue(re.fullmatch(r"diag-env-\d{8}T\d{6}-001\.bin", saved[0].name))
        self.assertEqual(saved[0].read_bytes(), data)
        self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)
        self.assertEqual(srv.uploads, {"diag-env": [digest]})

    def test_upload_rejections(self):
        srv, _, base = self.serve("diagnose", backup_dir=self.backups)
        self.assertEqual(http("POST", self.url(base, "upload/diag-env"), b"!!notbase64!!")[0], 400)
        self.assertEqual(http("POST", self.url(base, "upload/diag-env"), base64.b64encode(b"x" * 1000))[0], 422)
        self.assertEqual(http("POST", self.url(base, "upload/diag-info"), b"")[0], 413)
        self.assertEqual(http("POST", self.url(base, "upload/diag-info"), b"A" * (700 * 1024 + 4096))[0], 413)
        self.assertEqual(http("POST", self.url(base, "upload/collect-env"),
                              base64.b64encode(rbytes(1, sl.SENV_LEN)))[0], 403)          # collect mode only
        srv.fail_uploads = True
        self.assertEqual(http("POST", self.url(base, "upload/diag-info"), base64.b64encode(b"== uname\n"))[0], 500)
        self.assertEqual(srv.uploads, {})
        self.assertEqual([p.name for p in self.backups.iterdir()], [])

    def test_uboot_image_and_watchdog_gate_uploads(self):
        srv, _, base = self.serve("diagnose", backup_dir=self.backups)
        img = rbytes(9, sl.UBOOT_LEN)
        self.assertEqual(http("POST", self.url(base, "upload/diag-uboot"), base64.encodebytes(img)),
                         (200, (hashlib.sha256(img).hexdigest() + "\n").encode()))      # BusyBox 76-col base64
        self.assertEqual(http("POST", self.url(base, "upload/diag-uboot"), base64.encodebytes(img[:-1]))[0], 422)
        self.assertEqual(http("POST", self.url(base, "upload/wdtgate-info"), base64.b64encode(b"hold 300\n"))[0], 403)
        self.assertEqual([p.read_bytes() for p in self.backups.glob("diag-uboot-*.bin")], [img])
        srv2, _, base2 = self.serve("wdtgate", backup_dir=self.backups)
        info = b"hold 300\nuptime-start 12.34\n"
        self.assertEqual(http("POST", self.url(base2, "upload/wdtgate-info"), base64.b64encode(info))[0], 200)
        self.assertEqual(http("POST", self.url(base2, "upload/wdtgate-info"),
                              base64.b64encode(b"x" * (16 * 1024 + 1)))[0], 422)
        self.assertEqual(http("POST", self.url(base2, "upload/diag-uboot"), base64.encodebytes(img))[0], 403)
        self.assertEqual(http("POST", self.url(base2, "upload/log-wdtgate"),
                              base64.b64encode(b"[stockboot:wdtgate] resume ok\n"))[0], 200)
        self.assertEqual([p.read_bytes() for p in self.backups.glob("wdtgate-info-*.bin")], [info])
        self.assertEqual(sorted(srv2.uploads), ["log-wdtgate", "wdtgate-info"])

    def test_trial_reports_its_log_in_trial_arm_mode(self):
        srv, _, base = self.serve("trial-arm", backup_dir=self.backups, action_dir=self.action_dir("trial-arm"),
                                  write_confirm=sm.ACTIONS["trial-arm"][0])
        srv.record("2", "action-start-trial-arm")                   # consumed before the reboot
        log = b"omt nonce=00\nomt dhcp=ok\n[ 1.50] x: 100% +y/z [ok] ~!*'()&=_-.\t\xff"
        form = stock_wget_form(log)
        self.assertEqual(form[:22], b"omt%20nonce=00%0Aomt%2")  # what the stock wget sent under qemu
        for body in (form, log):  # the stock wget's form, or a wget that posts the bytes as they are
            self.assertEqual(http("POST", self.url(base, "upload/trial-log"), body), (200, b"ok\n"))
        stored = sorted(self.backups.glob("trial-log-*.bin"))
        self.assertEqual([p.read_bytes() for p in stored], [log, log])
        self.assertIn(b"mode=standby", http("GET", self.url(base, "manifest"))[1])
        full = b"\n" * sl.TL_TEXT_LEN  # escapes to three times its size
        self.assertEqual(http("POST", self.url(base, "upload/trial-log"), stock_wget_form(full))[0], 200)
        self.assertEqual(sorted(self.backups.glob("trial-log-*.bin"))[-1].read_bytes(), full)
        self.assertEqual(http("POST", self.url(base, "upload/trial-log"), stock_wget_form(full + b"\n"))[0], 413)
        self.assertEqual(http("POST", self.url(base, "upload/trial-log"), b"x" * (sl.TL_TEXT_LEN + 1))[0], 422)
        self.assertEqual(http("POST", self.url(base, "upload/trial-log"), b"x" * (3 * sl.TL_TEXT_LEN + 1))[0], 413)
        self.assertEqual(len(srv.uploads["trial-log"]), 3)

    def test_form_bodies_decode_and_others_stay_as_they_are(self):
        for raw in (b"", b"a", b"%", b"%4", b"%zz", b"100%", b"a b", b"x\ny", bytes(range(256))):
            self.assertEqual(sm.decode_post_data(stock_wget_form(raw)), raw)
        self.assertEqual(stock_wget_form(bytes(range(256))).count(b"%"), 256 - 62 - 11)
        for body in (b"a b", b"x\ny", b"100%", b"%4", b"%zz", b"%0a"):  # not the stock form: kept as they came
            self.assertEqual(sm.decode_post_data(body), body)
        self.assertEqual(sm.decode_post_data(b"omt%20dhcp=ok%0A"), b"omt dhcp=ok\n")

    def test_console_output_never_contains_run_id_or_digests(self):
        kw = self.armed_kw("probe-arm")
        srv, out, base = self.serve("probe-arm", backup_dir=self.backups, action_dir=self.action_dir("probe-arm"),
                                    write_confirm=kw["write_confirm"])
        http("GET", self.url(base, "manifest"))
        http("GET", self.url(base, "runtime/busybox"))
        http("GET", self.url(base, "action/env-probe.img"))
        http("GET", self.url(base, "manifest", run="1" * 32))
        http("POST", self.url(base, "upload/log-probe-arm"), base64.b64encode(b"[stockboot] log\n"))
        http("GET", self.url(base, "status", extra="&phase=2&event=action-start-probe-arm"))
        http("GET", self.url(base, "status", extra="&phase=installer&event=rebooting-probe-arm"))
        text = out.getvalue()
        self.assertIn("action withdrawn (one-shot)", text)
        self.assertIn("then run collect", text)
        self.assertNotIn(self.run_id, text)
        self.assertNotIn(srv.seq, text)
        for digest in (hashlib.sha256(b"[stockboot] log\n").hexdigest(), UBOOT, ENVLIVE,
                       *[f.digests[k] for f in srv.bundle.files.values() for k in ("sha256", "sha1", "md5")]):
            self.assertNotIn(digest, text)


EVENT_CALL = r"(?:p1_report|p2_report|progress) \"?([A-Za-z0-9.$_{}-]+)"


class PhraseConsistency(unittest.TestCase):
    def test_server_camera_installer_and_fixture_use_the_same_phrases(self):
        s2, inst, hook = STAGE2.read_text(), INSTALLER.read_text(), HOOK.read_text()
        self.assertIn(f'GO_PHRASE="{sm.GO_PHRASE}"', s2)
        names = {"probe-arm": ("PROBE_PHRASE", "PROBE_REBOOT"), "trial-arm": ("TRIAL_PHRASE", "TRIAL_REBOOT"),
                 "restore-env": ("RESTORE_PHRASE", None), "final": ("FINAL_PHRASE", "FINAL_REBOOT")}
        for action, (wn, rn) in names.items():
            w, r, _ = sm.ACTIONS[action]
            self.assertIn(f'\n{wn}="{w}"\n', inst)
            if rn:
                self.assertIn(f'\n{rn}="{r}"\n', inst)
            self.assertEqual(fx.PHRASES[action], (w, r))
            for phrase in (w, r):
                if phrase:
                    self.assertNotIn(phrase, hook)
                    self.assertNotIn(phrase, s2)                   # stage2 copies them from the manifest
        self.assertEqual(set(sm.ACTIONS) - {"diagnose", "collect", "wdtgate"}, set(names))
        self.assertEqual(sm.ACTIONS["wdtgate"], (None, None, ()))     # read-only: no phrase, no action set

    def test_camera_events_fit_the_server_event_syntax(self):
        events = set()
        for text in (STAGE2.read_text(), HOOK.read_text(), INSTALLER.read_text()):
            events |= set(re.findall(EVENT_CALL, text))
        trial = TRIAL_SH.read_text()
        events |= {f"trial-{e}" for e in re.findall(r"finish ([a-z0-9-]+)", trial)}
        events |= set(re.findall(r"event=([a-z0-9-]+)", trial))
        expanded = set()
        for ev in events:
            if "$ACTION" in ev or "$act" in ev:
                expanded |= {re.sub(r"\$(ACTION|act)", a, ev) for a in sm.ACTIONS}
            else:
                expanded.add(ev)
        for ev in expanded:
            sample = re.sub(r"\$(\{?[A-Za-z0-9_]+\}?)?", "x", ev)   # "$(...)" is cut at "$" by EVENT_CALL
            self.assertTrue(sm.EVENT_RE.fullmatch(sample), ev)
        self.assertTrue(set(sm.NOTES) <= expanded, set(sm.NOTES) - expanded)


class CommandLine(unittest.TestCase):
    def test_bad_bundle_or_mode_options_exit_2(self):
        tmp = private_tmp("om-cli")
        self.addCleanup(rmtree, tmp)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(sm.main(["--bundle", str(tmp / "missing")]), 2)
            make_bundle(tmp / "bundle", synthetic_files())
            common = ["--bundle", str(tmp / "bundle"), "--port", "0", "--bind", "127.0.0.1"]
            self.assertEqual(sm.main([*common, "--mode", "probe-arm", "--backup-dir", str(tmp / "b")]), 2)
            self.assertEqual(sm.main([*common, "--mode", "probe-arm", "--backup-dir", str(tmp / "b"),
                                      "--write-confirm", sm.ACTIONS["probe-arm"][0]]), 2)   # no --action-dir
            self.assertEqual(sm.main([*common, "--mode", "diagnose"]), 2)                    # no --backup-dir
        self.assertEqual(err.getvalue().count("serve_migration: "), 4)


if __name__ == "__main__":
    unittest.main()
