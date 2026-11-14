#!/usr/bin/env python3
"""Offline tests for build_migration_appfs.py (synthetic inputs only; no camera, no private files).

The class ToolChain additionally runs the real mksquashfs/unsquashfs when they are
available (PATH, or OM_MKSQUASHFS / OM_UNSQUASHFS pointing at wrappers).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import (INSTALLER, ROOT, STAGE2, bma, make_bundle, private_tmp, rbytes, rmtree, sm,
                        synthetic_files)
from sb_fixture import ff, oenv, uimage

import make_env  # noqa: E402  (om_testlib put the tool directory on sys.path)
import stock_layout as sl  # noqa: E402

BUSYBOX = shutil.which("busybox")
MKSQ = os.environ.get("OM_MKSQUASHFS") or shutil.which("mksquashfs")
UNSQ = os.environ.get("OM_UNSQUASHFS") or shutil.which("unsquashfs")
SYSCTL = b"/proc/sys/net/ipv4/tcp_slow_start_after_idle"
# Stand-in for the stock runapp.sh: the real one ends with its only `exit 0` and has exactly one
# tcp_slow_start_after_idle line, which anchors the trial branch.
RUNAPP = (b"#!/bin/sh\n# stand-in for the stock runapp.sh\necho stock-app-prep\necho 0 > " + SYSCTL +
          b"\necho stock-app-start\nexit 0\n")
LEGACY_MTDPARTS = "hi_sfc:256k(boot),64k(env),7168k@0x50000(firmware),-(rootfs_data)"
WLAN = [("wlandev", "mt7601u-hi3518ev100-camhi"), ("wlanssid", "dummy-ssid-for-test"),
        ("wlanpass", "dummy-pass-for-test")]
STOCK_SH = os.environ.get("OM_E2E_STOCK_BB")  # validate_online_qemu.py: launcher of the stock BusyBox 1.18.4
SHELLS = [("sh",)] + ([(BUSYBOX, "sh")] if BUSYBOX else []) + ([(STOCK_SH, "sh")] if STOCK_SH else [])


def make_elf(*, interp=bma.OLD_INTERP, interp_room=22, needed=("libc.so",), e_type=3, machine=40,
             flags=0x05000200, applets=bma.RUNTIME_APPLETS, elf_class=1) -> bytes:
    """A minimal 32-bit little-endian ELF: PT_LOAD (whole file), [PT_INTERP], PT_DYNAMIC, applet table."""
    phnum = 2 + (interp is not None)
    interp_off = 52 + 32 * phnum
    interp_blob = b"" if interp is None else interp + b"\0" * (interp_room - len(interp))
    strtab_off = interp_off + len(interp_blob)
    strtab, name_offs = b"\0", []
    for name in needed:
        name_offs.append(len(strtab))
        strtab += name.encode() + b"\0"
    dyn_off = (strtab_off + len(strtab) + 3) & ~3
    dyn = b"".join(struct.pack("<iI", bma.DT_NEEDED, o) for o in name_offs)
    dyn += struct.pack("<iI", bma.DT_STRTAB, strtab_off) + struct.pack("<iI", bma.DT_NULL, 0)
    table_off = dyn_off + len(dyn)
    table = b"\0" + b"\0".join(a.encode() for a in applets) + b"\0"
    total = table_off + len(table)
    phdrs = [struct.pack("<8I", bma.PT_LOAD, 0, 0, 0, total, total, 5, 0x1000)]
    if interp is not None:
        phdrs.append(struct.pack("<8I", bma.PT_INTERP, interp_off, interp_off, interp_off,
                                 len(interp_blob), len(interp_blob), 4, 1))
    phdrs.append(struct.pack("<8I", bma.PT_DYNAMIC, dyn_off, dyn_off, dyn_off, len(dyn), len(dyn), 6, 4))
    ident = b"\x7fELF" + bytes([elf_class, 1, 1, 0]) + b"\0" * 8
    head = ident + struct.pack("<HHIIIIIHHHHHH", e_type, machine, 1, 0, 52, 0, flags, 52, 32, phnum, 40, 0, 0)
    out = bytearray(total)
    out[:len(head) + 32 * phnum] = head + b"".join(phdrs)
    out[interp_off:interp_off + len(interp_blob)] = interp_blob
    out[strtab_off:strtab_off + len(strtab)] = strtab
    out[dyn_off:dyn_off + len(dyn)] = dyn
    out[table_off:] = table
    return bytes(out)


def libc_elf() -> bytes:
    return make_elf(interp=None, needed=(), applets=())


def run_sh(script: Path, shell=("sh",)):
    return subprocess.run([*shell, "-n", str(script)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=30)


def wait_for(path: Path, seconds: float = 15) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return path.exists()


def gate(tmp: Path) -> str:
    """Stands in for the launcher's start delay: note that the launcher runs, then wait (at most
    about 60 s, so that no stray process outlives a failed test for long) until the test opens the gate."""
    return (f": > {tmp / 'parked'}; for i in {' '.join(['1'] * 60)}; do [ -e {tmp / 'go'} ] && break; "
            "sleep 1; done; : ")


def runnable(out: bytes, tmp: Path, mark: Path, mount_ok: bool = True) -> bytes:
    """The patched runapp.sh with every system/device path redirected into tmp; it can never reboot."""
    subs = [(SYSCTL, tmp / "sysctl"), (b"/proc/cmdline", tmp / "cmdline"), (b"/proc/mtd", tmp / "mtd"),
            (b"/dev/omt", tmp / "omt"), (b"mknod ", "true "),
            (b"mount -t squashfs", "true -t squashfs" if mount_ok else "false -t squashfs"),
            (b"/bin/busybox sleep 900; /bin/busybox reboot -f", f"sleep 1; echo armed > {tmp / 'timer'}"),
            (b"/bin/busybox reboot -f", "echo REBOOT; exit 7"), (b"/bin/busybox sleep ", gate(tmp)),
            (b"/bin/sh /app/openipc-migrate/stage2.sh phase1", f"echo started > {mark}")]
    for old, new in subs:
        if old not in out:
            raise AssertionError(f"patched runapp.sh lacks {old!r}")
        out = out.replace(old, str(new).encode())
    if re.search(rb"/bin/busybox|/proc/|/dev/omt|reboot -f", out):
        raise AssertionError("a system path survived the test substitution")
    return out


class ElfHandling(unittest.TestCase):
    def test_info_reports_interp_needed_type_and_flags(self):
        info = bma.elf_info(make_elf())
        self.assertEqual((info["type"], info["flags"], info["interp"], info["needed"]),
                         (bma.ET_DYN, 0x05000200, bma.OLD_INTERP, ["libc.so"]))
        self.assertIsNone(bma.elf_info(libc_elf())["interp"])

    def test_patch_interp_only_touches_the_interp_string(self):
        data = make_elf()
        out = bma.patch_interp(data)
        self.assertEqual(len(out), len(data))
        self.assertEqual(bma.elf_info(out)["interp"], bma.NEW_INTERP)
        off, size = bma.elf_info(data)["interp_span"]
        self.assertEqual(out[:off] + out[off + size:], data[:off] + data[off + size:])
        self.assertEqual(out[off:off + size], bma.NEW_INTERP + b"\0" * (size - len(bma.NEW_INTERP)))
        self.assertEqual(bma.NEW_INTERP, b"/dev/om/ld-musl.so")

    def test_patch_interp_rejections(self):
        with self.assertRaisesRegex(bma.BuildError, "expected musl loader"):
            bma.patch_interp(make_elf(interp=b"/lib/ld-uClibc.so.0"))
        with self.assertRaisesRegex(bma.BuildError, "does not fit"):
            bma.patch_interp(make_elf(), new=b"/dev/om/" + b"x" * 20)
        with self.assertRaisesRegex(bma.BuildError, "32-bit little-endian"):
            bma.patch_interp(b"#!/bin/sh\n" + b"\0" * 60)
        with self.assertRaisesRegex(bma.BuildError, "32-bit little-endian"):
            bma.patch_interp(make_elf(elf_class=2))
        with self.assertRaisesRegex(bma.BuildError, "not ARM"):
            bma.patch_interp(make_elf(machine=3))
        with self.assertRaisesRegex(bma.BuildError, "PT_INTERP is malformed"):
            bma.elf_info(make_elf(interp_room=len(bma.OLD_INTERP)))
        broken = bytearray(make_elf())
        struct.pack_into("<I", broken, 52 + 16, len(broken) + 100)  # PT_LOAD p_filesz beyond the file
        with self.assertRaisesRegex(bma.BuildError, "exceeds the file"):
            bma.elf_info(bytes(broken))

    def test_check_runtime_accepts_only_the_expected_shape(self):
        info = bma.check_runtime(make_elf(), libc_elf())
        self.assertEqual(info, {"busybox_needed": ["libc.so"], "eabi_flags": "0x05000200",
                                "applets_checked": len(bma.RUNTIME_APPLETS)})
        missing = tuple(a for a in bma.RUNTIME_APPLETS if a != "timeout")
        with self.assertRaisesRegex(bma.BuildError, "lacks applets: timeout"):
            bma.check_runtime(make_elf(applets=missing), libc_elf())
        with self.assertRaisesRegex(bma.BuildError, "only libc.so"):
            bma.check_runtime(make_elf(needed=("libc.so", "libm.so")), libc_elf())
        with self.assertRaisesRegex(bma.BuildError, "only libc.so"):
            bma.check_runtime(make_elf(e_type=2), libc_elf())
        with self.assertRaisesRegex(bma.BuildError, "self-contained"):
            bma.check_runtime(make_elf(), make_elf(applets=()))
        with self.assertRaisesRegex(bma.BuildError, "EABI5"):
            bma.check_runtime(make_elf(flags=0x04000000), libc_elf())
        self.assertEqual(bma.check_applets(b"\0sh\0awk\0", ("sh", "awk", "wc")), ["wc"])


class ConfRendering(unittest.TestCase):
    def setUp(self):
        self.files = synthetic_files()
        self.stage2 = STAGE2.read_bytes()
        self.run_id = "0123456789abcdef0123456789abcdef"

    def values(self, **over):
        return bma.conf_values("192.0.2.10", 8608, self.run_id, self.files, self.stage2, over or None)

    def test_round_trip_and_pins(self):
        values = self.values()
        text = bma.render_conf(values)
        self.assertEqual(sm.parse_conf(text), values)
        pins = {"OM_RT_BB_SHA256": "runtime/busybox", "OM_RT_LD_SHA256": "runtime/ld-musl.so",
                "OM_INSTALLER_SHA256": "runtime/om_stockboot.sh", "OM_HOOK_SHA256": "runtime/migrate_hook.sh",
                "OM_FW_SHA256": "payload/firmware.bin", "OM_OENV_SHA256": "payload/env-openipc.img"}
        for key, rel in pins.items():
            self.assertEqual(values[key], bma.sha256(self.files[rel]), key)
        self.assertEqual(values["OM_STAGE2_SHA256"], bma.sha256(self.stage2))
        self.assertEqual((values["OM_DIR"], values["OM_APP_DIR"], values["OM_IDLE_MAX"]),
                         (bma.OM_DIR, bma.OM_APP_DIR, "180"))
        self.assertEqual(set(values), set(bma.CONF_KEYS))
        self.assertFalse([k for k in values if k.startswith("OM_ENV")])  # no U-Boot env image/erase any more
        self.assertEqual(self.values(OM_POLL_MAX=7)["OM_POLL_MAX"], "7")

    def test_a_posix_shell_reads_back_every_value(self):
        tmp = private_tmp("om-conf")
        try:
            conf = tmp / "migrate.conf"
            values = self.values(OM_POLL_MAX=7)
            conf.write_text(bma.render_conf(values))
            keys = sorted(values)
            script = ". \"$1\"; " + "; ".join(f'printf "%s=%s\\n" {k} "${k}"' for k in keys)
            for shell in SHELLS:
                r = subprocess.run([*shell, "-c", script, "sh", str(conf)], capture_output=True, text=True,
                                   timeout=30, stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(dict(line.split("=", 1) for line in r.stdout.splitlines()), values, shell)
        finally:
            rmtree(tmp)

    def test_rejections(self):
        bad = {
            "unknown": dict(self.values(), OM_EXTRA="1"),
            "missing": {k: v for k, v in self.values().items() if k != "OM_HOLD_MAX"},
            "missing pin": {k: v for k, v in self.values().items() if k != "OM_OENV_SHA256"},
            "test key": dict(self.values(), OM_SIM_DIR="/tmp/x"),
            "fault seam": dict(self.values(), OM_SIM_FAULT="after-erase"),
            "metachar": self.values(OM_HOST="192.0.2.10;reboot"),
            "quote": self.values(OM_HOST="192.0.2.10'x"),
            "subst": self.values(OM_LOCK="/dev/$(reboot)"),
            "number": self.values(OM_POLL_INTERVAL="20s"),
            "idle": self.values(OM_IDLE_MAX="3m"),
            "zero interval": self.values(OM_POLL_INTERVAL=0),
            "port": bma.conf_values("192.0.2.10", 70000, self.run_id, self.files, self.stage2),
            "relative": self.values(OM_LOCK="dev/lock"),
            "dotdot": self.values(OM_STOCK_BB="/bin/../tmp/busybox"),
            "digest": self.values(OM_RT_BB_SHA1="0" * 39),
            "oenv digest": self.values(OM_OENV_SHA256="0" * 63),
            "stage2 digest": self.values(OM_STAGE2_SHA256="X" * 64),
            "run id": bma.conf_values("192.0.2.10", 8608, "ABCDEF0123456789", self.files, self.stage2),
            "short run id": bma.conf_values("192.0.2.10", 8608, "abc123", self.files, self.stage2),
            "om dir": self.values(OM_DIR="/tmp/om"),
            "app dir": self.values(OM_APP_DIR="/tmp/app"),
        }
        for label, values in bad.items():
            with self.assertRaises(bma.BuildError, msg=label):
                bma.render_conf(values)
        no_oenv = {k: v for k, v in self.files.items() if k != "payload/env-openipc.img"}
        for files in (no_oenv, dict(self.files, **{"payload/uboot.bin": b"x"})):
            with self.assertRaisesRegex(bma.BuildError, "served file set"):
                bma.conf_values("192.0.2.10", 8608, self.run_id, files, self.stage2)

    def test_test_only_keys_need_the_explicit_switch(self):
        values = self.values(OM_DIR="/dev/shm/om-x", OM_SIM_DIR="/dev/shm/sim", OM_SIM_FAULT="after-erase",
                             OM_WATCHDOG_DEV="/dev/shm/no")
        text = bma.render_conf(values, allow_test_keys=True)
        self.assertIn("OM_SIM_DIR='/dev/shm/sim'", text)
        self.assertIn("OM_SIM_FAULT='after-erase'", text)
        self.assertEqual(sm.parse_conf(text)["OM_DIR"], "/dev/shm/om-x")
        self.assertTrue(text.rstrip().endswith("OM_WATCHDOG_DEV='/dev/shm/no'"))


class RunappAndTree(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-tree")
        self.pin = mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP))

    def tearDown(self):
        rmtree(self.tmp)

    def test_trial_branch_and_launcher_are_the_only_additions(self):
        with self.pin:
            out = bma.patch_runapp(RUNAPP, 45)
        cut = RUNAPP.index(bma.TRIAL_NEEDLE)
        want = (RUNAPP[:cut] + bma.TRIAL_BRANCH.encode() + RUNAPP[cut:-len(bma.RUNAPP_NEEDLE)] +
                bma.LAUNCHER.format(delay=45).encode())
        self.assertEqual(out, want)
        self.assertTrue(out.endswith(b"\nexit 0\n"))
        self.assertEqual(out.count(b"exit 0"), 1)
        self.assertLess(out.index(b"omtrial=1"), out.index(SYSCTL))  # before the stock app starts
        self.assertIn(b"/bin/busybox sleep 45\n", out)
        self.assertIn(b"/bin/sh /app/openipc-migrate/stage2.sh phase1\n", out)
        self.assertIn(b") </dev/null >/dev/null 2>&1 &\n", out)
        self.assertIn(f"grep -q '^{bma.TP_MTD_LINE}$' /proc/mtd".encode(), out)
        self.assertEqual(bma.TP_MTD_LINE, 'mtd6: 000b0000 00010000 "tp"')  # 704 KiB trial payload
        self.assertIn(b"( /bin/busybox sleep 900; /bin/busybox reboot -f )", out)
        script = self.tmp / "runapp.sh"
        script.write_bytes(out)
        for shell in SHELLS:
            r = run_sh(script, shell)
            self.assertEqual(r.returncode, 0, r.stderr)

    def run_patched(self, cmdline: str, mtd: str, *, mount_ok: bool = True, delay: int = 1, shell=("sh",)):
        tmp = private_tmp("om-runapp")
        self.addCleanup(rmtree, tmp)
        (tmp / "cmdline").write_text(cmdline + "\n")
        (tmp / "mtd").write_text(mtd)
        (tmp / "omt" / "p").mkdir(parents=True)
        (tmp / "omt" / "p" / "trial.sh").write_text("echo TRIAL-RUNNING\nexit 0\n")
        with self.pin:
            out = bma.patch_runapp(RUNAPP, delay)
        script = tmp / "runapp.sh"
        script.write_bytes(runnable(out, tmp, tmp / "agent-started", mount_ok))
        # Plain files, not pipes: a background child keeps an inherited pipe open under some shells
        # (e.g. Debian's BusyBox), which would hold up subprocess.run until the child exits.
        with open(tmp / "stdout", "wb") as so, open(tmp / "stderr", "wb") as se:
            rc = subprocess.run([*shell, str(script)], stdout=so, stderr=se, timeout=30,
                                stdin=subprocess.DEVNULL).returncode
        r = subprocess.CompletedProcess(shell, rc, (tmp / "stdout").read_text(), (tmp / "stderr").read_text())
        return r, tmp

    def test_normal_boot_runs_the_stock_app_and_starts_the_agent_later(self):
        mtd = 'dev:    size   erasesize  name\nmtd0: 00200000 00010000 "boot"\n'
        for shell in SHELLS:
            r, tmp = self.run_patched("mem=42M console=ttyAMA0,115200 root=1f01", mtd, delay=45, shell=shell)
            self.assertEqual((r.returncode, r.stdout), (0, "stock-app-prep\nstock-app-start\n"), (shell, r.stderr))
            self.assertEqual((tmp / "sysctl").read_text(), "0\n")
            # runapp.sh has exited (subprocess.run would have timed out otherwise) while the launcher
            # waits behind the closed gate: the boot is not held up by the agent, which starts later.
            self.assertTrue(wait_for(tmp / "parked", 20), shell)
            self.assertFalse((tmp / "agent-started").exists())
            (tmp / "go").touch()
            self.assertTrue(wait_for(tmp / "agent-started", 30), shell)
            self.assertEqual((tmp / "agent-started").read_text(), "started\n")
            self.assertFalse((tmp / "timer").exists())  # no trial timer outside the trial boot

    def test_trial_boot_without_the_payload_reboots_at_once(self):
        tp_wrong = 'mtd6: 000a0000 00010000 "tp"\n'
        cases = {"no tp": 'mtd5: 00280000 00010000 "bakfs"\n', "wrong size": tp_wrong,
                 "mount fails": bma.TP_MTD_LINE + "\n"}
        for shell in SHELLS:
            for label, mtd in cases.items():
                r, tmp = self.run_patched("mem=42M root=1f01 mtdparts=hi_sfc:x " + sl.TRIAL_MARK, mtd,
                                             mount_ok=label != "mount fails", shell=shell)
                self.assertEqual((r.returncode, r.stdout), (7, "stock-app-prep\nREBOOT\n"), (shell, label, r.stderr))
                self.assertFalse((tmp / "sysctl").exists(), label)
                self.assertTrue(wait_for(tmp / "timer", 10), label)  # the 900 s timer was armed first
                # The timer slept 1 s before it wrote: a launcher started alongside would have shown up.
                self.assertFalse((tmp / "parked").exists(), label)
                self.assertFalse((tmp / "agent-started").exists(), label)

    def test_trial_boot_execs_the_trial_script_from_the_payload(self):
        mtd = 'dev:    size   erasesize  name\nmtd5: 001d0000 00010000 "tk"\n' + bma.TP_MTD_LINE + "\n"
        for shell in SHELLS:
            r, tmp = self.run_patched("mem=42M root=1f01 " + sl.TRIAL_MARK, mtd, shell=shell)
            self.assertEqual((r.returncode, r.stdout), (0, "stock-app-prep\nTRIAL-RUNNING\n"), (shell, r.stderr))
            self.assertFalse((tmp / "sysctl").exists())
            self.assertTrue(wait_for(tmp / "timer", 10))
            self.assertFalse((tmp / "parked").exists())
            self.assertFalse((tmp / "agent-started").exists())
        # A cmdline that merely mentions the mark without the leading space is not a trial boot.
        r, tmp = self.run_patched("x" + sl.TRIAL_MARK, mtd)
        self.assertEqual(r.stdout, "stock-app-prep\nstock-app-start\n")
        (tmp / "go").touch()
        self.assertTrue(wait_for(tmp / "agent-started", 30))

    def test_runapp_rejections(self):
        with self.assertRaisesRegex(bma.BuildError, "pinned stock script"):
            bma.patch_runapp(RUNAPP, 45)
        for data in (RUNAPP + b"\n", RUNAPP.replace(b"echo stock-app-start", b"exit 0\necho stock-app-start")):
            with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(data)):
                with self.assertRaisesRegex(bma.BuildError, "only `exit 0`"):
                    bma.patch_runapp(data, 45)
        no_anchor = RUNAPP.replace(bma.TRIAL_NEEDLE, b"\n")
        twice = RUNAPP.replace(b"echo stock-app-start\n", b"echo mid" + bma.TRIAL_NEEDLE + b"echo stock-app-start\n")
        for data in (no_anchor, twice):
            with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(data)):
                with self.assertRaisesRegex(bma.BuildError, "tcp_slow_start_after_idle"):
                    bma.patch_runapp(data, 45)
        with self.pin:
            for delay in (-1, 3601):
                with self.assertRaisesRegex(bma.BuildError, "start delay"):
                    bma.patch_runapp(RUNAPP, delay)
        arith = RUNAPP.replace(b"echo stock-app-prep", b"x=$((1+1))")
        with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(arith)):
            with self.assertRaisesRegex(bma.BuildError, "cannot even parse"):
                bma.patch_runapp(arith, 45)

    def make_stock_tree(self, root: Path) -> Path:
        root.mkdir()
        (root / "runapp.sh").write_bytes(RUNAPP)
        os.chmod(root / "runapp.sh", 0o744)
        (root / "cmsMain.out").write_bytes(rbytes(1, 3000))
        os.chmod(root / "cmsMain.out", 0o755)
        (root / "etc").mkdir()
        (root / "etc" / "conf.ini").write_bytes(b"[x]\n")
        os.chmod(root / "etc" / "conf.ini", 0o600)
        os.symlink("etc", root / "lib")
        os.chmod(root, 0o755)
        return root

    def test_prepare_tree_adds_exactly_the_agent(self):
        base = self.make_stock_tree(self.tmp / "base")
        before = bma.snapshot(base)
        with self.pin:
            changed = bma.prepare_tree(base, self.tmp / "src", b"#!/bin/sh\n", b"OM_X='1'\n", 45)
        self.assertEqual(changed, bma.CHANGED_PATHS)
        self.assertEqual(bma.snapshot(base), before)
        src = self.tmp / "src"
        modes = {rel: stat.S_IMODE(os.lstat(src / rel).st_mode) for rel in bma.CHANGED_PATHS}
        self.assertEqual(modes, {"openipc-migrate": 0o755, "openipc-migrate/migrate.conf": 0o644,
                                 "openipc-migrate/stage2.sh": 0o755, "runapp.sh": 0o744})
        self.assertEqual(os.readlink(src / "lib"), "etc")
        self.assertEqual(stat.S_IMODE(os.stat(src / "etc" / "conf.ini").st_mode), 0o600)
        with self.pin:
            self.assertEqual((src / "runapp.sh").read_bytes(), bma.patch_runapp(RUNAPP, 45))

    def test_prepare_tree_refusals(self):
        base = self.make_stock_tree(self.tmp / "base")
        (base / bma.APP_DIR_NAME).mkdir()
        with self.pin, self.assertRaisesRegex(bma.BuildError, "already contains"):
            bma.prepare_tree(base, self.tmp / "src1", b"", b"", 45)
        base2 = self.make_stock_tree(self.tmp / "base2")
        (base2 / "runapp.sh").unlink()
        os.symlink("cmsMain.out", base2 / "runapp.sh")
        with self.pin, self.assertRaisesRegex(bma.BuildError, "no regular runapp.sh"):
            bma.prepare_tree(base2, self.tmp / "src2", b"", b"", 45)


class Listing(unittest.TestCase):
    TEXT = ("Parallel unsquashfs: Using 1 processor\n"
            "drwxr-xr-x 500/500                38 2024-01-01 00:00 squashfs-root\n"
            "-rw-r--r-- 500/500                 3 2024-01-01 00:00 squashfs-root/a b\n"
            "lrwxrwxrwx 500/500                 3 2024-01-01 00:00 squashfs-root/l -> a b\n"
            "crw-r----- 500/500             1,  3 2024-01-01 00:00 squashfs-root/null\n")

    def test_perm_bits(self):
        self.assertEqual(bma.perm_bits("rwxr-xr-x"), 0o755)
        self.assertEqual(bma.perm_bits("rwsr-sr-t"), 0o7755)
        self.assertEqual(bma.perm_bits("rwSr--r-T"), 0o5644)

    def test_parse_listing(self):
        out = bma.parse_listing(self.TEXT)
        # Device nodes do not parse (size "1,  3"), so an appfs containing one fails closed.
        self.assertEqual(set(out), {".", "a b", "l"})
        self.assertEqual(out["l"], {"type": "l", "mode": 0o777, "owner": "500/500", "size": "3", "target": "a b"})
        self.assertEqual(out["a b"]["mode"], 0o644)

    def test_tree_matches_listing(self):
        tmp = private_tmp("om-list")
        try:
            (tmp / "a b").write_bytes(b"abc")
            os.chmod(tmp / "a b", 0o644)
            os.symlink("a b", tmp / "l")
            os.chmod(tmp, 0o755)
            snap, listing = bma.snapshot(tmp), bma.parse_listing(self.TEXT)
            self.assertTrue(bma.tree_matches_listing(snap, listing))
            for rel, key, value in (("a b", "mode", 0o600), ("a b", "size", "4"), ("l", "target", "x")):
                changed = {k: dict(v) for k, v in listing.items()}
                changed[rel][key] = value
                self.assertFalse(bma.tree_matches_listing(snap, changed), (rel, key))
            extra = dict(listing, extra={"type": "-", "mode": 0o644, "size": "1", "target": None})
            self.assertFalse(bma.tree_matches_listing(snap, extra))
        finally:
            rmtree(tmp)


class ScriptChecks(unittest.TestCase):
    def setUp(self):
        self.s2, self.inst = STAGE2.read_bytes(), INSTALLER.read_bytes()

    def test_reviewed_scripts_pass(self):
        bma.check_scripts(self.s2, self.inst)
        self.assertEqual(set(bma.INSTALLER_PHRASES), {a for a, v in sm.ACTIONS.items() if v[0]})

    def test_phase1_must_stay_within_the_stock_busybox(self):
        cases = {
            "arithmetic": (b"p1_ge(){", b"x=$((1+1)); p1_ge(){"),
            "head": (b"2>/dev/null | awk '{print $1}'; }", b"2>/dev/null | head -n 1; }"),
            "sha256sum": (b"p1_sum sha1sum", b"p1_sum sha256sum"),
            "chmod": (b"  rm -f \"$1\"\n", b"  chmod 755 \"$2\"\n"),
            "dd conv=": (b"  rm -f \"$1\"\n", b"  dd if=/dev/zero of=\"$1\" bs=1 count=1 conv=notrunc\n"),
            "grep -x": (b"  rm -f \"$1\"\n", b"  grep -qx run \"$1\" && rm -f \"$1\"\n"),
        }
        for label, (old, new) in cases.items():
            self.assertEqual(self.s2.count(old), 1, label)
            with self.assertRaises(bma.BuildError, msg=label):
                bma.check_scripts(self.s2.replace(old, new), self.inst)
        # Runtime applets are fine in phase2, and anything is fine in comments.
        ok = self.s2.replace(b"phase2(){\n", b"phase2(){\n  \"$BB\" head -c 1 /dev/null\n"
                             b"  \"$BB\" dd if=/dev/zero of=/dev/null bs=1 count=1 conv=notrunc\n")
        ok = ok.replace(b"phase1(){\n", b"phase1(){\n  # sha256sum, head and $((...)) are not available here\n")
        bma.check_scripts(ok, self.inst)

    def test_stock_dd_takes_only_the_basic_operands(self):
        # The stock BusyBox 1.18.4 dd fails (rc 1) on conv=/ibs=/obs=/iflag=/oflag=: trial.sh once padded its
        # tl checkpoint with conv=sync and never wrote it on the camera's userland.
        for bad in ('dd if=a of=b bs=1 count=1 conv=notrunc\n', 'x=$(dd if=a ibs=512 2>/dev/null | md5sum)\n',
                    '  dd\tif=a of=b oflag=direct\n'):
            with self.assertRaisesRegex(bma.BuildError, "stock BusyBox dd only takes", msg=bad):
                bma.check_stock_sh("#!/bin/sh\n" + bad, "t.sh")
        bma.check_stock_sh("#!/bin/sh\n# no dd conv=notrunc on the stock BusyBox\n"
                           'dd if=a of=b bs=65536 count=2 skip=1 seek=3 2>/dev/null\n'
                           'dd if=a bs=61440 count=1 2>/dev/null 1<>b; x=$(echo conv=1)\n', "t.sh")
        for script in ("trial.sh", "trial_dhcp.sh"):
            bma.check_stock_sh((ROOT / "camera" / script).read_text("ascii"), script)

    def test_stock_grep_has_no_x(self):
        # "grep: invalid option -- x" from the stock BusyBox 1.18.4: trial.sh once matched the manifest with -qx
        # and would always have reported host=fail, which blocks final.
        for bad in ('if grep -qx "run=$ID" m; then :; fi\n', 'grep -x a b\n', 'x=$(cat m | grep -vxF a)\n'):
            with self.assertRaisesRegex(bma.BuildError, "-x to grep", msg=bad):
                bma.check_stock_sh("#!/bin/sh\n" + bad, "t.sh")
        bma.check_stock_sh('#!/bin/sh\n# grep -x is not available here\nif grep -q "^run=$ID\\$" m; then :; fi\n',
                           "t.sh")
        bma.check_stock_sh('#!/bin/sh\n' + bma.PHASE1_END + '\n"$BB" grep -qxF a b\n', "t.sh", upto=bma.PHASE1_END)

    def test_arithmetic_is_banned_in_the_whole_file(self):
        # The stock sh has no $((...)) support at all and parses phase2's functions as well.
        for old, new in ((b"phase2(){\n", b"phase2(){\n  y=$((1+1))\n"),
                         (b"\ncase \"$PHASE\" in\n", b"\nz=$((2+2))\ncase \"$PHASE\" in\n")):
            self.assertEqual(self.s2.count(old), 1, old)
            with self.assertRaisesRegex(bma.BuildError, "cannot even parse"):
                bma.check_scripts(self.s2.replace(old, new), self.inst)

    def test_phrases_and_marker(self):
        with self.assertRaisesRegex(bma.BuildError, "GO_PHRASE"):
            bma.check_scripts(self.s2.replace(sm.GO_PHRASE.encode(), b"yes-go"), self.inst)
        names = [n for pair in bma.INSTALLER_PHRASES.values() for n in pair if n]
        self.assertEqual(len(names), 7)
        for name in names:
            line = re.search(rb'\n%s="[^"\n]*"\n' % name.encode(), self.inst)
            self.assertIsNotNone(line, name)
            bad = self.inst.replace(line.group(0), b'\n%s="yes-something-else"\n' % name.encode(), 1)
            with self.assertRaisesRegex(bma.BuildError, f"installer {name} differs"):
                bma.check_scripts(self.s2, bad)
        with self.assertRaisesRegex(bma.BuildError, "marker"):
            bma.check_scripts(self.s2.replace(bma.PHASE1_END.encode(), b"# end"), self.inst)

    def test_host_validation(self):
        self.assertEqual(bma.validate_host("192.0.2.10"), "192.0.2.10")
        for host in ("127.0.0.1", "0.0.0.0", "224.0.0.1", "169.254.1.1", "240.0.0.1", "255.255.255.255",
                     "camera.local", "::1", "192.0.2.300", "192.0.2.10 "):
            with self.assertRaises(bma.BuildError, msg=host):
                bma.validate_host(host)


class Inputs(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-in")

    def tearDown(self):
        rmtree(self.tmp)

    def put(self, name, data, mode=0o600):
        p = self.tmp / name
        p.write_bytes(data)
        os.chmod(p, mode)
        return p

    def test_read_pinned(self):
        data = rbytes(2, 100)
        p = self.put("fw.bin", data)
        self.assertEqual(bma.read_pinned(p, 100, bma.sha256(data), "fw"), data)
        with self.assertRaisesRegex(bma.BuildError, "pinned size/SHA-256"):
            bma.read_pinned(p, 100, "0" * 64, "fw")
        with self.assertRaisesRegex(bma.BuildError, "at most 99 B"):
            bma.read_pinned(p, 99, bma.sha256(data), "fw")
        with self.assertRaisesRegex(bma.BuildError, "missing.bin: FileNotFoundError"):
            bma.read_input(self.tmp / "missing.bin", 10)
        with self.assertRaisesRegex(bma.BuildError, "not a regular file"):
            bma.read_input(self.tmp, 10)

    @staticmethod
    def env(entries) -> bytes:
        return sl.build_env(entries, sl.OIPC_ENV_LEN)

    def test_oenv_image_checks(self):
        good = self.put("env-openipc.img", oenv())
        self.assertEqual(bma.load_oenv_image(good), oenv())
        self.assertEqual(len(oenv()), make_env.ENV_SIZE)
        with self.assertRaisesRegex(bma.BuildError, "mode 0600"):
            bma.load_oenv_image(self.put("env-loose.img", oenv(), 0o640))
        corrupt = bytearray(oenv())
        corrupt[10] ^= 1
        with self.assertRaisesRegex(bma.BuildError, "env-openipc.img: env CRC mismatch"):
            bma.load_oenv_image(self.put("env-crc.img", bytes(corrupt)))
        with self.assertRaisesRegex(bma.BuildError, "at most"):
            bma.load_oenv_image(self.put("env-big.img", oenv() + b"\0"))
        with self.assertRaisesRegex(bma.BuildError, "exactly"):
            bma.load_oenv_image(self.put("env-small.img", oenv()[:-1]))
        final = [("bootcmd", sl.FINAL_BOOTCMD), ("mtdparts", sl.OIPC_ENV_LAYOUT["mtdparts"])]
        with self.assertRaisesRegex(bma.BuildError, "wlandev/wlanssid/wlanpass"):
            bma.load_oenv_image(self.put("env-nowlan.img", self.env(final + WLAN[:1])))
        legacy = [("bootcmd", "run bootflash"), ("mtdparts", LEGACY_MTDPARTS)] + WLAN
        with self.assertRaisesRegex(bma.BuildError, "stock-U-Boot final layout"):
            bma.load_oenv_image(self.put("env-legacy.img", self.env(legacy)))
        with self.assertRaisesRegex(bma.BuildError, "stock-U-Boot final layout"):
            bma.load_oenv_image(self.put("env-oldboot.img", self.env([final[1], ("bootcmd", "run bootflash")] + WLAN)))

    def squashfs_header(self, used: int, size: int) -> bytes:
        head = bytearray(size)
        head[:4] = b"hsqs"
        struct.pack_into("<I", head, 12, 1 << 20)
        struct.pack_into("<H", head, 20, 4)
        struct.pack_into("<H", head, 24, 0x440)
        struct.pack_into("<HH", head, 28, 4, 0)
        struct.pack_into("<Q", head, 40, used)
        struct.pack_into("<II", head, 98, 1 << 20, 24)
        return bytes(head)

    def test_stock_appfs_checks(self):
        img = self.squashfs_header(4000, 4096)
        prof = bma.squashfs_profile(img)
        self.assertEqual((prof["compression"], prof["block_size"], prof["flags"], prof["comp_options"],
                          prof["version"], prof["bytes_used"]), (4, 1 << 20, 0x440, (1 << 20, 24), (4, 0), 4000))
        p = self.put("appfs.part", img)
        with self.assertRaisesRegex(bma.BuildError, "differs from the pinned image"):
            bma.load_stock_appfs(p)
        with mock.patch.object(bma, "STOCK_APPFS_SHA256", bma.sha256(img[:4000])):
            self.assertEqual(bma.load_stock_appfs(p), img[:4000])
        with self.assertRaisesRegex(bma.BuildError, "out of range"):
            bma.load_stock_appfs(self.put("big.part", self.squashfs_header(5000, 4096)))
        with self.assertRaisesRegex(bma.BuildError, "not a SquashFS"):
            bma.load_stock_appfs(self.put("zero.part", bytes(4096)))


class Bundle(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-bundle")

    def tearDown(self):
        rmtree(self.tmp)

    def test_bundle_is_private_complete_and_loadable(self):
        files = synthetic_files()
        run_id, conf = make_bundle(self.tmp / "b", files)
        out = self.tmp / "b"
        for p in [out] + list(out.rglob("*")):
            want = 0o700 if p.is_dir() else 0o600
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), want, p)
        meta = json.loads((out / "bundle.json").read_text())
        self.assertEqual((meta["schema"], meta["run_id"], meta["update_envelope_created"]),
                         (sm.BUNDLE_SCHEMA, run_id, False))
        self.assertEqual(meta["appfs"]["margin_bytes"], bma.APPFS_LIMIT - len(b"appfs-placeholder"))
        self.assertEqual(sorted(meta["files"]), sorted(sm.RUNTIME_FILES + sm.PAYLOAD_FILES))
        self.assertEqual((out / "conf" / "migrate.conf").read_text(), conf)
        self.assertEqual({rel: (out / "serve" / rel).read_bytes() for rel in files}, files)
        self.assertEqual(sm.load_bundle(out).run_id, run_id)

    def test_refusals_leave_nothing_behind(self):
        files = synthetic_files()
        conf = bma.render_conf(bma.conf_values("192.0.2.10", 8608, "a" * 32, files, STAGE2.read_bytes()))
        kw = dict(host="192.0.2.10", port=8608, run_id="a" * 32, stage2=STAGE2.read_bytes(), appfs=b"x")
        (self.tmp / "exists").mkdir()
        with self.assertRaisesRegex(bma.BuildError, "already exists"):
            bma.assemble_bundle(self.tmp / "exists", files, conf, **kw)
        with self.assertRaisesRegex(bma.BuildError, "outside the Git repository"):
            bma.assemble_bundle(sm.REPO_ROOT / "research" / "om-bundle-test", files, conf, **kw)
        with self.assertRaisesRegex(bma.BuildError, "parent directory"):
            bma.assemble_bundle(self.tmp / "no" / "out", files, conf, **kw)
        with self.assertRaises(TypeError):
            bma.assemble_bundle(self.tmp / "out", dict(files, **{"runtime/busybox": "not bytes"}), conf, **kw)
        with self.assertRaises(TypeError):  # the legacy env mode is gone
            bma.assemble_bundle(self.tmp / "out", files, conf, env_mode="image", **kw)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["exists"])
        self.assertFalse((sm.REPO_ROOT / "research" / "om-bundle-test").exists())


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-cli")
        self.junk = self.tmp / "junk.bin"
        self.junk.write_bytes(b"x" * 100)
        os.chmod(self.junk, 0o644)

    def tearDown(self):
        rmtree(self.tmp)

    def main(self, *extra, out=None, oenv_path=None, firmware=None):
        argv = ["--firmware", str(firmware or self.junk), "--oenv-image", str(oenv_path or self.junk),
                "--stock-appfs", str(self.junk), "--host", "192.0.2.10", "--mksquashfs", "/bin/true",
                "--unsquashfs", "/bin/true", "--out", str(out or self.tmp / "out"), *extra]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = bma.main(argv)
        return rc, err.getvalue()

    def test_errors_exit_2_without_output(self):
        cases = [
            ((), "OpenIPC firmware differs"),
            (("--host", "127.0.0.1"), "unicast LAN address"),
            (("--run-id", "ABCDEF0123456789"), "run-id"),
            (("--port", "0"), "--port"),
            (("--mksquashfs", str(self.tmp / "none")), "mksquashfs not found"),
            (("--poll-interval", "0"), "--poll-interval"),
            (("--start-delay", "4000"), "--start-delay"),
        ]
        for extra, msg in cases:
            rc, err = self.main(*extra)
            self.assertEqual(rc, 2, extra)
            self.assertIn(msg, err, extra)
        rc, err = self.main(out=sm.REPO_ROOT / "research" / "om-out-test")
        self.assertEqual(rc, 2)
        self.assertIn("outside the Git repository", err)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["junk.bin"])

    def test_oenv_and_firmware_layout_errors(self):
        pins = (mock.patch.object(bma, "FIRMWARE_SIZE", 100), mock.patch.object(bma, "FIRMWARE_SHA256",
                                                                                bma.sha256(b"x" * 100)))
        with pins[0], pins[1]:
            rc, err = self.main()
            self.assertEqual(rc, 2)
            self.assertIn("mode 0600", err)
            os.chmod(self.junk, 0o600)
            rc, err = self.main()
            self.assertIn("env-openipc.img: env image must be exactly", err)
            good = self.tmp / "env-openipc.img"
            good.write_bytes(oenv())
            os.chmod(good, 0o600)
            rc, err = self.main(oenv_path=good)
            self.assertEqual(rc, 2)
            self.assertIn(f"firmware.bin must be {sl.FW_LEN} bytes", err)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["env-openipc.img", "junk.bin"])

    def test_legacy_uboot_and_env_options_are_gone(self):
        base = ["--firmware", "a", "--oenv-image", "c", "--stock-appfs", "d", "--host", "192.0.2.10",
                "--out", str(self.tmp / "o")]
        for extra in (["--uboot", "b"], ["--env-erase"], ["--env-image", "e"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bma.main(base + extra)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bma.main([a for a in base if a not in ("--oenv-image", "c")])  # the OpenIPC env is mandatory


@unittest.skipUnless(MKSQ and UNSQ, "mksquashfs/unsquashfs not available (set OM_MKSQUASHFS/OM_UNSQUASHFS)")
class ToolChain(unittest.TestCase):
    """Real squashfs-tools on a synthetic stock appfs (pins monkeypatched to the synthetic data)."""

    def setUp(self):
        self.tmp = private_tmp("om-tools")
        self.work = self.tmp / "work"
        self.work.mkdir()

    def tearDown(self):
        rmtree(self.tmp)

    def squash(self, tree: Path, name: str, options=bma.MKSQUASHFS_OPTIONS) -> bytes:
        img = self.tmp / name
        bma.run_tool([MKSQ, tree, img, *options], 300)
        return img.read_bytes()

    def stock_tree(self) -> Path:
        root = self.tmp / "stock"
        root.mkdir()
        (root / "runapp.sh").write_bytes(RUNAPP)
        os.chmod(root / "runapp.sh", 0o744)
        (root / "cmsMain.out").write_bytes(rbytes(4, 50000))
        os.chmod(root / "cmsMain.out", 0o755)
        (root / "etc").mkdir()
        (root / "etc" / "conf.ini").write_bytes(b"[cfg]\nx=1\n")
        os.symlink("etc", root / "lib")
        os.chmod(root, 0o755)
        return root

    def test_build_appfs_round_trip(self):
        stock = self.squash(self.stock_tree(), "stock.sqfs")
        stock = stock[:bma.squashfs_profile(stock)["bytes_used"]]
        conf = b"OM_X='1'\n"
        with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP)):
            image, info = bma.build_appfs(stock, STAGE2.read_bytes(), conf, 45, MKSQ, UNSQ, self.work)
        self.assertEqual(info, {"changed_paths": bma.CHANGED_PATHS, "stock_entries": 6, "profile_matches_vendor": True})
        img = self.tmp / "migration.sqfs"
        img.write_bytes(image)

        def cat(name):
            return bma.run_tool([UNSQ, "-cat", img, name], 60).stdout
        with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP)):
            self.assertEqual(cat("runapp.sh"), bma.patch_runapp(RUNAPP, 45))
        self.assertEqual(cat("openipc-migrate/stage2.sh"), STAGE2.read_bytes())
        self.assertEqual(cat("openipc-migrate/migrate.conf"), conf)
        self.assertEqual(cat("cmsMain.out"), rbytes(4, 50000))
        listing = bma.listing_of(UNSQ, img)
        self.assertEqual({m["owner"] for m in listing.values()}, {"500/500"})
        self.assertEqual(listing["openipc-migrate/stage2.sh"]["mode"], 0o755)

    def test_build_appfs_refusals(self):
        tree = self.stock_tree()
        stock = self.squash(tree, "stock.sqfs")
        pin = mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP))
        with pin, mock.patch.object(bma, "APPFS_LIMIT", 4096), self.assertRaisesRegex(bma.BuildError, "appfs/bakfs"):
            bma.build_appfs(stock, STAGE2.read_bytes(), b"", 45, MKSQ, UNSQ, self.setup_work("w1"))
        rooted = [o for o in bma.MKSQUASHFS_OPTIONS if o not in ("-force-uid", "-force-gid", "500")] + ["-all-root"]
        with pin, self.assertRaisesRegex(bma.BuildError, "500/500"):
            bma.build_appfs(self.squash(tree, "root.sqfs", rooted), STAGE2.read_bytes(), b"", 45, MKSQ, UNSQ,
                            self.setup_work("w2"))
        gzip = ["-noappend", "-comp", "gzip", "-no-progress", "-force-uid", "500", "-force-gid", "500"]
        with pin, self.assertRaisesRegex(bma.BuildError, "vendor SquashFS profile"):
            bma.build_appfs(self.squash(tree, "gzip.sqfs", gzip), STAGE2.read_bytes(), b"", 45, MKSQ, UNSQ,
                            self.setup_work("w3"))

    def setup_work(self, name):
        path = self.tmp / name
        path.mkdir()
        return path

    def runtime_rootfs(self) -> bytes:
        root = self.tmp / "rootfs"
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        (root / "bin" / "busybox").write_bytes(make_elf())
        (root / "lib" / "libc.so").write_bytes(libc_elf())
        return self.squash(root, "rootfs.sqfs", ["-noappend", "-comp", "xz", "-no-progress"])

    def runtime_pins(self):
        return (mock.patch.object(bma, "RUNTIME_BUSYBOX_SHA256", bma.sha256(make_elf())),
                mock.patch.object(bma, "RUNTIME_LIBC_SHA256", bma.sha256(libc_elf())))

    def test_extract_runtime_from_a_firmware_image(self):
        firmware = b"\xff" * bma.ROOTFS_OFFSET + self.runtime_rootfs()
        pins = self.runtime_pins()
        with pins[0], pins[1]:
            busybox, libc = bma.extract_runtime(firmware, UNSQ, self.work)
        self.assertEqual((busybox, libc), (make_elf(), libc_elf()))
        self.assertEqual(list(self.work.iterdir()), [])
        with self.assertRaisesRegex(bma.BuildError, "pinned runtime"):
            bma.extract_runtime(firmware, UNSQ, self.work)

    def test_main_builds_a_loadable_private_bundle(self):
        self.assertEqual(bma.ROOTFS_OFFSET, sl.FW_KERNEL_LEN)
        kernel = uimage(rbytes(8, 1_500_000))
        fw = kernel + ff(sl.FW_KERNEL_LEN - len(kernel)) + self.runtime_rootfs()
        fw += ff(sl.FW_LEN - len(fw))
        inputs = {"fw.bin": fw, "env-openipc.img": oenv()}
        stock = self.squash(self.stock_tree(), "stock.sqfs")
        inputs["appfs.part"] = stock + ff(bma.APPFS_PARTITION - len(stock))
        stock = stock[:bma.squashfs_profile(stock)["bytes_used"]]  # the pin covers bytes_used only
        for name, data in inputs.items():
            (self.tmp / name).write_bytes(data)
            os.chmod(self.tmp / name, 0o600)
        out = self.tmp / "bundle"
        argv = ["--firmware", str(self.tmp / "fw.bin"), "--oenv-image", str(self.tmp / "env-openipc.img"),
                "--stock-appfs", str(self.tmp / "appfs.part"), "--host", "192.0.2.10", "--start-delay", "30",
                "--poll-interval", "15", "--poll-max", "40", "--mksquashfs", MKSQ, "--unsquashfs", UNSQ,
                "--workdir", str(self.work), "--out", str(out)]
        pins = (*self.runtime_pins(), mock.patch.object(bma, "FIRMWARE_SHA256", bma.sha256(fw)),
                mock.patch.object(bma, "STOCK_APPFS_SHA256", bma.sha256(stock)),
                mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP)))
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for pin in pins:
                stack.enter_context(pin)
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            rc = bma.main(argv)
            runapp = bma.patch_runapp(RUNAPP, 30)
        self.assertEqual(rc, 0, stderr.getvalue())
        summary = json.loads(stdout.getvalue())
        meta = json.loads((out / "bundle.json").read_text())
        self.assertNotIn(meta["run_id"], stdout.getvalue())
        self.assertEqual((summary["result"], summary["camera_contacted"], summary["update_envelope_created"]),
                         ("PASS", False, False))
        self.assertEqual(summary["kernel_slot_margin_bytes"], sl.FW_KERNEL_LEN - len(kernel))
        self.assertEqual(summary["appfs_margin_bytes"], bma.APPFS_LIMIT - summary["appfs_bytes"])
        self.assertEqual((summary["poll_window_s"], summary["start_delay_s"]), (600, 30))
        self.assertEqual(sorted(summary["served_bytes"]), sorted(sm.RUNTIME_FILES + sm.PAYLOAD_FILES))
        bundle = sm.load_bundle(out)
        self.assertEqual(bundle.run_id, meta["run_id"])
        conf_text = (out / "conf" / "migrate.conf").read_text()
        conf = sm.parse_conf(conf_text)
        self.assertEqual((conf["OM_FW_SHA256"], conf["OM_OENV_SHA256"], conf["OM_POLL_INTERVAL"], conf["OM_POLL_MAX"]),
                         (bma.sha256(fw), bma.sha256(oenv()), "15", "40"))
        self.assertEqual(conf["OM_INSTALLER_SHA256"], bma.sha256(INSTALLER.read_bytes()))
        self.assertEqual((out / "serve" / "payload" / "env-openipc.img").read_bytes(), oenv())
        busybox = (out / "serve" / "runtime" / "busybox").read_bytes()
        self.assertEqual(bma.elf_info(busybox)["interp"], bma.NEW_INTERP)
        img = out / "appfs" / "migration-appfs.squashfs"
        self.assertEqual(len(img.read_bytes()), summary["appfs_bytes"])
        for name, want in (("runapp.sh", runapp), ("openipc-migrate/migrate.conf", conf_text.encode()),
                           ("openipc-migrate/stage2.sh", STAGE2.read_bytes())):
            self.assertEqual(bma.run_tool([UNSQ, "-cat", img, name], 60).stdout, want, name)
        self.assertEqual(list(self.work.iterdir()), [])  # the temporary build tree is gone
        for p in [out] + list(out.rglob("*")):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o700 if p.is_dir() else 0o600, p)


if __name__ == "__main__":
    unittest.main()
