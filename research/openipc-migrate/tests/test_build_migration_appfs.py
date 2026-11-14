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
from om_testlib import INSTALLER, STAGE2, bma, make_bundle, private_tmp, rbytes, rmtree, sm, synthetic_files

import make_env  # noqa: E402  (om_testlib put the tool directory on sys.path)

BUSYBOX = shutil.which("busybox")
MKSQ = os.environ.get("OM_MKSQUASHFS") or shutil.which("mksquashfs")
UNSQ = os.environ.get("OM_UNSQUASHFS") or shutil.which("unsquashfs")
RUNAPP = b"#!/bin/sh\n# stand-in for the stock runapp.sh\necho stock-app-start\nexit 0\n"
ENV_DEFAULTS = [("bootcmd", "run bootflash"), ("bootdelay", "1"), ("baudrate", "115200"),
                ("ipaddr", "192.0.2.20"), ("serverip", "192.0.2.254"),
                ("mtdparts", "hi_sfc:256k(boot),64k(env),2048k(kernel),5120k(rootfs),-(rootfs_data)")]
OPENIPC_MTDPARTS = "hi_sfc:256k(boot),64k(env),7168k@0x50000(firmware),-(rootfs_data)"


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
        return bma.conf_values("192.0.2.10", 8608, self.run_id, self.files, self.stage2, "image", over or None)

    def test_round_trip_and_pins(self):
        values = self.values()
        text = bma.render_conf(values)
        self.assertEqual(sm.parse_conf(text), values)
        self.assertEqual(values["OM_RT_BB_SHA256"], bma.sha256(self.files["runtime/busybox"]))
        self.assertEqual(values["OM_STAGE2_SHA256"], bma.sha256(self.stage2))
        self.assertEqual(values["OM_ENV_SHA256"], bma.sha256(self.files[sm.ENV_FILE]))
        self.assertEqual((values["OM_DIR"], values["OM_APP_DIR"]), (bma.OM_DIR, bma.OM_APP_DIR))
        self.assertTrue(set(values) == set(bma.CONF_KEYS))
        erase = bma.conf_values("192.0.2.10", 8608, self.run_id, synthetic_files("erase"), self.stage2, "erase")
        self.assertEqual((erase["OM_ENV_MODE"], erase["OM_ENV_SHA256"]), ("erase", ""))
        bma.render_conf(erase)

    def test_a_posix_shell_reads_back_every_value(self):
        tmp = private_tmp("om-conf")
        try:
            conf = tmp / "migrate.conf"
            values = self.values(OM_POLL_MAX=7)
            conf.write_text(bma.render_conf(values))
            keys = sorted(values)
            script = ". \"$1\"; " + "; ".join(f'printf "%s=%s\\n" {k} "${k}"' for k in keys)
            for shell in [["sh"]] + ([[BUSYBOX, "sh"]] if BUSYBOX else []):
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
            "test key": dict(self.values(), OM_SIM_DIR="/tmp/x"),
            "metachar": self.values(OM_HOST="192.0.2.10;reboot"),
            "quote": self.values(OM_HOST="192.0.2.10'x"),
            "subst": self.values(OM_LOCK="/dev/$(reboot)"),
            "number": self.values(OM_POLL_INTERVAL="20s"),
            "zero interval": self.values(OM_POLL_INTERVAL=0),
            "port": bma.conf_values("192.0.2.10", 70000, self.run_id, self.files, self.stage2, "image"),
            "relative": self.values(OM_LOCK="dev/lock"),
            "dotdot": self.values(OM_STOCK_BB="/bin/../tmp/busybox"),
            "digest": self.values(OM_RT_BB_SHA1="0" * 39),
            "env image without digest": self.values(OM_ENV_SHA256=""),
            "env erase with digest": self.values(OM_ENV_MODE="erase"),
            "run id": bma.conf_values("192.0.2.10", 8608, "ABCDEF0123456789", self.files, self.stage2, "image"),
            "short run id": bma.conf_values("192.0.2.10", 8608, "abc123", self.files, self.stage2, "image"),
            "om dir": self.values(OM_DIR="/tmp/om"),
            "app dir": self.values(OM_APP_DIR="/tmp/app"),
        }
        for label, values in bad.items():
            with self.assertRaises(bma.BuildError, msg=label):
                bma.render_conf(values)
        with self.assertRaisesRegex(bma.BuildError, "served file set"):
            bma.conf_values("192.0.2.10", 8608, self.run_id, synthetic_files("erase"), self.stage2, "image")
        with self.assertRaisesRegex(bma.BuildError, "env mode"):
            bma.conf_values("192.0.2.10", 8608, self.run_id, self.files, self.stage2, "both")

    def test_test_only_keys_need_the_explicit_switch(self):
        values = self.values(OM_DIR="/dev/shm/om-x", OM_SIM_DIR="/dev/shm/sim", OM_WATCHDOG_DEV="/dev/shm/no")
        text = bma.render_conf(values, allow_test_keys=True)
        self.assertIn("OM_SIM_DIR='/dev/shm/sim'", text)
        self.assertEqual(sm.parse_conf(text)["OM_DIR"], "/dev/shm/om-x")
        self.assertTrue(text.rstrip().endswith("OM_WATCHDOG_DEV='/dev/shm/no'"))


