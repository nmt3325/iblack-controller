"""Offline tests for build_stockboot.py: the host-side state machine, fed by the real om_stockboot.sh
diagnose/collect actions running on the synthetic camera (sb_fixture) with a hook stub that stores the
uploads the way serve_migration.py does. The trial build needs OM_MKSQUASHFS (else those tests skip)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sb_fixture as fx  # noqa: E402
from sb_fixture import sl, sha  # noqa: E402

import build_stockboot as bs  # noqa: E402
import auth_fixture as af  # noqa: E402

MKSQ = os.environ.get("OM_MKSQUASHFS")
SSID, PASS = "dummy-ssid-for-test", "dummy-pass-for-test"
HOOK = """#!/bin/sh
case "$1" in
  upload) n=$(ls "$OMT_UP" | wc -l); n=$(printf %02d "$n")
    cp "$3" "$OMT_UP/$2-20250101T0000$n-0$n.bin";;
esac
exit 0
"""
DAT = "Default\nCountryRegion=1\nSSID=\nNetworkType=Infra\nAuthMode=OPEN\nEncrypType=NONE\nWPAPSK=\n"
TRIAL_OK = {"kernel": "3.0.8", "cmdline_mark": "1", "mtd_table": "ok", "env": "saved-by-uboot",
            "insmod_cfg80211": "0", "insmod_mt7601sta": "0", "iface": "ra0", "dhcp": "ok", "host": "ok",
            "result": "ok", "insmod_wdt": "0", "stable_window": "ok",
            "auth_binary": "ok", "auth": "connected"}


def tl_block(flags: dict) -> bytes:
    text = "".join(f"omt {k}={v}\n" for k, v in flags.items()).encode()
    return (text.ljust(sl.TL_TEXT_LEN, b"\0") + sl.TL_MARKER).ljust(sl.TL_LEN, b"\0")


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="om-bs-", dir="/dev/shm" if os.path.isdir("/dev/shm") else None))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cam = fx.Camera(self.tmp / "cam")
        self.up = self.tmp / "uploads"
        self.up.mkdir(mode=0o700)
        self.state = self.tmp / "state"
        (self.tmp / "hook.sh").write_text(HOOK)
        self.dump = self.tmp / "spi.bin"
        self.dump.write_bytes(self.cam.original)
        self.fw = fx.firmware()
        (self.tmp / "firmware.bin").write_bytes(self.fw)
        self.n = 0
        old = bs.FIRMWARE_SHA256
        bs.FIRMWARE_SHA256 = sha(self.fw)                    # synthetic image instead of the pinned build
        self.addCleanup(setattr, bs, "FIRMWARE_SHA256", old)

    # -- helpers --------------------------------------------------------------------------------------
    def bs(self, *argv, rc=0):
        if argv and argv[0] in ("trial", "plan") and "--auth-runtime" not in argv:
            argv = (*argv, "--auth-runtime", str(af.runtime(self.tmp / "auth-runtime")))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            got = bs.main(["--state", str(self.state), *argv])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(got, rc, text)
        for secret in (SSID, PASS, "192.0.2.10", "00:00:5e:00:53:01"):
            self.assertNotIn(secret, text)
        self.assertIsNone(re.search(r"[0-9a-f]{64}", text), text)
        return json.loads(out.getvalue()) if rc == 0 else text

    def camera(self, action):
        rc, out = self.cam.run(action, extra=("--hook", str(self.tmp / "hook.sh")), env={"OMT_UP": str(self.up)})
        self.assertEqual(rc, 0, out)

    def newest(self, name) -> Path:
        return sorted(self.up.glob(f"{name}-*.bin"))[-1]

    def diagnose(self, tamper=None):
        self.camera("diagnose")
        info = self.newest("diag-info")
        secs, cur = [], None
        for line in info.read_text().splitlines():
            if line.startswith("== "):
                cur = [line[3:], []]
                secs.append(cur)
            elif cur:
                cur[1].append(line)
        fixed = {"usb": ["1-1 148f:7601 802.11 n WLAN"], "watchdog": ["ls: /dev/watchdog: No such file or directory"]}
        info.write_text("".join(f"== {n}\n" + "".join(x + "\n" for x in fixed.get(n, body)) for n, body in secs))
        if tamper:
            p = self.newest("diag-blocks")
            p.write_text(tamper(p.read_text()))

    def set_flash(self, off: int, data: bytes):
        flash = bytearray(self.cam.flash())
        flash[off:off + len(data)] = data
        self.cam.write_flash(bytes(flash))

    def action(self, name, *argv):
        out = self.tmp / f"act-{name}-{self.n}"
        self.n += 1
        res = self.bs(name, *argv, "--out", str(out))
        meta = json.loads((out / "action.json").read_text())
        for f, m in meta["files"].items():
            self.assertEqual(sha((out / f).read_bytes()), m["sha256"])
            self.assertEqual((out / f).stat().st_mode & 0o777, 0o600)
        return res, meta, out

    def oipc_rootfs(self, big_module=False) -> Path:
        r = self.tmp / "oipc"
        mod = r / "lib" / "modules" / "3.0.8"
        (mod / "kernel" / "net" / "wireless").mkdir(parents=True)
        (mod / "extra").mkdir()
        (mod / "hisilicon").mkdir()
        (mod / "modules.dep").write_text("kernel/net/wireless/cfg80211.ko:\nextra/mt7601sta.ko: "
                                         "kernel/net/wireless/cfg80211.ko\nhisilicon/wdt.ko:\n")
        (mod / "kernel" / "net" / "wireless" / "cfg80211.ko").write_bytes(fx.rbytes(5, 4000))
        (mod / "extra" / "mt7601sta.ko").write_bytes(fx.rbytes(6, 800_000 if big_module else 9000))
        (mod / "hisilicon" / "wdt.ko").write_bytes(fx.rbytes(7, 1024))
        (r / "etc" / "mediatek").mkdir(parents=True)
        (r / "etc" / "mediatek" / "MT7601USTA.dat").write_text(DAT)
        return r

    def wifi_vars(self) -> Path:
        p = self.tmp / "wifi.vars"
        p.write_text(f"wlanssid={SSID}\nwlanpass={PASS}\n")
        p.chmod(0o600)
        return p

    def through_probe(self):
        self.diagnose()
        self.assertTrue(self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump))["ok"])
        res, meta, out = self.action("probe")
        self.assertEqual(meta["action"], "probe-arm")
        self.assertEqual(res["probe"]["bootcmd_chars"], 158)
        self.set_flash(sl.SENV_OFF, fx.uboot_saved((out / "env-probe.img").read_bytes()))   # the probe boot
        self.camera("collect")
        self.assertTrue(self.bs("collect", "--uploads", str(self.up), "--stage", "probe")["ok"])

    def trial(self, **kw):
        return self.action("trial", "--firmware", str(self.tmp / "firmware.bin"), "--oipc-rootfs",
                           str(self.oipc_rootfs(**kw)), "--wifi-vars", str(self.wifi_vars()), "--mksquashfs", MKSQ)

    # -- tests -------------------------------------------------------------------------------------------
    def test_steps_refuse_to_run_out_of_order(self):
        for argv in (("probe", "--out", str(self.tmp / "x")), ("restore", "--out", str(self.tmp / "x")),
                     ("final", "--firmware", str(self.tmp / "firmware.bin"), "--out", str(self.tmp / "x"))):
            self.assertIn("missing: run", self.bs(*argv, rc=2))
        self.assertFalse((self.tmp / "x").exists())
        self.assertEqual(self.bs("status"), {"diag": "missing", "probe": "missing", "trial-build": "missing",
                                             "trial": "missing"})

    def test_trial_requires_matching_watchdog_module(self):
        root = self.oipc_rootfs()
        dep = root / "lib/modules/3.0.8/modules.dep"
        dep.write_text(dep.read_text().replace("hisilicon/wdt.ko:\n", ""))
        with self.assertRaisesRegex(bs.StateError, "wdt.ko"):
            bs.find_modules(root)

    def test_trial_watchdog_must_not_pull_unknown_modules(self):
        root = self.oipc_rootfs()
        dep = root / "lib/modules/3.0.8/modules.dep"
        dep.write_text(dep.read_text().replace("hisilicon/wdt.ko:\n", "hisilicon/wdt.ko: extra/unknown.ko\n"))
        with self.assertRaisesRegex(bs.StateError, "wdt.ko"):
            bs.find_modules(root)

    def test_private_paths(self):
        repo = Path(bs.__file__).resolve().parent
        self.assertIn("outside the Git repository", self._state_in_repo(repo))
        self.up.chmod(0o755)
        self.assertIn("0700", self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump), rc=2))

    def _state_in_repo(self, repo):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(bs.main(["--state", str(repo / "private-state"), "status"]), 2)
        self.assertFalse((repo / "private-state").exists())
        return err.getvalue()

    def test_diag_detects_unstable_blocks_and_foreign_dump(self):
        def flip(text):                                       # pass 2 of block 50 (appfs) read differently
            return re.sub(r"^b 2 50 [0-9a-f]{64}$", "b 2 50 " + "0" * 64, text, flags=re.M)
        self.diagnose(tamper=flip)
        res = self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump))
        self.assertFalse(res["ok"])
        self.assertIn("unstable reads in partitions appfs", res["problems"])
        self.assertIn("diag did not pass", self.bs("probe", "--out", str(self.tmp / "p"), rc=2))
        other = bytearray(self.cam.original)
        other[100] ^= 1
        self.dump.write_bytes(bytes(other))
        self.diagnose()
        res = self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump))
        self.assertIn("vendor U-Boot differs from the SPI dump (the dump would not restore this boot loader)",
                      res["problems"])

    def test_diag_refuses_a_flaky_weak_block(self):
        def flip(text):                                       # one of the 16 reads of block 0x6c0000 differs
            return re.sub(r"^(w \d+ )[0-9a-f]{64}$", lambda m: m.group(1) + "1" * 64, text, count=1, flags=re.M)
        self.diagnose(tamper=flip)
        res = self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump))
        self.assertFalse(res["ok"])
        self.assertEqual((res["weak_block_variants"], res["unstable_block_count"]), (2, 0))
        self.assertIn("weak block 0x6c0000 read 2 different ways in 16 reads", res["problems"])
        self.assertIn("diag did not pass", self.bs("probe", "--out", str(self.tmp / "p"), rc=2))

    def test_probe_not_run_by_uboot(self):
        self.diagnose()
        self.bs("diag", "--uploads", str(self.up), "--spi-dump", str(self.dump))
        repo = Path(bs.__file__).resolve().parent
        self.assertIn("outside the Git repository", self.bs("probe", "--out", str(repo / "act"), rc=2))
        self.assertFalse((repo / "act").exists())
        _, _, out = self.action("probe")
        self.set_flash(sl.SENV_OFF, (out / "env-probe.img").read_bytes())   # armed, but U-Boot never ran it
        self.camera("collect")
        res = self.bs("collect", "--uploads", str(self.up), "--stage", "probe")
        self.assertFalse(res["ok"])
        self.assertIn("probe env still in place", res["reason"])
        self.assertIn("saveenv probe did not pass", self.bs(
            "trial", "--firmware", str(self.tmp / "firmware.bin"), "--oipc-rootfs", str(self.tmp),
            "--wifi-vars", str(self.wifi_vars()), "--mksquashfs", "false", "--out", str(self.tmp / "t"), rc=2))
        res, meta, out = self.action("restore")
        self.assertEqual((out / "env-base.img").read_bytes(), self.cam.e0)

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_full_flow(self):
        self.through_probe()
        res, meta, out = self.trial()
        trial, armed = (out / "trial.img").read_bytes(), (out / "env-armed.img").read_bytes()
        self.assertEqual(len(trial), sl.TRIAL_LEN)
        self.assertEqual(trial[:64 + 1_864_668], self.fw[:64 + 1_864_668])
        self.assertEqual(trial[sl.TK_LEN:sl.TK_LEN + 4], b"hsqs")
        self.assertLessEqual(res["payload_bytes"], sl.TP_LEN)
        self.assertEqual(res["payload_margin_bytes"], sl.TP_LEN - res["payload_bytes"])
        self.assertEqual(res["kernel_margin_bytes"], sl.TK_LEN - 64 - 1_864_668)
        self.assertNotIn(SSID.encode(), trial)                # xz-compressed, never plain on flash
        self.assertEqual(meta["envlive"], sha(self.cam.senv()))
        self.assertIn(sl.TRIAL_MARK, dict(sl.parse_env(armed))["bootcmd"])
        self.assertIn("trial.json missing", self.bs("final", "--firmware", str(self.tmp / "firmware.bin"),
                                                    "--out", str(self.tmp / "f0"), rc=2))
        nonce = json.loads((self.state / "trial-build.json").read_text())["nonce"]
        self.set_flash(sl.SENV_OFF, fx.uboot_saved(armed))     # the trial boot: U-Boot saveenv first
        self.set_flash(sl.TL_OFF, tl_block({"nonce": nonce, **TRIAL_OK}))
        self.camera("collect")
        res = self.bs("collect", "--uploads", str(self.up), "--stage", "trial")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["log_source"], "tl mailbox")
        res, meta, out = self.action("final", "--firmware", str(self.tmp / "firmware.bin"))
        env = dict(sl.parse_env((out / "env-final.img").read_bytes()))
        self.assertEqual((env["bootcmd"], env["verify"]), (sl.FINAL_BOOTCMD, "y"))
        self.assertEqual(env["ipaddr"], "192.0.2.10")         # built from E0: vendor keys kept
        self.assertEqual(res["kernel_slot_margin_bytes"], sl.TK_LEN - 64 - 1_864_668)
        st = self.bs("status")
        self.assertTrue(st["trial"]["ok"] and st["probe"]["ok"] and st["diag"]["ok"])

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_failed_trial_blocks_final(self):
        self.through_probe()
        _, _, out = self.trial()
        self.set_flash(sl.SENV_OFF, fx.uboot_saved((out / "env-armed.img").read_bytes()))
        self.set_flash(sl.TL_OFF, tl_block({"nonce": "0" * 32, **TRIAL_OK, "dhcp": "fail"}))
        self.camera("collect")
        res = self.bs("collect", "--uploads", str(self.up), "--stage", "trial")
        self.assertFalse(res["ok"])
        self.assertEqual(sorted(k for k, v in res["checks"].items() if not v), ["dhcp_ok", "nonce_matches"])
        err = self.bs("final", "--firmware", str(self.tmp / "firmware.bin"), "--out", str(self.tmp / "f"), rc=2)
        self.assertIn("dhcp_ok,nonce_matches", err)
        self.assertFalse((self.tmp / "f").exists())

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_watchdog_and_stability_evidence_are_both_required(self):
        self.through_probe()
        _, _, out = self.trial()
        self.set_flash(sl.SENV_OFF, fx.uboot_saved((out / "env-armed.img").read_bytes()))
        build = json.loads((self.state / "trial-build.json").read_text())
        for missing, check in [("insmod_wdt", "watchdog_driver_loaded"), ("stable_window", "stable_window_survived")]:
            flags = dict(TRIAL_OK, nonce=build["nonce"])
            flags.pop(missing)
            self.set_flash(sl.TL_OFF, tl_block(flags)); self.camera("collect")
            result = self.bs("collect", "--uploads", str(self.up), "--stage", "trial")
            self.assertFalse(result["ok"]); self.assertFalse(result["checks"][check])
            self.bs("final", "--firmware", str(self.tmp / "firmware.bin"), "--out",
                    str(self.tmp / ("blocked-" + missing)), rc=2)

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_auth_runtime_and_connected_evidence_are_required(self):
        self.through_probe()
        self.trial()
        build = json.loads((self.state / "trial-build.json").read_text())
        for missing, check in (("auth_binary", "auth_runtime_verified"), ("auth", "wifi_authenticated")):
            flags = dict(TRIAL_OK, nonce=build["nonce"])
            flags.pop(missing)
            self.set_flash(sl.TL_OFF, tl_block(flags))
            self.camera("collect")
            result = self.bs("collect", "--uploads", str(self.up), "--stage", "trial")
            self.assertFalse(result["ok"])
            self.assertFalse(result["checks"][check])
            self.bs("final", "--firmware", str(self.tmp / "firmware.bin"), "--out",
                    str(self.tmp / ("blocked-" + missing)), rc=2)

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_trial_payload_capacity(self):
        self.through_probe()
        err = self.bs("trial", "--firmware", str(self.tmp / "firmware.bin"), "--oipc-rootfs",
                      str(self.oipc_rootfs(big_module=True)), "--wifi-vars", str(self.wifi_vars()),
                      "--mksquashfs", MKSQ, "--out", str(self.tmp / "t"), rc=2)
        self.assertIn(f"exceeds the {sl.TP_LEN} B tp partition", err)
        self.assertFalse((self.tmp / "t").exists())

    def test_trial_scripts_must_suit_the_stock_busybox(self):
        bad = self.tmp / "trial.sh"
        bad.write_text("#!/bin/sh\ndd if=/tmp/log of=/tmp/tl bs=61440 count=1 conv=sync\n")
        with mock.patch.object(bs, "TRIAL_SH", bad):
            with self.assertRaisesRegex(bs.StateError, "stock BusyBox dd only takes"):
                bs.trial_payload(b"l", b"a", "0" * 32, SSID, PASS, self.oipc_rootfs(), "/nonexistent/mksquashfs",
                                 workdir=self.tmp)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("om-trial-")), [])

    @unittest.skipUnless(MKSQ, "OM_MKSQUASHFS not set")
    def test_plan_needs_no_camera_state(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            got = bs.main(["plan", "--firmware", str(self.tmp / "firmware.bin"), "--oipc-rootfs",
                           str(self.oipc_rootfs()), "--mksquashfs", MKSQ, "--workdir", str(self.tmp),
                           "--auth-runtime", str(af.runtime(self.tmp / "auth-runtime"))])
            with self.assertRaises(SystemExit):                # every other step needs the private state
                bs.main(["status"])
        self.assertEqual(got, 0, err.getvalue())
        self.assertFalse(self.state.exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.name.startswith("om-trial-")), [])
        res = json.loads(out.getvalue())
        self.assertFalse(res["camera_contacted"])
        tp, tk = res["trial_payload"], res["trial_kernel"]
        self.assertEqual((tp["limit_bytes"], tp["margin_bytes"]), (sl.TP_LEN, sl.TP_LEN - tp["bytes"]))
        self.assertGreater(tp["margin_bytes"], 0)
        self.assertNotIn("wpa.conf", tp["file_bytes"])
        self.assertNotIn("MT7601USTA.dat", tp["file_bytes"])  # private Wi-Fi files are omitted
        self.assertEqual(tp["file_bytes"]["tl-blank.img"], sl.TL_LEN)
        self.assertEqual(tk["margin_bytes"], sl.TK_LEN - 64 - 1_864_668)
        self.assertEqual((res["probe_env"]["bootcmd_chars"], res["trial_env"]["bootcmd_chars"]),
                         (res["layout"]["probe_bootcmd_chars"], res["layout"]["trial_bootcmd_chars"]))
        self.assertGreater(res["trial_env"]["bootcmd_margin_chars"], 0)
        for secret in (SSID, PASS):
            self.assertNotIn(secret, out.getvalue())


class Units(unittest.TestCase):
    def test_make_dat(self):
        out = bs.make_dat(DAT, "s", "p" * 8)
        self.assertIn("SSID=s\n", out)
        self.assertIn("AuthMode=WPA2PSK\n", out)
        with self.assertRaises(bs.StateError):
            bs.make_dat("SSID=\n", "s", "p" * 8)

    def test_parse_blocks_needs_everything(self):
        with self.assertRaises(bs.StateError):
            bs.parse_blocks("b 1 0 " + "0" * 64 + "\n")

    def test_tl_blank_is_an_empty_checkpoint(self):
        blank = sl.tl_blank()
        self.assertEqual(blank, tl_block({}))
        self.assertEqual(len(blank), sl.TL_LEN)
        self.assertEqual(bs.parse_trial_log(blank), ("", {}))

    def test_trial_log_sources(self):
        text, flags = bs.parse_trial_log(tl_block({"nonce": "ab", "dhcp": "ok"}))
        self.assertEqual(flags, {"nonce": "ab", "dhcp": "ok"})
        self.assertEqual(bs.parse_trial_log(b"\xff" * sl.TL_LEN), (None, {}))
        self.assertEqual(bs.parse_trial_log(b"\xff" * sl.TL_LEN, b"omt host=ok\n")[1], {"host": "ok"})


if __name__ == "__main__":
    unittest.main()
