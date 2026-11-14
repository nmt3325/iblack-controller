#!/usr/bin/env python3
"""Offline unit tests for migrate_plan.py.

These tests build synthetic uImage+squashfs firmware blobs and a synthetic
U-Boot in memory. They never touch a camera, a network, or real flash. All
fixtures are generated so the suite is self-contained and deterministic.
"""
import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import migrate_plan as mp  # noqa: E402


def build_uimage(data_size, soc="hi3518ev100", corrupt_hcrc=False,
                 corrupt_dcrc=False, name=None):
    data = bytes((i % 251) for i in range(data_size))
    if name is None:
        name = "Linux-3.0.8-" + soc
    hdr = bytearray(64)
    struct.pack_into(">I", hdr, 0, mp.UIMAGE_MAGIC)
    struct.pack_into(">I", hdr, 12, data_size)
    struct.pack_into(">I", hdr, 16, 0x80008000)
    struct.pack_into(">I", hdr, 20, 0x80008000)
    dcrc = zlib.crc32(data) & 0xFFFFFFFF
    if corrupt_dcrc:
        dcrc ^= 0xFFFFFFFF
    struct.pack_into(">I", hdr, 24, dcrc)
    nb = name.encode("latin1")[:31]
    hdr[32:32 + len(nb)] = nb
    tmp = bytearray(hdr)
    struct.pack_into(">I", tmp, 4, 0)
    hcrc = zlib.crc32(bytes(tmp)) & 0xFFFFFFFF
    if corrupt_hcrc:
        hcrc ^= 0xFFFFFFFF
    struct.pack_into(">I", hdr, 4, hcrc)
    return bytes(hdr) + data


def build_firmware(data_size=0x100000, soc="hi3518ev100", place_squashfs=True,
                   corrupt_hcrc=False, corrupt_dcrc=False, extra=0):
    uimg = build_uimage(data_size, soc=soc, corrupt_hcrc=corrupt_hcrc,
                        corrupt_dcrc=corrupt_dcrc)
    total_len = (mp.OVERLAY_BASE - mp.FW_BASE) + extra
    buf = bytearray(total_len)
    buf[:len(uimg)] = uimg
    blocks = (64 + data_size + mp.ERASE_BYTES - 1) // mp.ERASE_BYTES
    sq_off = blocks * mp.ERASE_BYTES
    if place_squashfs:
        struct.pack_into("<I", buf, sq_off, mp.SQUASHFS_LE)
        want_overlay_rel = mp.OVERLAY_BASE - mp.FW_BASE
        bytes_used = want_overlay_rel - sq_off
        struct.pack_into("<I", buf, sq_off + 40, bytes_used)
    return bytes(buf)


def valid_uboot(n=122972):
    return bytes((i % 253) for i in range(n))


class TestFirmwareChecks(unittest.TestCase):
    def test_valid_layout_passes(self):
        plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.assertTrue(plan["all_ok"])
        self.assertTrue(plan["firmware"]["all_ok"])
        self.assertTrue(all(plan["firmware"]["checks"].values()))
        self.assertTrue(all(plan["invariants"].values()))
        self.assertEqual(plan["firmware"]["overlay_abs"], mp.OVERLAY_BASE)

    def test_wrong_soc_rejected(self):
        plan = mp.build_plan(build_firmware(soc="hi3516ev200"), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["soc_matches"])

    def test_bad_header_crc_rejected(self):
        plan = mp.build_plan(build_firmware(corrupt_hcrc=True), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["uimage_header_crc_ok"])

    def test_bad_data_crc_rejected(self):
        plan = mp.build_plan(build_firmware(corrupt_dcrc=True), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["uimage_data_crc_ok"])

    def test_kernel_over_2mib_rejected(self):
        plan = mp.build_plan(build_firmware(data_size=0x200000), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["kernel_within_2mib"])

    def test_squashfs_misplaced_rejected(self):
        plan = mp.build_plan(build_firmware(place_squashfs=False), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["squashfs_follows_kernel"])

    def test_image_not_ending_at_overlay_rejected(self):
        plan = mp.build_plan(build_firmware(extra=0x10000), valid_uboot(), "image")
        self.assertFalse(plan["all_ok"])
        self.assertFalse(plan["firmware"]["checks"]["image_ends_at_overlay"])

    def test_missing_uimage_magic_rejected(self):
        with self.assertRaises(SystemExit):
            mp.build_plan(b"\x00" * 0x6b0000, valid_uboot(), "image")


class TestUbootChecks(unittest.TestCase):
    def test_uboot_too_big_rejected(self):
        with self.assertRaises(SystemExit):
            mp.build_plan(build_firmware(), b"\x00" * (0x40000 + 1), "image")

    def test_uboot_empty_rejected(self):
        with self.assertRaises(SystemExit):
            mp.build_plan(build_firmware(), b"", "image")


class TestStockMapping(unittest.TestCase):
    def setUp(self):
        self.plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.regions = {r["name"]: r for r in self.plan["regions"]}

    def test_each_region_fully_and_contiguously_mapped(self):
        for r in self.plan["regions"]:
            segs = r["stock_mtd_map"]
            self.assertEqual(sum(s["length"] for s in segs), r["abs_bytes"])
            self.assertEqual(segs[0]["abs_start"], r["abs_start"])
            self.assertEqual(segs[-1]["abs_end"], r["abs_end"])
            for a, b in zip(segs, segs[1:]):
                self.assertEqual(a["abs_end"], b["abs_start"])

    def test_bootloader_in_boot_partition_only(self):
        segs = self.regions["bootloader"]["stock_mtd_map"]
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["stock_name"], "boot")
        self.assertEqual(segs[0]["offset_in_part"], 0)

    def test_overlay_maps_into_bakfs(self):
        segs = self.regions["overlay"]["stock_mtd_map"]
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["stock_name"], "bakfs")
        self.assertEqual(segs[0]["offset_in_part"], 0x180000)
        self.assertEqual(segs[0]["length"], 0x100000)

    def test_firmware_crosses_all_six_partitions(self):
        self.assertEqual(self.plan["stock_partitions_touched"],
                         ["appfs", "bakfs", "boot", "mdat1", "mdat2", "rootfs"])


