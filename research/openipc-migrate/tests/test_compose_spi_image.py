#!/usr/bin/env python3
"""Offline tests for compose_spi_image.py with synthetic flash content only (no unit data)."""
import hashlib
import io
import os
import random
import stat
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import compose_spi_image as cs  # noqa: E402
import stock_layout as sl  # noqa: E402


def uimage(payload: bytes) -> bytes:
    hdr = bytearray(64)
    hdr[:4] = sl.UIMAGE_MAGIC
    hdr[12:16] = struct.pack(">I", len(payload))
    hdr[24:28] = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    hdr[32:40] = b"synthetc"
    hdr[4:8] = struct.pack(">I", zlib.crc32(bytes(hdr)) & 0xFFFFFFFF)
    return bytes(hdr) + payload


def firmware(rng) -> bytes:
    kern = uimage(rng.randbytes(0x150000))
    kern += b"\xff" * (sl.FW_KERNEL_LEN - len(kern))
    sq = bytearray(rng.randbytes(0x300000))
    sq[:4] = b"hsqs"
    sq[40:48] = struct.pack("<Q", len(sq))
    fw = kern + bytes(sq)
    return fw + b"\xff" * (sl.FW_LEN - len(fw))


class Compose(unittest.TestCase):
    def setUp(self):
        rng = random.Random(7)
        self.dump = rng.randbytes(sl.FLASH_BYTES)
        self.env_final = sl.build_env([("bootdelay", "0"), ("bootcmd", sl.FINAL_BOOTCMD), ("verify", "y"),
                                       ("bootargs", sl.FINAL_BOOTARGS)])
        self.env_openipc = sl.build_env([("bootcmd", sl.FINAL_BOOTCMD), ("wlanssid", "example-ssid")],
                                        sl.OIPC_ENV_LEN)
        self.fw = firmware(rng)
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        for n, b in (("dump.bin", self.dump), ("ef.img", self.env_final), ("eo.img", self.env_openipc),
                     ("fw.bin", self.fw), ("m1.bin", b"\x11" * 0x20000), ("m2.bin", self.dump[0x560000:0x580000])):
            (self.d / n).write_bytes(b)

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cs.main([str(a) for a in args])
        return rc, out.getvalue(), err.getvalue()

    def openipc_args(self, out="o.bin", **kw):
        a = {"--dump": self.d / "dump.bin", "--env-final": self.d / "ef.img", "--env-openipc": self.d / "eo.img",
             "--firmware": self.d / "fw.bin", "--out": self.d / out}
        a.update(kw)
        return ["openipc"] + [x for k, v in a.items() for x in (k, v)]

    def test_openipc_image_equals_installer_replay_and_layout(self):
        img = cs.compose_openipc(self.dump, self.env_final, self.env_openipc, self.fw)
        self.assertEqual(img, cs.replay_final(self.dump, self.env_final, self.env_openipc, self.fw))
        self.assertEqual(img[:sl.UBOOT_LEN], self.dump[:sl.UBOOT_LEN])
        self.assertEqual(img[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN], self.env_final)
        self.assertEqual(img[sl.OIPC_ENV_OFF:sl.OIPC_ENV_OFF + sl.OIPC_ENV_LEN], self.env_openipc)
        self.assertEqual(img[sl.FW_OFF:sl.FW_OFF + sl.FW_LEN], self.fw)
        self.assertEqual(img[sl.OVERLAY_OFF:], b"\xff" * sl.OVERLAY_LEN)
        self.assertTrue(all(cs.check_openipc_image(img, self.dump).values()))

    def test_cli_openipc_writes_private_file_and_pins_uboot(self):
        pin = hashlib.sha256(self.dump[:sl.UBOOT_LEN]).hexdigest()
        rc, out, err = self.run_main(*self.openipc_args(**{"--uboot-sha256": pin}))
        self.assertEqual(rc, 0, err)
        p = self.d / "o.bin"
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertIn('"equals_installer_replay": true', out)
        self.assertNotIn("example-ssid", out)
        rc, _, err = self.run_main(*self.openipc_args(**{"--uboot-sha256": pin}))
        self.assertEqual(rc, 1)                                   # never overwrites
        self.assertIn("exists", err)

    def test_wrong_uboot_pin_is_refused(self):
        rc, _, err = self.run_main(*self.openipc_args(**{"--uboot-sha256": "0" * 64}))
        self.assertEqual(rc, 1)
        self.assertIn("U-Boot differs", err)
        self.assertFalse((self.d / "o.bin").exists())

    def test_bad_inputs_are_refused(self):
        trial = sl.build_env([("bootcmd", sl.FINAL_BOOTCMD), ("verify", "y"),
                              ("bootargs", sl.FINAL_BOOTARGS + " " + sl.TRIAL_MARK)])
        stockish = sl.build_env([("bootcmd", sl.STOCK_BOOTCMD), ("verify", "y"), ("bootargs", sl.FINAL_BOOTARGS)])
        bad_fw = bytearray(self.fw); bad_fw[100] ^= 1
        for ef, eo, fw, msg in ((trial, self.env_openipc, self.fw, "bootargs"),
                                (stockish, self.env_openipc, self.fw, "bootcmd"),
                                (self.env_final, self.env_openipc[:-1] + b"\x01", self.fw, "env-openipc"),
                                (self.env_final, self.env_openipc, bytes(bad_fw), "uImage"),
                                (self.env_final, self.env_openipc, self.fw[:-1], "0x6b0000")):
            with self.assertRaises(cs.ComposeError) as cm:
                cs.check_inputs(self.dump, ef, eo, fw)
            self.assertIn(msg, str(cm.exception))

    def test_split_probe_header_is_refused(self):
        img = bytearray(cs.compose_openipc(self.dump, self.env_final, self.env_openipc, self.fw))
        img[sl.SPLIT_PROBE_OFF:sl.SPLIT_PROBE_OFF + 4] = sl.UIMAGE_MAGIC
        with self.assertRaises(cs.ComposeError):
            cs.check_openipc_image(bytes(img), self.dump)

    def test_restore_replaces_only_mdat(self):
        rc, out, err = self.run_main("restore", "--dump", self.d / "dump.bin", "--mdat1", self.d / "m1.bin",
                                     "--mdat2", self.d / "m2.bin", "--out", self.d / "r.bin")
        self.assertEqual(rc, 0, err)
        self.assertIn('"mdat_replaced": [\n  "mdat1"\n ]', out)
        img = (self.d / "r.bin").read_bytes()
        self.assertEqual(img[:0x540000], self.dump[:0x540000])
        self.assertEqual(img[0x540000:0x560000], b"\x11" * 0x20000)
        self.assertEqual(img[0x560000:], self.dump[0x560000:])
        rc, out, _ = self.run_main("restore", "--dump", self.d / "dump.bin", "--out", self.d / "r2.bin")
        self.assertEqual(rc, 0)
        self.assertIn('"identical_to_dump": true', out)


if __name__ == "__main__":
    unittest.main()
