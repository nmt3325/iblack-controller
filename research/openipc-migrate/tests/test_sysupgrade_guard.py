#!/usr/bin/env python3
"""update/sysupgrade-guard.sh (the fixed-slot wrapper installed as /usr/sbin/sysupgrade) under host BusyBox ash.

PATH offers only BusyBox applets, /proc/mtd is a file, and the original updater is a stand-in that records the
arguments it was started with (so "nothing was written" means: it was never started). Images are synthetic: a
uImage header (magic, data size, name) and a squashfs superblock (magic, bytes_used) padded to the wanted size.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from update_fixture import SOC, plain, squashfs, tgz, uimage  # noqa: E402

GUARD = HERE.parent / "update" / "sysupgrade-guard.sh"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
APPLETS = ("awk", "cat", "dd", "gzip", "head", "mktemp", "od", "rm", "sed", "tar", "tr", "wc")
KERNEL_SLOT, ROOTFS_SLOT = 0x1D0000, 0x4E0000
FIXED = ('dev:    size   erasesize  name\nmtd0: 00060000 00010000 "boot"\nmtd1: 00010000 00010000 "env"\n'
         'mtd2: 001d0000 00010000 "kernel"\nmtd3: 004e0000 00010000 "rootfs"\nmtd4: 000e0000 00010000 "rootfs_data"\n')
# The standard OpenIPC 8 MiB NOR layout (U-Boot 256K, env 64K, kernel 2048K, rootfs 5120K, rootfs_data rest).
STANDARD = ('dev:    size   erasesize  name\nmtd0: 00040000 00010000 "boot"\nmtd1: 00010000 00010000 "env"\n'
            'mtd2: 00200000 00010000 "kernel"\nmtd3: 00500000 00010000 "rootfs"\nmtd4: 000b0000 00010000 "rootfs_data"\n')


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class Guard(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sysupgrade-guard-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        b = self.d / "bin"
        b.mkdir()
        for applet in APPLETS:
            (b / applet).symlink_to(BUSYBOX)
        (b / "logger").write_text('#!/bin/sh\nshift 2\necho "$*" >> "$D/syslog"\n')
        (b / "logger").chmod(0o755)
        self.mtd = self.d / "mtd"
        self.mtd.write_text(FIXED)
        self.real = self.d / "real-sysupgrade"
        self.set_real("1.0.84")

    def set_real(self, version: str) -> None:
        self.real.write_text(f'#!/bin/sh\nscr_version={version}\nprintf "%s\\n" "$@" > "$D/real-args"\nexit 7\n')
        self.real.chmod(0o755)

    def put(self, name: str, data: bytes) -> str:
        p = self.d / name
        p.write_bytes(data)
        return str(p)

    def run_guard(self, *args, **env):
        e = {"PATH": str(self.d / "bin"), "D": str(self.d), "SYSUPGRADE_GUARD_REAL": str(self.real),
             "SYSUPGRADE_GUARD_PROC_MTD": str(self.mtd)}
        e.update(env)
        r = subprocess.run([BUSYBOX, "sh", str(GUARD), *args], env=e, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr

    def real_args(self):
        p = self.d / "real-args"
        return p.read_text().splitlines() if p.exists() else None

    def assert_passed(self, *args, **env):
        rc, out = self.run_guard(*args, **env)
        self.assertEqual(rc, 7, out)  # the stand-in's exit code: the guard exec'd it
        self.assertEqual(self.real_args(), ["-z", *args])
        return out

    def assert_refused(self, *args, why: str = "", **env):
        rc, out = self.run_guard(*args, **env)
        self.assertEqual(rc, 1, out)
        self.assertIn("refused, nothing was written", out)
        self.assertIn(why, out)
        self.assertIsNone(self.real_args(), "the updater must not start")
        self.assertIn("refused:", (self.d / "syslog").read_text())
        return out

    # --- what goes through ---
    def test_local_images_that_fit_pass_with_no_self_update(self):
        k = self.put("uImage", uimage(1864732))
        r = self.put("rootfs", squashfs(5103616, used=5100346))
        self.assert_passed(f"--kernel={k}", f"--rootfs={r}")
        self.assertIn("passing on:", (self.d / "syslog").read_text())

    def test_images_exactly_the_slot_size_pass(self):
        k = self.put("uImage", uimage(KERNEL_SLOT))
        r = self.put("rootfs", squashfs(ROOTFS_SLOT, used=ROOTFS_SLOT))
        self.assert_passed(f"--kernel={k}", f"--rootfs={r}", "-x")

    def test_web_upload_of_a_plain_package_passes(self):
        a = self.put("fw.tgz", plain(uimage(1864732), squashfs(5103616)))
        self.assert_passed("--web", "-k", "-r", f"--archive={a}")
        self.assert_passed("--web", "-k", "-r", "-n", f"--archive={a}")

    def test_archive_with_dot_slash_names_passes(self):
        a = self.put("fw.tgz", tgz({f"./uImage.{SOC}": uimage(4096), f"./rootfs.squashfs.{SOC}": squashfs(8192)}))
        self.assert_passed(f"--archive={a}")

    def test_overlay_wipe_and_read_only_commands_pass(self):
        for args in (("-n",), ("-n", "--web"), ("-n", "-x"), ("--help",), ("-h",), ("--history",),
                     ("--list-builds",), ("--list-builds=5",), (), ("--web",)):
            with self.subTest(args=args):
                (self.d / "real-args").unlink(missing_ok=True)
                self.assert_passed(*args)

    def test_read_only_commands_pass_on_any_layout(self):
        self.mtd.write_text(STANDARD)
        self.assert_passed("--list-builds")
        self.set_real("9.9.9")
        self.assert_passed("--help")

    def test_dry_run_names_the_command_and_starts_nothing(self):
        k = self.put("uImage", uimage(4096))
        rc, out = self.run_guard(f"--kernel={k}", SYSUPGRADE_GUARD_DRYRUN="1")
        self.assertEqual(rc, 0, out)
        self.assertIn(f"would run: {self.real} -z --kernel={k}", out)
        self.assertIsNone(self.real_args())

    def test_status(self):
        rc, out = self.run_guard("--guard-status")
        self.assertEqual(rc, 0, out)
        self.assertIn("flash layout: fixed slots", out)
        self.assertIn("updater version: known", out)
        self.assertIsNone(self.real_args())
        self.mtd.write_text(STANDARD)
        self.set_real("1.0.90")
        out = self.run_guard("--guard-status")[1]
        self.assertIn("NOT the expected fixed slots", out)
        self.assertIn("not reviewed", out)

    # --- the online paths and the switches that turn checks off ---
    def test_online_update_is_refused(self):
        for args, why in ((("-k", "-r"), "online update"), (("--web", "-k", "-r"), "online update"),
                          (("-k",), "online update"), (("-r", "-n"), "online update"),
                          (("-k", "-r", "-z", "--build=nightly-20261115-4cc6476"), "split package"),
                          (("--channel=nightly",), "split package"), (("--url=https://example.invalid/x.tgz",), "split"),
                          (("-u",), "U-Boot"), (("--no_ramfs", "-n"), "RAM")):
            with self.subTest(args=args):
                self.assert_refused(*args, why=why)

    def test_force_switches_are_refused(self):
        a = self.put("fw.tgz", plain(uimage(4096), squashfs(8192)))
        for flag in ("-f", "--force_all", "--force_md5", "--force_soc", "--force_ver"):
            with self.subTest(flag=flag):
                self.assert_refused("--web", flag, f"--archive={a}", why="checks together")

    def test_unknown_options_are_refused(self):
        for arg in ("--wipe", "-y", "--kernel", "--archive", "nightly"):
            with self.subTest(arg=arg):
                self.assert_refused(arg, why="unknown option")

    # --- images that do not fit or are not what they claim ---
    def test_split_package_is_refused(self):
        a = self.put("split.tgz", tgz({f"firmware.bin.{SOC}.md5sum": b"x\n", f"firmware.bin.{SOC}": b"\0" * 70000}))
        self.assert_refused("--web", "-k", "-r", f"--archive={a}", why="overruns rootfs_data")
        a = self.put("both.tgz", plain(uimage(4096), squashfs(8192), {f"firmware.bin.{SOC}": b"\0" * 100}))
        self.assert_refused(f"--archive={a}", why="overruns rootfs_data")

    def test_rootfs_a_little_too_big_is_refused(self):
        # nightly-20261115-4cc6476: rootfs.squashfs is 5124096 bytes, 12288 over the slot.
        a = self.put("fw.tgz", plain(uimage(1864788), squashfs(5124096)))
        self.assert_refused("--web", "-k", "-r", f"--archive={a}", why="12288 more than the rootfs slot holds")
        r = self.put("rootfs", squashfs(ROOTFS_SLOT + 1))
        self.assert_refused(f"--rootfs={r}", why="1 more than the rootfs slot holds")

    def test_kernel_too_big_is_refused(self):
        k = self.put("uImage", uimage(KERNEL_SLOT + 1))
        self.assert_refused(f"--kernel={k}", why="the kernel slot holds 1900544")
        a = self.put("fw.tgz", plain(uimage(KERNEL_SLOT + 64), squashfs(8192)))
        self.assert_refused(f"--archive={a}", why="the kernel slot holds")

    def test_bad_images_are_refused(self):
        cases = (("kernel", uimage(4096, magic=0x12345678), "not a uImage"),
                 ("kernel", uimage(4096, name="Linux-3.0.8-hi3518cv100"), "not hi3518ev100"),
                 ("kernel", uimage(4096, data_len=8192), "cut short"),
                 ("kernel", b"\x27\x05\x19\x56", "header is short"),
                 ("rootfs", squashfs(8192, magic=b"sqsh"), "not a squashfs"),
                 ("rootfs", squashfs(8192, used=9000), "cut short"),
                 ("rootfs", squashfs(8192, used=1 << 32), "4 GiB"),
                 ("rootfs", b"hsqs" + b"\0" * 20, "superblock is short"))
        for kind, data, why in cases:
            with self.subTest(kind=kind, why=why):
                f = self.put(kind, data)
                self.assert_refused(f"--{kind}={f}", why=why)

    def test_bad_archives_are_refused(self):
        cases = ((plain(uimage(4096), squashfs(8192), {"etc/passwd": b"x"}), "not part of a plain"),
                 (tgz({f"uImage.{SOC}": uimage(4096)}), "is not a plain"),
                 (tgz({f"uImage.hi3518cv100": uimage(4096), f"rootfs.squashfs.hi3518cv100": squashfs(8192)}),
                  "not part of a plain"),
                 (plain(uimage(4096, name="Linux-3.0.8-hi3516cv100"), squashfs(8192)), "not hi3518ev100"),
                 (plain(uimage(4096), squashfs(8192, magic=b"\0\0\0\0")), "not a squashfs"),
                 (b"not a gzip file", "is not a plain"))
        for data, why in cases:
            with self.subTest(why=why):
                a = self.put("fw.tgz", data)
                self.assert_refused("--web", "-k", "-r", f"--archive={a}", why=why)

    def test_missing_files_and_mixed_sources_are_refused(self):
        self.assert_refused(f"--kernel={self.d}/nothing", why="not found")
        self.assert_refused(f"--archive={self.d}/nothing.tgz", why="not found")
        a = self.put("fw.tgz", plain(uimage(4096), squashfs(8192)))
        k = self.put("uImage", uimage(4096))
        self.assert_refused(f"--archive={a}", f"--kernel={k}", why="either --archive")

    # --- the guard's own preconditions ---
    def test_other_layout_refuses_every_write(self):
        self.mtd.write_text(STANDARD)
        k = self.put("uImage", uimage(4096))
        self.assert_refused(f"--kernel={k}", why="fixed slots")
        self.assert_refused("-n", why="fixed slots")
        self.mtd.write_text(FIXED.replace('"rootfs_data"', '"overlay"'))
        self.assert_refused(f"--kernel={k}", why="fixed slots")
        self.mtd.unlink()
        self.assert_refused(f"--kernel={k}", why="fixed slots")

    def test_unreviewed_updater_refuses_every_write(self):
        self.set_real("1.0.90")
        k = self.put("uImage", uimage(4096))
        self.assert_refused(f"--kernel={k}", why="v1.0.90, not a version reviewed")
        self.assert_passed("--history")
        k2 = self.put("uImage2", uimage(4096))
        (self.d / "real-args").unlink()
        self.assert_passed(f"--kernel={k2}", SYSUPGRADE_GUARD_KNOWN="1.0.84 1.0.90")

    def test_missing_updater_is_refused(self):
        self.real.unlink()
        self.assert_refused("--history", why="is missing")


if __name__ == "__main__":
    unittest.main()
