"""RAMFS/tiny-shmem regression: real stock shell + awk, no device access."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import STAGE2, private_tmp, rmtree
from sb_fixture import BUSYBOX

STOCK = os.environ.get("OM_E2E_STOCK_BB") or BUSYBOX
MEM = "MemFree: 8000 kB\nBuffers: 440 kB\nCached: 9000 kB\nShmem: 0 kB\nUnevictable: 1000 kB\n"
DF = "Filesystem 1K-blocks Used Available Use% Mounted on\nnone 0 0 0 0% /dev\n"
STAT = ('  File: "/dev/om"\n    ID: 0 Namelen: 255 Type: romfs\nBlock size: 4096\n'
        'Blocks: Total: 0 Free: 0 Available: 0\nInodes: Total: 0 Free: 0\n')


@unittest.skipUnless(STOCK, "BusyBox required")
class Phase1Capacity(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-space")
        self.addCleanup(rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name in ("awk", "expr"):
            p = self.bin / name
            p.write_text(f"#!/bin/sh\nexec {shlex.quote(str(STOCK))} {name} \"$@\"\n")
            p.chmod(0o700)
        self.om_dir = self.tmp / "om"
        self.om_dir.mkdir()
        self.mounts = self.tmp / "mounts"
        p = self.bin / "awk"
        p.write_text(f"#!/bin/sh\n"
                     'if [ "$1" = "-v" ] && [ "${2%%=*}" = p ] && [ "$4" = /proc/mounts ]; then\n'
                     f'  exec {shlex.quote(str(STOCK))} awk "$1" "$2" "$3" {shlex.quote(str(self.mounts))}\n'
                     f'fi\nexec {shlex.quote(str(STOCK))} awk "$@"\n')
        p.chmod(0o700)
        src = STAGE2.read_text("ascii")
        self.funcs = src.split("# --- phase1 begin", 1)[1].split("p1_get(){", 1)[1]
        self.funcs = "p1_get(){" + self.funcs.split("phase1(){", 1)[0]

    def run_guard(self, tail, mem=MEM, df=DF, stat=STAT, df_rc=0, stat_rc=0, mounts=None, om_dir=None):
        self.mounts.write_text(mounts if mounts is not None else f"none {self.tmp} tmpfs rw 0 0\n")
        for name, text, rc in (("df", df, df_rc), ("stat", stat, stat_rc)):
            data = self.tmp / (name + ".out")
            data.write_text(text)
            p = self.bin / name
            p.write_text(f"#!/bin/sh\n{shlex.quote(str(STOCK))} cat {shlex.quote(str(data))}\nexit {rc}\n")
            p.chmod(0o700)
        script = (f"OM_DIR={shlex.quote(str(om_dir or self.om_dir))}; OM_MIN_AVAIL_KB=12288; OM_MIN_DIRFREE_KB=10240\n" + self.funcs + "\n" + tail)
        return subprocess.run([STOCK, "sh", "-c", script], input=mem, text=True, capture_output=True,
                              env={"PATH": str(self.bin)}, timeout=30)

    def capacity(self, **kw):
        r = self.run_guard('p1_capacity; r=$?; echo "$avail $dirfree"; exit "$r"', **kw)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        return r

    def test_tiny_shmem_ramfs_zero_is_not_disk_full(self):
        r = self.capacity()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "16440 14392")

    def test_stock_romfs_alias_accepts_both_ram_mount_names(self):
        for kind in ("ramfs", "tmpfs"):
            with self.subTest(kind=kind):
                r = self.capacity(mounts=f"none {self.om_dir} {kind} rw,nosuid 0 0\n")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), "16440 14392")

    def test_correct_ramfs_label_also_requires_mount_proof(self):
        stat = STAT.replace("romfs", "ramfs")
        self.assertEqual(self.capacity(stat=stat).returncode, 0)
        self.assertEqual(self.capacity(stat=stat, mounts="").returncode, 1)

    def test_real_romfs_and_other_mount_types_are_rejected(self):
        for kind in ("romfs", "squashfs", "ext4", "rootfs", "unknown"):
            with self.subTest(kind=kind):
                self.assertEqual(self.capacity(mounts=f"none {self.om_dir} {kind} rw 0 0\n").returncode, 1)

    def test_read_only_or_missing_rw_mounts_are_rejected(self):
        for opts in ("ro", "ro,nosuid", "rw,ro", "xrw", "rwx", "nosuid", ""):
            with self.subTest(opts=opts):
                self.assertEqual(self.capacity(mounts=f"none {self.om_dir} ramfs {opts} 0 0\n").returncode, 1)

    def test_longest_mount_wins_over_writable_ram_parent(self):
        outer = f"none / ramfs rw 0 0\nnone {self.tmp} tmpfs rw 0 0\n"
        for kind, opts in (("tmpfs", "ro"), ("ext4", "rw"), ("romfs", "ro")):
            with self.subTest(kind=kind, opts=opts):
                self.assertEqual(self.capacity(mounts=outer + f"none {self.om_dir} {kind} {opts} 0 0\n").returncode, 1)
        self.assertEqual(self.capacity(mounts=f"root / rootfs ro 0 0\nnone {self.om_dir} tmpfs rw 0 0\n").returncode, 0)

    def test_mountpoint_matching_requires_directory_boundary(self):
        for point in (str(self.om_dir) + "-other", str(self.tmp)[:-1]):
            with self.subTest(point=point):
                mounts = f"root / ext4 rw 0 0\nnone {point} ramfs rw 0 0\n"
                self.assertEqual(self.capacity(mounts=mounts).returncode, 1)

    def test_missing_malformed_or_ambiguous_mounts_fail_closed(self):
        valid = f"none {self.om_dir} ramfs rw 0 0\n"
        for mounts in ("", "bad\n", valid + valid, valid.replace(" 0 0", " 0"),
                       valid.replace(str(self.om_dir), "relative"), valid + "malformed\n"):
            with self.subTest(mounts=mounts):
                self.assertEqual(self.capacity(mounts=mounts).returncode, 1)

    def test_realpath_normalization_handles_symlink_and_dot_dot(self):
        link = self.tmp / "alias"
        link.symlink_to(self.om_dir, target_is_directory=True)
        mounts = f"none {self.om_dir} ramfs rw 0 0\n"
        for path in (link, str(self.om_dir) + "/../om"):
            with self.subTest(path=path):
                self.assertEqual(self.capacity(mounts=mounts, om_dir=path).returncode, 0)

    def test_escaped_paths_fail_closed(self):
        for name in ("space dir", "tab\tdir", "line\ndir", "back\\slash"):
            work = self.tmp / name
            work.mkdir()
            with self.subTest(name=name):
                self.assertEqual(self.capacity(om_dir=work, mounts="none / ramfs rw 0 0\n").returncode, 1)

    def test_missing_work_directory_is_rejected(self):
        self.assertEqual(self.capacity(om_dir=self.tmp / "absent").returncode, 1)

    def test_normal_accounted_filesystem_keeps_df_limit(self):
        r = self.capacity(df=DF.replace("none 0 0 0", "disk 20000 5000 15000"), stat="unparseable")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "16440 15000")

    def test_full_bounded_tmpfs_is_rejected(self):
        self.assertEqual(self.capacity(stat=STAT.replace("romfs", "tmpfs").replace("Total: 0", "Total: 1024")).returncode, 1)

    def test_zero_accounting_other_types_are_rejected(self):
        for kind in ("tmpfs", "ext4", "unknown", "0x858458f6"):
            with self.subTest(kind=kind):
                self.assertEqual(self.capacity(stat=STAT.replace("romfs", kind)).returncode, 1)

    def test_ramfs_requires_all_block_counts_zero(self):
        for field in ("Total", "Free", "Available"):
            with self.subTest(field=field):
                self.assertEqual(self.capacity(stat=STAT.replace(field + ": 0", field + ": 1")).returncode, 1)

    def test_ramfs_reserves_two_mib_beyond_work_budget(self):
        r = self.run_guard('OM_MIN_AVAIL_KB=1; p1_capacity', mem=MEM.replace("8000", "3000"))
        self.assertEqual(r.returncode, 1)  # 11440 available, but only 9392 for work

    def test_ram_minimum_still_applies(self):
        r = self.run_guard('OM_MIN_DIRFREE_KB=1; p1_capacity', mem=MEM.replace("8000", "3000"))
        self.assertEqual(r.returncode, 1)

    def test_shmem_and_unevictable_are_not_reclaimable(self):
        r = self.run_guard('p1_mem_avail', mem=MEM.replace("Shmem: 0", "Shmem: 2000"))
        self.assertEqual(r.stdout.strip(), "14440")
        r = self.capacity(mem=MEM.replace("Unevictable: 1000", "Unevictable: 12000"))
        self.assertEqual(r.returncode, 1)

    def test_missing_accounting_uses_only_free_and_buffers(self):
        for field in ("Shmem", "Unevictable"):
            with self.subTest(field=field):
                mem = "\n".join(l for l in MEM.splitlines() if not l.startswith(field + ":")) + "\n"
                r = self.run_guard('p1_mem_avail', mem=mem)
                self.assertEqual(r.stdout.strip(), "8440")
                self.assertEqual(self.capacity(mem=mem).returncode, 1)

    def test_malformed_or_missing_memory_fields_fail_closed(self):
        for mem in ("", MEM.replace("8000", "bad"), MEM.replace("MemFree:", "Absent:"), MEM + "MemFree: 99999 kB\n"):
            with self.subTest(mem=mem):
                r = self.capacity(mem=mem)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout.strip(), "0 0")

    def test_stat_failure_or_partial_output_does_not_bypass_guard(self):
        for stat, rc in ((STAT, 1), ("", 0), (STAT.replace("Available: 0", "Available: bad"), 0), (STAT + STAT, 0)):
            with self.subTest(rc=rc, stat=stat):
                self.assertEqual(self.capacity(stat=stat, stat_rc=rc).returncode, 1)

    def test_df_failure_or_malformed_output_does_not_bypass_guard(self):
        for df, rc in ((DF, 1), ("", 0), (DF.replace("none 0 0 0", "none 0 0 bad"), 0)):
            with self.subTest(df=df, rc=rc):
                self.assertEqual(self.capacity(df=df, df_rc=rc).returncode, 1)

    def test_real_stat_and_stock_awk_accept_normal_accounted_filesystem(self):
        # Call stat directly to check the stock applet supports its default -f output.
        r = subprocess.run([STOCK, "stat", "-f", str(self.tmp)], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Blocks: Total:", r.stdout)
        self.assertIn("Type:", r.stdout)


if __name__ == "__main__":
    unittest.main()