class TestSafetyInvariants(unittest.TestCase):
    def test_flash_write_never_allowed(self):
        plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.assertIs(plan["flash_write_allowed"], False)
        plan2 = mp.build_plan(build_firmware(), valid_uboot(), "erase")
        self.assertIs(plan2["flash_write_allowed"], False)

    def test_default_order_is_bootloader_first(self):
        plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.assertEqual(plan["write_order"][0], "bootloader")
        self.assertEqual(plan["write_order"],
                         ["bootloader", "env", "firmware", "overlay"])
        self.assertIn("bootloader", plan["write_order_alt"])

    def test_private_backup_required(self):
        plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.assertEqual(plan["private_backup_required"], ["mdat1", "mdat2"])

    def test_erase_bytes_flagged_assumption(self):
        plan = mp.build_plan(build_firmware(), valid_uboot(), "image")
        self.assertTrue(plan["erase_bytes_is_assumption"])


class TestV2Layout(unittest.TestCase):
    def setUp(self):
        self.fw = build_firmware()
        self.ub = valid_uboot()
        self.plan = mp.build_plan(self.fw, self.ub, "image")
        self.regions = {r["name"]: r for r in self.plan["regions"]}

    def test_bootloader_region_is_full_256k(self):
        r = self.regions["bootloader"]
        self.assertEqual((r["abs_start"], r["abs_end"]), (0, 0x40000))
        self.assertEqual(r["stock_mtd_map"][0]["length"], 0x40000)

    def test_regions_tile_whole_chip(self):
        self.assertTrue(self.plan["invariants"]["regions_tile_whole_chip"])
        self.assertTrue(self.plan["invariants"]["regions_erase_aligned"])
        rs = self.plan["regions"]
        self.assertEqual(rs[0]["abs_start"], 0)
        self.assertEqual(rs[-1]["abs_end"], 0x800000)
        for a, b in zip(rs, rs[1:]):
            self.assertEqual(a["abs_end"], b["abs_start"])
        self.assertFalse(self.plan["final_flash_depends_on_old_contents"])

    def test_boot_image_and_expected_flash_hashes(self):
        bimg = self.ub + b"\xff" * (0x40000 - len(self.ub))
        self.assertEqual(self.plan["boot_image_sha256"], hashlib.sha256(bimg).hexdigest())
        full = bimg + b"\xff" * 0x10000 + self.fw + b"\xff" * 0x100000
        self.assertEqual(self.plan["expected_flash_sha256_if_env_erased"],
                         hashlib.sha256(full).hexdigest())

    def test_env_modes(self):
        self.assertFalse(self.regions["env"]["erase_only"])
        erase = mp.build_plan(self.fw, self.ub, "erase")
        self.assertTrue({r["name"]: r for r in erase["regions"]}["env"]["erase_only"])
        self.assertEqual(erase["env_mode"], "erase")
        with self.assertRaises(SystemExit):
            mp.build_plan(self.fw, self.ub, "keep")

    def test_recovery_caveat_recorded(self):
        rat = self.plan["ordering_rationale"]
        self.assertIn("Ethernet", rat["recovery_caveat"])
        self.assertIn("tftpboot", rat["recovery_path"])


class TestReadBounded(unittest.TestCase):
    def test_oversized_refused(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"\x00" * 16)
            path = f.name
        try:
            with self.assertRaises(SystemExit):
                mp.read_bounded(mp.Path(path), 8)
        finally:
            os.unlink(path)

    def test_directory_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                mp.read_bounded(mp.Path(d), 0x1000)


class TestMainCli(unittest.TestCase):
    def _write(self, d):
        fwp = os.path.join(d, "fw.bin")
        ubp = os.path.join(d, "uboot.bin")
        with open(fwp, "wb") as f:
            f.write(build_firmware())
        with open(ubp, "wb") as f:
            f.write(valid_uboot())
        return fwp, ubp

    def test_main_success_and_sha_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            fwp, ubp = self._write(d)
            outp = os.path.join(d, "plan.json")
            argv = ["migrate_plan.py", "--firmware", fwp, "--uboot", ubp,
                    "--output", outp]
            old = sys.argv
            sys.argv = argv
            try:
                rc = mp.main()
            finally:
                sys.argv = old
            self.assertEqual(rc, 0)
            with open(outp) as f:
                data = json.load(f)
            self.assertTrue(data["all_ok"])
            self.assertEqual(len(data["inputs"]["firmware_sha256"]), 64)
            self.assertEqual(len(data["inputs"]["uboot_sha256"]), 64)
            self.assertFalse(data["flash_write_allowed"])

    def test_main_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            fwp, ubp = self._write(d)
            outp = os.path.join(d, "plan.json")
            with open(outp, "w") as f:
                f.write("{}")
            argv = ["migrate_plan.py", "--firmware", fwp, "--uboot", ubp,
                    "--output", outp]
            old = sys.argv
            sys.argv = argv
            try:
                with self.assertRaises(SystemExit):
                    mp.main()
            finally:
                sys.argv = old


if __name__ == "__main__":
    unittest.main()
