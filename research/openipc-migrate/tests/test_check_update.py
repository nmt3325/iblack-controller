#!/usr/bin/env python3
"""update/check_update.py: the host-side fit check for OpenIPC updates, on synthetic packages."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "update")]
import check_update as cu  # noqa: E402
from update_fixture import SOC, plain, squashfs, tgz, uimage  # noqa: E402


class CheckUpdate(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="check-update-"))
        self.addCleanup(shutil.rmtree, self.d, True)

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cu.main([str(a) for a in argv])
        return rc, out.getvalue()

    def pkg(self, data: bytes) -> Path:
        p = self.d / "pkg.tgz"
        p.write_bytes(data)
        return p

    def test_slots_come_from_the_final_layout(self):
        self.assertEqual((cu.KERNEL_SLOT, cu.ROOTFS_SLOT), (0x1D0000, 0x4E0000))

    def test_installed_build_fits(self):
        # nightly-20261113-a0f341e, the build on unit 2: kernel 1864732, rootfs 5103616 bytes.
        rc, out = self.run_main(self.pkg(plain(uimage(1864732), squashfs(5103616, used=5100346))), "--json")
        self.assertEqual(rc, 0, out)
        res = json.loads(out)
        self.assertTrue(res["fits"])
        self.assertEqual((res["kernel"]["spare"], res["rootfs"]["spare"]), (35812, 8192))

    def test_exact_slot_sizes_fit(self):
        rc, out = self.run_main(self.pkg(plain(uimage(cu.KERNEL_SLOT), squashfs(cu.ROOTFS_SLOT))))
        self.assertEqual(rc, 0, out)
        self.assertIn("FITS", out)

    def test_rootfs_over_by_12k_does_not_fit(self):
        # nightly-20261115-4cc6476: rootfs 5124096 bytes.
        rc, out = self.run_main(self.pkg(plain(uimage(1864788), squashfs(5124096))))
        self.assertEqual(rc, 1)
        self.assertIn("12288 more than the rootfs slot", out)

    def test_split_package_does_not_fit(self):
        rc, out = self.run_main(self.pkg(tgz({f"firmware.bin.{SOC}.md5sum": b"x\n", f"firmware.bin.{SOC}": b"\0" * 99})))
        self.assertEqual(rc, 1)
        self.assertIn("split package", out)

    def test_damaged_or_foreign_images_do_not_fit(self):
        cases = ((plain(uimage(4096, bad_dcrc=True), squashfs(8192)), "data CRC"),
                 (plain(uimage(4096, data_len=9000), squashfs(8192)), "cut short"),
                 (plain(uimage(4096, name="Linux-3.0.8-hi3518cv100"), squashfs(8192)), "not hi3518ev100"),
                 (plain(uimage(cu.KERNEL_SLOT + 4), squashfs(8192)), "more than the kernel slot"),
                 (plain(uimage(4096), squashfs(8192, used=8193)), "cut short"),
                 (plain(uimage(4096), squashfs(8192, magic=b"sqsh")), "not a squashfs"),
                 (plain(uimage(4096), squashfs(8192), {"etc/shadow": b"x"}), "not a plain"),
                 (tgz({f"uImage.{SOC}": uimage(4096)}), "is missing"))
        for data, why in cases:
            with self.subTest(why=why):
                rc, out = self.run_main(self.pkg(data))
                self.assertEqual(rc, 1, out)
                self.assertIn(why, out)

    def test_header_crc_is_checked(self):
        k = bytearray(uimage(4096))
        k[40] ^= 1  # inside the name: header CRC no longer matches
        rc, out = self.run_main("--kernel", self.write("k", bytes(k)))
        self.assertEqual(rc, 1)
        self.assertIn("header CRC", out)

    def write(self, name: str, data: bytes) -> Path:
        p = self.d / name
        p.write_bytes(data)
        return p

    def test_separate_images(self):
        rc, out = self.run_main("--kernel", self.write("k", uimage(4096)), "--rootfs", self.write("r", squashfs(8192)))
        self.assertEqual(rc, 0, out)

    def test_unreadable_input_is_a_usage_error(self):
        self.assertEqual(self.run_main(self.write("x.tgz", b"not gzip"))[0], 2)
        self.assertEqual(self.run_main("--kernel", self.d / "missing")[0], 2)


if __name__ == "__main__":
    unittest.main()
