#!/usr/bin/env python3
"""Offline tests for make_env.py using synthetic U-Boot blobs only."""
import hashlib
import io
import lzma
import os
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import make_env as me  # noqa: E402

DEFAULTS_RAW = ["bootcmd=run bootflash", "bootdelay=1", "baudrate=115200",
                "ipaddr=192.0.2.20", "serverip=192.0.2.107", "mtdids=nor0=old",
                "mtdparts=", "osmem=32M", "board_name", "soc=hi3518ev100",
                "fwfile=firmware.bin", "mtdids=nor0=hi_sfc", "mtdparts=hi_sfc:256k(boot),64k(env)"]
DEFAULTS = [tuple(e.partition("=")[::2]) for e in DEFAULTS_RAW]
SSID, PSK = "example-ssid", "example-pass-123"


def blob(entries=DEFAULTS_RAW, extra_candidate=False):
    env = b"".join(e.encode() + b"\0" for e in entries) + b"\0"
    out = b"\x7fjunk\0Loading: *\0" + env + b"tail\0bootcmd=help text\0\0"
    if extra_candidate:
        out += b"\0" + env
    return out


def fake_uboot(entries=DEFAULTS_RAW, extra_candidate=False):
    return os.urandom(me.LZMA_OFFSET) + lzma.compress(blob(entries, extra_candidate), format=lzma.FORMAT_ALONE)


class TestExtraction(unittest.TestCase):
    def test_extract_and_last_duplicate_wins(self):
        entries = me.extract_default_env(me.decompress_uboot(fake_uboot()))
        self.assertEqual(entries, DEFAULTS)
        merged, dups, deletions = me.effective(entries)
        self.assertEqual(dups, ["mtdids", "mtdparts"])
        self.assertEqual(deletions, ["board_name", "mtdparts"])
        self.assertEqual(dict(merged)["mtdparts"], "hi_sfc:256k(boot),64k(env)")
        self.assertNotIn("board_name", dict(merged))
        self.assertEqual([k for k, _ in merged][:3], ["bootcmd", "bootdelay", "baudrate"])

    def test_delete_entry_removes_earlier_definition(self):
        merged, _, _ = me.effective([("a", "1"), ("b", "2"), ("a", "")])
        self.assertEqual(merged, [("b", "2")])

    def test_ambiguous_or_missing_env_rejected(self):
        with self.assertRaises(me.EnvError):
            me.extract_default_env(blob(extra_candidate=True))
        with self.assertRaises(me.EnvError):
            me.extract_default_env(b"\0bootcmd=x\0\0")

    def test_not_lzma_rejected(self):
        with self.assertRaises(me.EnvError):
            me.decompress_uboot(b"\0" * (me.LZMA_OFFSET + 64))


class TestImage(unittest.TestCase):
    def test_crc_layout_and_roundtrip(self):
        img = me.build_env_image(DEFAULTS, {"wlandev": "x", "wlanssid": SSID, "wlanpass": PSK})
        self.assertEqual(len(img), 0x10000)
        self.assertEqual(struct.unpack("<I", img[:4])[0], zlib.crc32(img[4:]) & 0xFFFFFFFF)
        env = me.parse_env_image(img)
        self.assertEqual(env["wlanssid"], SSID)
        self.assertEqual(env["mtdparts"], "hi_sfc:256k(boot),64k(env)")
        self.assertEqual(env["bootcmd"], "run bootflash")

    def test_additions_override_defaults(self):
        env = me.parse_env_image(me.build_env_image(DEFAULTS, {"ipaddr": "192.0.2.2"}))
        self.assertEqual(env["ipaddr"], "192.0.2.2")

    def test_overflow_rejected(self):
        with self.assertRaises(me.EnvError):
            me.build_env_image(DEFAULTS, {"wlandev": "x" * 0x10000})

    def test_bad_crc_detected(self):
        img = bytearray(me.build_env_image(DEFAULTS, {}))
        img[10] ^= 1
        with self.assertRaises(me.EnvError):
            me.parse_env_image(bytes(img))


class TestPrivateVars(unittest.TestCase):
    def _write(self, d, text, mode=0o600):
        p = Path(d) / "wifi.vars"
        p.write_text(text)
        os.chmod(p, mode)
        return p

    def test_allowlist_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            ok = me.parse_private_vars(self._write(d, f"# c\nwlanssid={SSID}\nwlanpass={PSK}\n"))
            self.assertEqual(ok, {"wlanssid": SSID, "wlanpass": PSK})
            for bad in ("bootcmd=reset\n", "wlanpass=short\n", f"wlanssid={'s' * 33}\n", "novalue\n"):
                with self.assertRaises(me.EnvError):
                    me.parse_private_vars(self._write(d, bad))

    def test_group_readable_file_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(me.EnvError):
                me.parse_private_vars(self._write(d, f"wlanssid={SSID}\n", 0o644))


class TestCli(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = me.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.ub = d / "uboot.bin"
        self.ub.write_bytes(fake_uboot())
        self.sha = hashlib.sha256(self.ub.read_bytes()).hexdigest()
        self.vars = d / "wifi.vars"
        self.vars.write_text(f"wlanssid={SSID}\nwlanpass={PSK}\n")
        os.chmod(self.vars, 0o600)
        self.out = d / "private-out"

    def test_success_writes_private_files_without_secrets(self):
        rc, out, err = self.run_main(["--uboot", str(self.ub), "--uboot-sha256", self.sha,
                                      "--private-vars", str(self.vars), "--output-dir", str(self.out)])
        self.assertEqual(rc, 0, err)
        img = (self.out / "env.img").read_bytes()
        self.assertEqual(me.parse_env_image(img)["wlandev"], me.DEFAULT_WLANDEV)
        self.assertEqual(os.stat(self.out / "env.img").st_mode & 0o777, 0o600)
        summary = (self.out / "env-summary.json").read_text()
        for secret in (SSID, PSK):
            self.assertNotIn(secret, out + err + summary)
        self.assertIn('"wlanpass": true', summary)
        rc2, _, err2 = self.run_main(["--uboot", str(self.ub), "--uboot-sha256", self.sha,
                                      "--private-vars", str(self.vars), "--output-dir", str(self.out)])
        self.assertEqual(rc2, 1)
        self.assertIn("overwrite", err2)

    def test_pin_mismatch_refused(self):
        rc, _, err = self.run_main(["--uboot", str(self.ub), "--private-vars", str(self.vars),
                                    "--output-dir", str(self.out)])
        self.assertEqual(rc, 1)
        self.assertIn("pin", err)

    def test_missing_wifi_refused_unless_allowed(self):
        args = ["--uboot", str(self.ub), "--uboot-sha256", self.sha, "--output-dir", str(self.out)]
        rc, _, err = self.run_main(args)
        self.assertEqual(rc, 1)
        self.assertIn("unreachable", err)
        rc, _, err = self.run_main(args + ["--allow-no-wifi"])
        self.assertEqual(rc, 0, err)

    def test_output_inside_repo_refused(self):
        inside = me.repo_root() / "research" / "openipc-migrate" / "results" / "should-not-exist"
        rc, _, err = self.run_main(["--uboot", str(self.ub), "--uboot-sha256", self.sha,
                                    "--private-vars", str(self.vars), "--output-dir", str(inside)])
        self.assertEqual(rc, 1)
        self.assertIn("inside the repository", err)
        self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
