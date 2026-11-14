#!/usr/bin/env python3
"""Offline simulator for installer/openipc_migrate.sh.

Builds a fake 8 MiB stock flash as six file-backed "mtdblock" files plus a
fake /proc/mtd, then runs the REAL installer with --dry-run-dir (a mode that
forces real-write OFF, can never reboot and is the only mode in which the
OPENIPC_MIGRATE_SELFTEST_FAULT seams exist). No camera, no network, no real
flash device is touched. Sandboxes live in /dev/shm when available.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.join(HERE, "..", "installer", "openipc_migrate.sh")
BB = shutil.which("busybox")
SH = shutil.which("dash") or shutil.which("sh")
FLASH = 0x800000
BOOT_LEN, ENV_BASE, ENV_LEN = 0x40000, 0x40000, 0x10000
FW_BASE, OVERLAY = 0x50000, 0x700000
STOCK = [("boot", 0x200000), ("rootfs", 0xc0000), ("appfs", 0x280000),
         ("mdat1", 0x20000), ("mdat2", 0x20000), ("bakfs", 0x280000)]
CONFIRM = "yes-migrate-this-camera-to-openipc"
REBOOT = "yes-reboot-into-openipc"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rbytes(seed: int, n: int) -> bytes:
    return random.Random(seed).randbytes(n)


def build_firmware() -> bytes:
    body = bytearray(rbytes(7, OVERLAY - FW_BASE))
    body[0:4] = bytes.fromhex("27051956")
    return bytes(body)


def build_env() -> bytes:
    data = b"bootcmd=run bootflash\0bootdelay=1\0wlandev=test\0\0"
    return b"\x12\x34\x56\x78" + data + b"\0" * (ENV_LEN - 4 - len(data))


class Sandbox:
    def __init__(self):
        base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
        self.d = tempfile.mkdtemp(prefix="migsim.", dir=base)
        self.dev = os.path.join(self.d, "dev")
        os.mkdir(self.dev)
        self.stage = os.path.join(self.d, "stage")
        self.fw = build_firmware()
        self.ub = rbytes(11, 122972).rstrip(b"\xff") + b"\x01"
        self.env = build_env()
        self.fw_path = self._put("firmware.bin", self.fw)
        self.ub_path = self._put("uboot.bin", self.ub)
        self.env_path = self._put("env.img", self.env)
        self.original = rbytes(3, FLASH)
        self._write_devs(self.original)
        self.proc_mtd = os.path.join(self.d, "proc_mtd")
        self.write_proc_mtd()
        self.hook_log = os.path.join(self.d, "hook.log")

    def _put(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def _slices(self):
        off = 0
        for i, (_, size) in enumerate(STOCK):
            yield i, off, size
            off += size

    def _write_devs(self, flash):
        for i, off, size in self._slices():
            with open(os.path.join(self.dev, f"mtdblock{i}"), "wb") as f:
                f.write(flash[off:off + size])

    def read_flash(self) -> bytes:
        out = b""
        for i, _, _ in self._slices():
            with open(os.path.join(self.dev, f"mtdblock{i}"), "rb") as f:
                out += f.read()
        return out

    def write_proc_mtd(self, rows=None):
        rows = rows or [(f"mtd{i}", size, 0x10000, name) for i, (name, size) in enumerate(STOCK)]
        with open(self.proc_mtd, "w") as f:
            f.write("dev:    size   erasesize  name\n")
            for dev, size, erase, name in rows:
                f.write(f'{dev}: {size:08x} {erase:08x} "{name}"\n')

    def expected_after(self, env_mode="image") -> bytes:
        boot = self.ub + b"\xff" * (BOOT_LEN - len(self.ub))
        env = self.env if env_mode == "image" else b"\xff" * ENV_LEN
        img = boot + env + self.fw + b"\xff" * (FLASH - OVERLAY)
        assert len(img) == FLASH
        return img

    def write_hook(self, fail_verb=None):
        p = os.path.join(self.d, "hook.sh")
        fail = f'[ "$1" = "{fail_verb}" ] && exit 9\n' if fail_verb else ""
        with open(p, "w") as f:
            f.write(f'echo "$*" >> "{self.hook_log}"\n{fail}exit 0\n')
        return p

    def hook_verbs(self):
        if not os.path.exists(self.hook_log):
            return []
        with open(self.hook_log) as f:
            return [line.strip() for line in f if line.strip()]

    def run(self, extra_env=None, args=None, env_mode="image", sim=True,
            fw_sha=None, ub_path=None, ub_sha=None, timeout=180):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MTD_PROC": self.proc_mtd}
        env.update(extra_env or {})
        ub_path = ub_path or self.ub_path
        cmd = [SH, INSTALLER, "--busybox", BB,
               "--firmware", self.fw_path, "--firmware-sha256", fw_sha or sha256(self.fw),
               "--uboot", ub_path, "--uboot-sha256", ub_sha or sha256(self.ub),
               "--stage", self.stage, "--stability-reads", "2"]
        if env_mode == "image":
            cmd += ["--env-image", self.env_path, "--env-sha256", sha256(self.env)]
        elif env_mode == "erase":
            cmd += ["--env-erase"]
        if sim:
            cmd += ["--dry-run-dir", self.dev]
        cmd += list(args or [])
        return subprocess.run(cmd, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def cleanup(self):
        shutil.rmtree(self.d, ignore_errors=True)


@unittest.skipUnless(BB and SH, "busybox and a POSIX sh are required")
class InstallerSimulation(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def assertUnchanged(self):
        self.assertEqual(sha256(self.sb.read_flash()), sha256(self.sb.original))

    def assertMigrated(self, env_mode="image"):
        self.assertEqual(sha256(self.sb.read_flash()), sha256(self.sb.expected_after(env_mode)))

    def log_text(self):
        with open(os.path.join(self.sb.stage, "migrate.log")) as f:
            return f.read()

    # ---- success paths ----------------------------------------------------
    def test_clean_migration_with_env_image(self):
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertMigrated("image")
        self.assertIn("final sweep", r.stdout)
        with open(os.path.join(self.sb.stage, "migrate.result")) as f:
            self.assertEqual(f.read().strip(), "ok")
        self.assertIn("region firmware", self.log_text())
        self.assertFalse(os.path.exists(os.path.join(self.sb.stage, ".lock")))
        self.assertFalse(os.path.exists(os.path.join(self.sb.stage, ".tmp")))

    def test_clean_migration_with_env_erase(self):
        r = self.sb.run(env_mode="erase")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertMigrated("erase")

    def test_result_independent_of_original_contents(self):
        self.sb.original = bytes(FLASH)
        self.sb._write_devs(self.sb.original)
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertMigrated("image")

    def test_backup_matches_original_mdat_and_is_private(self):
        r = self.sb.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        priv = os.path.join(self.sb.stage, "private")
        self.assertEqual(stat.S_IMODE(os.stat(priv).st_mode), 0o700)
        for name, off, size in (("mdat1", 0x540000, 0x20000), ("mdat2", 0x560000, 0x20000)):
            p = os.path.join(priv, f"{name}.bin")
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            with open(p, "rb") as f:
                self.assertEqual(f.read(), self.sb.original[off:off + size])
        self.assertNotIn(sha256(self.sb.original[0x540000:0x560000]), r.stdout + r.stderr + self.log_text())

    def test_first_short_write_is_repaired_by_rewrite(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "short_write_firmware"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rewrite 1/2", r.stdout)
        self.assertMigrated()

    def test_transient_bad_read_tolerated_by_majority(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "transient_read"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("note: 1 of 3 readback(s) mismatched", r.stdout)
        self.assertNotIn("rewrite", r.stdout)
        self.assertMigrated()

    # ---- persistent failures -> PARTIAL STATE (rc 2), never reboot ---------
    def test_persistent_short_write_is_partial_state(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "short_write_firmware_always"},
                        args=["--allow-reboot"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("PARTIAL STATE", r.stderr)
        self.assertIn("after 2 rewrite(s)", r.stderr)
        self.assertNotIn("reboot authorized", r.stdout)

    def test_corruption_after_write_is_partial_state(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "corrupt_after_write_firmware"},
                        args=["--max-rewrites", "1"])
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("after 1 rewrite(s)", r.stderr)

    # ---- clean aborts before any write (rc 1, flash untouched) -------------
    def test_firmware_sha_mismatch_aborts(self):
        r = self.sb.run(fw_sha="0" * 64)
        self.assertEqual(r.returncode, 1)
        self.assertIn("firmware sha256 mismatch", r.stderr)
        self.assertUnchanged()

    def test_bad_uimage_magic_aborts(self):
        bad = bytearray(self.sb.fw)
        bad[0] = 0
        self.sb.fw = bytes(bad)
        self.sb.fw_path = self.sb._put("firmware.bin", self.sb.fw)
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("uImage", r.stderr)
        self.assertUnchanged()

    def test_wrong_partition_table_aborts(self):
        rows = [(f"mtd{i}", size, 0x10000, name) for i, (name, size) in enumerate(STOCK)]
        rows[2] = ("mtd2", 0x200000, 0x10000, "appfs")
        self.sb.write_proc_mtd(rows)
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertUnchanged()

    def test_wrong_erasesize_aborts(self):
        rows = [(f"mtd{i}", size, 0x10000, name) for i, (name, size) in enumerate(STOCK)]
        rows[0] = ("mtd0", 0x200000, 0x20000, "boot")
        self.sb.write_proc_mtd(rows)
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("erasesize", r.stderr)
        self.assertUnchanged()

    def test_unstable_read_aborts_before_any_write(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "unstable_read"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("unstable read", r.stderr)
        self.assertNotIn("PARTIAL", r.stderr)
        flash = self.sb.read_flash()
        self.assertEqual(flash[:0x580000], self.sb.original[:0x580000])

    def test_oversized_uboot_aborts(self):
        big = os.path.join(self.sb.d, "big.bin")
        with open(big, "wb") as f:
            f.write(b"\x01" * (BOOT_LEN + 1))
        r = self.sb.run(ub_path=big, ub_sha=sha256(b"\x01" * (BOOT_LEN + 1)))
        self.assertEqual(r.returncode, 1)
        self.assertUnchanged()

    def test_env_strategy_is_mandatory(self):
        r = self.sb.run(env_mode=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("choose an env strategy", r.stderr)
        self.assertUnchanged()

    def test_env_options_are_exclusive(self):
        r = self.sb.run(args=["--env-erase"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not both", r.stderr)

    def test_env_image_must_be_64k(self):
        self.sb.env = self.sb.env[:-1]
        self.sb.env_path = self.sb._put("env.img", self.sb.env)
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("64 KiB", r.stderr)
        self.assertUnchanged()

    def test_existing_lock_refuses_second_run(self):
        os.makedirs(os.path.join(self.sb.stage, ".lock"))
        r = self.sb.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("another installer run", r.stderr)
        self.assertUnchanged()

    def test_write_flag_without_phrase_refused_before_device_access(self):
        r = self.sb.run(sim=False, args=["--allow-flash-write"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("phrase missing", r.stderr)

    # ---- dry run and reboot gating -----------------------------------------
    def test_default_is_dry_run_no_mutation(self):
        r = self.sb.run(sim=False, extra_env={"MTD_DEV_DIR": self.sb.dev})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MODE: dry run", r.stdout)
        self.assertIn("would write", r.stdout)
        self.assertIn("nothing was written", r.stdout)
        self.assertUnchanged()

    def test_reboot_never_in_simulation(self):
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_REBOOT_CONFIRM": REBOOT,
                                   "OPENIPC_MIGRATE_CONFIRM": CONFIRM},
                        args=["--allow-reboot", "--allow-flash-write"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Reboot withheld", r.stdout)
        self.assertIn("file-backed simulation", r.stdout)

    # ---- hooks --------------------------------------------------------------
    def test_hook_order_on_success(self):
        hook = self.sb.write_hook()
        r = self.sb.run(args=["--hook", hook])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        verbs = self.sb.hook_verbs()
        self.assertEqual(verbs[0], "quiesce")
        self.assertTrue(verbs[1].startswith("export ") and verbs[1].endswith("/private"))
        self.assertEqual(verbs[2], "progress writing-start")
        self.assertIn("progress region-begin-bootloader", verbs)
        self.assertIn("progress verified", verbs)
        self.assertNotIn("resume", verbs)
        self.assertMigrated()

    def test_quiesce_failure_aborts_cleanly_and_resumes(self):
        hook = self.sb.write_hook(fail_verb="quiesce")
        r = self.sb.run(args=["--hook", hook])
        self.assertEqual(r.returncode, 1)
        self.assertIn("resume", self.sb.hook_verbs())
        self.assertUnchanged()

    def test_export_failure_aborts_cleanly_and_resumes(self):
        hook = self.sb.write_hook(fail_verb="export")
        r = self.sb.run(args=["--hook", hook])
        self.assertEqual(r.returncode, 1)
        verbs = self.sb.hook_verbs()
        self.assertIn("resume", verbs)
        self.assertNotIn("progress writing-start", verbs)
        self.assertUnchanged()

    def test_partial_state_reports_and_does_not_resume(self):
        hook = self.sb.write_hook()
        r = self.sb.run(extra_env={"OPENIPC_MIGRATE_SELFTEST_FAULT": "short_write_firmware_always"},
                        args=["--hook", hook])
        self.assertEqual(r.returncode, 2)
        verbs = self.sb.hook_verbs()
        self.assertIn("progress abort-partial", verbs)
        self.assertNotIn("resume", verbs)

    def test_dry_run_with_hook_rehearses_and_resumes(self):
        hook = self.sb.write_hook()
        r = self.sb.run(sim=False, extra_env={"MTD_DEV_DIR": self.sb.dev}, args=["--hook", hook])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        verbs = self.sb.hook_verbs()
        self.assertEqual(verbs[0], "quiesce")
        self.assertTrue(verbs[1].startswith("export "))
        self.assertEqual(verbs[-1], "resume")
        self.assertUnchanged()


if __name__ == "__main__":
    unittest.main()