class RunappAndTree(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-tree")
        self.pin = mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(RUNAPP))

    def tearDown(self):
        rmtree(self.tmp)

    def test_launcher_replaces_only_the_final_exit(self):
        with self.pin:
            out = bma.patch_runapp(RUNAPP, 45)
        self.assertTrue(out.startswith(RUNAPP[:-len(b"exit 0\n")]))
        self.assertTrue(out.endswith(b"\nexit 0\n"))
        self.assertEqual(out.count(b"exit 0"), 1)
        self.assertIn(b"/bin/busybox sleep 45\n", out)
        self.assertIn(b"/bin/sh /app/openipc-migrate/stage2.sh phase1\n", out)
        self.assertIn(b") </dev/null >/dev/null 2>&1 &\n", out)
        script = self.tmp / "runapp.sh"
        script.write_bytes(out)
        for shell in [("sh",)] + ([(BUSYBOX, "sh")] if BUSYBOX else []):
            r = run_sh(script, shell)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_runapp_returns_at_once_and_the_agent_starts_later(self):
        with self.pin:
            out = bma.patch_runapp(RUNAPP, 3)
        mark = self.tmp / "agent-started"
        out = out.replace(b"/bin/busybox sleep", b"sleep").replace(
            b"/bin/sh /app/openipc-migrate/stage2.sh phase1", f"echo started > {mark}".encode())
        script = self.tmp / "runapp.sh"
        script.write_bytes(out)
        t0 = time.monotonic()
        r = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual((r.returncode, r.stdout), (0, "stock-app-start\n"))
        self.assertLess(time.monotonic() - t0, 2.5)  # boot is not delayed by the agent
        self.assertFalse(mark.exists())
        for _ in range(150):
            if mark.exists():
                break
            time.sleep(0.1)
        self.assertEqual(mark.read_text(), "started\n")

    def test_runapp_rejections(self):
        with self.assertRaisesRegex(bma.BuildError, "pinned stock script"):
            bma.patch_runapp(RUNAPP, 45)
        for data in (RUNAPP + b"\n", RUNAPP.replace(b"echo", b"exit 0\necho")):
            with mock.patch.object(bma, "STOCK_RUNAPP_SHA256", bma.sha256(data)):
                with self.assertRaisesRegex(bma.BuildError, "only `exit 0`"):
                    bma.patch_runapp(data, 45)
        with self.pin:
            for delay in (-1, 3601):
                with self.assertRaisesRegex(bma.BuildError, "start delay"):
                    bma.patch_runapp(RUNAPP, delay)

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

    def test_phase1_must_stay_within_the_stock_busybox(self):
        cases = {
            "arithmetic": (b"p1_ge(){", b"x=$((1+1)); p1_ge(){"),
            "head": (b"2>/dev/null | awk '{print $1}'; }", b"2>/dev/null | head -n 1; }"),
            "sha256sum": (b"p1_sum sha1sum", b"p1_sum sha256sum"),
            "chmod": (b"  rm -f \"$1\"\n", b"  chmod 755 \"$2\"\n"),
        }
        for label, (old, new) in cases.items():
            self.assertEqual(self.s2.count(old), 1, label)
            with self.assertRaises(bma.BuildError, msg=label):
                bma.check_scripts(self.s2.replace(old, new), self.inst)
        # Runtime applets are fine in phase2, and anything is fine in comments.
        ok = self.s2.replace(b"phase2(){\n", b"phase2(){\n  \"$BB\" head -c 1 /dev/null\n")
        ok = ok.replace(b"phase1(){\n", b"phase1(){\n  # sha256sum, head and $((...)) are not available here\n")
        bma.check_scripts(ok, self.inst)

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
        with self.assertRaisesRegex(bma.BuildError, "CONFIRM_PHRASE"):
            bma.check_scripts(self.s2, self.inst.replace(sm.WRITE_PHRASE.encode(), b"yes-write"))
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

    def env_image(self, **extra):
        additions = {"wlandev": "mt7601u-hi3518ev100-camhi", "mtdparts": OPENIPC_MTDPARTS}
        additions.update(extra)
        return make_env.build_env_image(ENV_DEFAULTS, {k: v for k, v in additions.items() if v is not None})

    def test_env_image_checks(self):
        good = self.put("env.img", self.env_image())
        self.assertEqual(len(bma.load_env_image(good)), make_env.ENV_SIZE)
        with self.assertRaisesRegex(bma.BuildError, "mode 0600"):
            bma.load_env_image(self.put("env-loose.img", self.env_image(), 0o644))
        corrupt = bytearray(self.env_image())
        corrupt[10] ^= 1
        with self.assertRaisesRegex(bma.BuildError, "env image rejected"):
            bma.load_env_image(self.put("env-crc.img", bytes(corrupt)))
        with self.assertRaisesRegex(bma.BuildError, "lacks wlandev"):
            bma.load_env_image(self.put("env-nowlan.img", self.env_image(wlandev=None)))
        with self.assertRaisesRegex(bma.BuildError, "7168k@0x50000"):
            bma.load_env_image(self.put("env-stock.img", self.env_image(mtdparts=None)))

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
        self.assertEqual((out / "conf" / "migrate.conf").read_text(), conf)
        self.assertEqual({rel: (out / "serve" / rel).read_bytes() for rel in files}, files)
        self.assertEqual(sm.load_bundle(out).run_id, run_id)

    def test_refusals_leave_nothing_behind(self):
        files = synthetic_files()
        conf = bma.render_conf(bma.conf_values("192.0.2.10", 8608, "a" * 32, files, STAGE2.read_bytes(), "image"))
        kw = dict(host="192.0.2.10", port=8608, run_id="a" * 32, env_mode="image", stage2=STAGE2.read_bytes(),
                  appfs=b"x")
        (self.tmp / "exists").mkdir()
        with self.assertRaisesRegex(bma.BuildError, "already exists"):
            bma.assemble_bundle(self.tmp / "exists", files, conf, **kw)
        with self.assertRaisesRegex(bma.BuildError, "outside the Git repository"):
            bma.assemble_bundle(sm.REPO_ROOT / "research" / "om-bundle-test", files, conf, **kw)
        with self.assertRaisesRegex(bma.BuildError, "parent directory"):
            bma.assemble_bundle(self.tmp / "no" / "out", files, conf, **kw)
        with self.assertRaises(TypeError):
            bma.assemble_bundle(self.tmp / "out", dict(files, **{"runtime/busybox": "not bytes"}), conf, **kw)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["exists"])
        self.assertFalse((sm.REPO_ROOT / "research" / "om-bundle-test").exists())


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-cli")
        self.junk = self.tmp / "junk.bin"
        self.junk.write_bytes(b"x" * 100)

    def tearDown(self):
        rmtree(self.tmp)

    def main(self, *extra, out=None):
        argv = ["--firmware", str(self.junk), "--uboot", str(self.junk), "--env-erase",
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

    def test_env_options_are_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bma.main(["--firmware", "a", "--uboot", "b", "--env-erase", "--env-image", "c", "--stock-appfs", "d",
                      "--host", "192.0.2.10", "--out", str(self.tmp / "o")])


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

    def test_extract_runtime_from_a_firmware_image(self):
        root = self.tmp / "rootfs"
        (root / "bin").mkdir(parents=True)
        (root / "lib").mkdir()
        (root / "bin" / "busybox").write_bytes(make_elf())
        (root / "lib" / "libc.so").write_bytes(libc_elf())
        rootfs = self.squash(root, "rootfs.sqfs", ["-noappend", "-comp", "xz", "-no-progress"])
        firmware = b"\xff" * bma.ROOTFS_OFFSET + rootfs
        pins = (mock.patch.object(bma, "RUNTIME_BUSYBOX_SHA256", bma.sha256(make_elf())),
                mock.patch.object(bma, "RUNTIME_LIBC_SHA256", bma.sha256(libc_elf())))
        with pins[0], pins[1]:
            busybox, libc = bma.extract_runtime(firmware, UNSQ, self.work)
        self.assertEqual((busybox, libc), (make_elf(), libc_elf()))
        self.assertEqual(list(self.work.iterdir()), [])
        with self.assertRaisesRegex(bma.BuildError, "pinned runtime"):
            bma.extract_runtime(firmware, UNSQ, self.work)


if __name__ == "__main__":
    unittest.main()
