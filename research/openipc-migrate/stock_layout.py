#!/usr/bin/env python3
"""Stock-U-Boot ("shifted") OpenIPC flash plan for the Wi-Fi-only iBlack (Hi3518EV100, 8 MiB NOR).

The unit has neither UART nor Ethernet, so a bootloader that was never run on
this board must not be flashed: the vendor U-Boot 2010.06 (0x0-0x40000) is
never written and keeps reading its own 128 KiB environment at 0x40000.
OpenIPC is placed behind it (final layout, explicit sizes, sum 8 MiB):

  0x000000  384 KiB boot (ro)     vendor U-Boot 256 KiB + vendor env 128 KiB (env rewritten LAST)
  0x060000   64 KiB env           OpenIPC userland env (fw_printenv), built by make_env.py
  0x070000 1856 KiB kernel        \\ firmware.bin (nor-lite-split, 0x6B0000 B) written verbatim
  0x240000 4992 KiB rootfs        /
  0x720000  896 KiB rootfs_data   overlay, erased

Before that, a one-shot trial boots the OpenIPC kernel once from the bakfs
area with the stock userland. The armed vendor env restores the stock bootcmd
and runs saveenv BEFORE it boots the trial kernel, so any failure (bad CRC,
panic, hang + power cycle) ends in the stock firmware.

This module holds the geometry, a codec for the 128 KiB vendor env, a model of
the vendor U-Boot 2010.06 simple command parser (run_command/process_macros/
parse_line, CONFIG_SYS_CBSIZE 1024, CONFIG_SYS_MAXARGS 16, the 23 built-in
commands and their maxargs as read from the board's own U-Boot), a model of
the Linux 3.0.8 cmdlinepart parser, and builders/validators for the trial,
final and restore environments. Offline only: it never contacts a camera and
never prints env values (they include the unit's MAC address).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path

KIB = 1024
FLASH_BYTES = 0x800000
ERASE = 0x10000
STOCK_PARTS = (("boot", 0x000000, 0x200000), ("rootfs", 0x200000, 0x0C0000), ("appfs", 0x2C0000, 0x280000),
               ("mdat1", 0x540000, 0x020000), ("mdat2", 0x560000, 0x020000), ("bakfs", 0x580000, 0x280000))
UBOOT_OFF, UBOOT_LEN = 0x000000, 0x040000      # vendor U-Boot: never written
SENV_OFF, SENV_LEN = 0x040000, 0x020000        # vendor env, single copy, CRC32 over SENV_LEN - 4
OIPC_ENV_OFF, OIPC_ENV_LEN = 0x060000, 0x010000
TL_OFF, TL_LEN = 0x070000, 0x010000            # trial log mailbox (unused 0xFF area of the stock layout)
STOCK_KERNEL_OFF = 0x080000
FW_OFF, FW_LEN = 0x070000, 0x6B0000            # firmware.bin lands here verbatim
FW_KERNEL_LEN = 0x1D0000                       # kernel slot at the start of firmware.bin
OVERLAY_OFF = FW_OFF + FW_LEN                  # 0x720000
OVERLAY_LEN = FLASH_BYTES - OVERLAY_OFF
TRIAL_OFF, TRIAL_LEN = 0x580000, 0x280000      # = stock bakfs
TK_LEN = 0x1D0000                              # trial kernel slot
TP_OFF, TP_LEN = TRIAL_OFF + TK_LEN, TRIAL_LEN - TK_LEN   # trial payload SquashFS
WEAK_BIT_ABS = 0x6C4F28                        # unstable read seen in the bakfs investigation
LOAD_ADDR = 0x82000000
TL_TEXT_LEN = 60 * KIB                         # trial log text area; marker follows at this offset
TL_MARKER = b"OMTLOG-END"
TRIAL_MARK = "omtrial=1"
# The OpenIPC kernel (CONFIG_MTD_SPLIT_LAYOUT, hisfc350 probe) reads a uImage header at flash 0x50000 and
# nowhere else. When it finds one followed by a SquashFS, it registers its own boot/env/firmware/kernel/rootfs/
# rootfs_data table and ignores mtdparts= and an MTD root=. Here 0x50000 lies inside the vendor env.
SPLIT_PROBE_OFF = 0x050000
UIMAGE_MAGIC = b"\x27\x05\x19\x56"

# name, offset, size, read-only
FINAL_LAYOUT = (("boot", 0x000000, 0x060000, True), ("env", 0x060000, 0x010000, False),
                ("kernel", 0x070000, 0x1D0000, False), ("rootfs", 0x240000, 0x4E0000, False),
                ("rootfs_data", 0x720000, 0x0E0000, False))
TRIAL_LAYOUT = (("boot", 0x000000, 0x200000, True), ("rootfs", 0x200000, 0x0C0000, True),
                ("appfs", 0x2C0000, 0x280000, True), ("mdat1", 0x540000, 0x020000, True),
                ("mdat2", 0x560000, 0x020000, True), ("tk", TRIAL_OFF, TK_LEN, True),
                ("tp", TP_OFF, TP_LEN, True), ("tl", TL_OFF, TL_LEN, False), ("senv", SENV_OFF, SENV_LEN, False))

STOCK_BOOTCMD = "sf probe 0;sf read 0x80007fc0 0x80000 0x180000;bootm 0x80007fc0"
STOCK_DEFAULT_BOOTARGS_MEM = "mem=40M"         # vendor default env (used on env CRC error) keeps the stock layout

# Vendor U-Boot 2010.06 command table (name -> maxargs), read from the board's U-Boot at 0x1c16c.
UBOOT_COMMANDS = {"go": 16, "reset": 1, "bootm": 16, "help": 16, "?": 16, "loadb": 3, "loady": 3, "md": 3, "mm": 2,
                  "nm": 2, "mw": 4, "cp": 4, "cmp": 4, "mii": 5, "tftp": 4, "ping": 2, "wdstart": 1, "wdstop": 1,
                  "saveenv": 1, "printenv": 16, "setenv": 16, "initenv": 1, "sf": 5}
CBSIZE = 1024
MAXARGS = 16


class LayoutError(ValueError):
    pass


def mtdparts(layout, dev: str = "hi_sfc") -> str:
    parts, pos = [], 0
    for name, off, size, ro in layout:
        if size % KIB:
            raise LayoutError(f"{name}: size is not a KiB multiple")
        spec = f"{size // KIB}k" + (f"@0x{off:x}" if off != pos else "") + f"({name})" + ("ro" if ro else "")
        parts.append(spec)
        pos = off + size
    return f"{dev}:" + ",".join(parts)


FINAL_BOOTCMD = f"sf probe 0;sf read 0x{LOAD_ADDR:x} 0x{FW_OFF:x} 0x{FW_KERNEL_LEN:x};bootm 0x{LOAD_ADDR:x};reset"
FINAL_BOOTARGS = ("mem=32M console=ttyAMA0,115200 panic=20 root=/dev/mtdblock3 rootfstype=squashfs init=/init "
                  "mtdparts=" + mtdparts(FINAL_LAYOUT))
TRIAL_BOOTARGS = ("mem=42M console=ttyAMA0,115200 panic=10 root=1f01 rootfstype=squashfs "
                  "mtdparts=" + mtdparts(TRIAL_LAYOUT) + " " + TRIAL_MARK)
# Keys of the OpenIPC userland env (0x60000) that describe the flash layout.
OIPC_ENV_LAYOUT = {"mtdparts": mtdparts(FINAL_LAYOUT), "mtds": mtdparts(FINAL_LAYOUT).split(":", 1)[1],
                   "kernaddr": f"0x{FW_OFF:x}", "kernsize": f"0x{FW_KERNEL_LEN:x}",
                   "rootaddr": f"0x{FW_OFF + FW_KERNEL_LEN:x}", "rootsize": f"0x{FW_LEN - FW_KERNEL_LEN:x}",
                   "osmem": "32M"}


def trial_bootcmd(base_bootcmd: str) -> str:
    return (f"setenv bootcmd '{base_bootcmd}';saveenv;setenv bootargs {TRIAL_BOOTARGS};setenv verify y;"
            f"sf probe 0;sf read 0x{LOAD_ADDR:x} 0x{TRIAL_OFF:x} 0x{TK_LEN:x};bootm 0x{LOAD_ADDR:x};reset")


def part_at(abs_off: int, layout) -> tuple:
    hits = [(n, abs_off - o) for n, o, s, *_ in layout if o <= abs_off < o + s]
    if not hits:
        raise LayoutError(f"0x{abs_off:x} is outside the layout")
    return hits[0]


def tl_blank() -> bytes:
    """The empty tl checkpoint: zeros with TL_MARKER at TL_TEXT_LEN. trial.sh lays the log text over its start
    (the stock BusyBox 1.18.4 dd has no conv=sync/notrunc to pad or keep the rest)."""
    return bytes(TL_TEXT_LEN) + TL_MARKER + bytes(TL_LEN - TL_TEXT_LEN - len(TL_MARKER))


# ---- vendor env (128 KiB) ------------------------------------------------------------------
def parse_env(img: bytes, size: int = SENV_LEN) -> list:
    """Return the ordered (key, value) list of a valid env image (raises on bad size/CRC/format)."""
    if len(img) != size:
        raise LayoutError(f"env image must be exactly {size} bytes")
    crc, data = struct.unpack("<I", img[:4])[0], img[4:]
    if zlib.crc32(data) & 0xFFFFFFFF != crc:
        raise LayoutError("env CRC mismatch")
    end = data.find(b"\0\0")
    if end < 0:
        raise LayoutError("env data is not terminated")
    entries, seen = [], set()
    for raw in data[:end].split(b"\0"):
        if not raw or b"=" not in raw or not all(0x20 <= c < 0x7F for c in raw):
            raise LayoutError("env entry is not printable key=value")
        k, v = raw.decode("ascii").split("=", 1)
        if not k or k in seen:
            raise LayoutError("env has an empty or duplicate key")
        seen.add(k)
        entries.append((k, v))
    return entries


def build_env(entries, size: int = SENV_LEN) -> bytes:
    data = b"".join(f"{k}={v}".encode("ascii") + b"\0" for k, v in entries) + b"\0"
    if len(data) > size - 4:
        raise LayoutError("environment does not fit")
    data += b"\0" * (size - 4 - len(data))
    return struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF) + data


def env_crc_ok(img: bytes) -> bool:
    return len(img) >= 8 and zlib.crc32(img[4:]) & 0xFFFFFFFF == struct.unpack("<I", img[:4])[0]


def replace_values(entries, changes: dict) -> list:
    """Replace values in place (vendor key order kept); append keys that are missing."""
    out = [(k, changes.get(k, v)) for k, v in entries]
    keys = {k for k, _ in entries}
    out += [(k, v) for k, v in changes.items() if k not in keys]
    return out


# ---- vendor U-Boot 2010.06 simple parser model -----------------------------------------------
def ub_split(cmd: str) -> list:
    """run_command(): split on ';' outside '...' (a quote/semicolon after '\\' does not count)."""
    tokens, i, n = [], 0, len(cmd)
    while i < n:
        inq, j = False, i
        while j < n:
            prev = cmd[j - 1] if j > 0 else "\0"
            if cmd[j] == "'" and prev != "\\":
                inq = not inq
            if not inq and cmd[j] == ";" and j != i and prev != "\\":
                break
            j += 1
        tokens.append(cmd[i:j])
        i = j + 1 if j < n else j
    return tokens


def ub_process_macros(token: str, env: dict) -> str:
    """process_macros(): one level of '\\' escapes, '...' literal, ${v}/$(v) expansion, CBSIZE output cap."""
    out, state, prev, name, i, room = [], 0, "\0", [], 0, CBSIZE
    while i < len(token) and room:
        c = token[i]
        i += 1
        if state != 3 and c == "\\" and prev != "\\":
            if i >= len(token):
                break
            prev, c = c, token[i]
            i += 1
        if state == 0:
            if c == "'" and prev != "\\":
                state = 3
            elif c == "$" and prev != "\\":
                state = 1
            else:
                out.append(c)
                room -= 1
        elif state == 1:
            if c in "({":
                state, name = 2, []
            else:
                state = 0
                out.append("$")
                room -= 1
                if room:
                    out.append(c)
                    room -= 1
        elif state == 2:
            if c in ")}":
                val = env.get("".join(name), "")
                take = val[:room]
                out.append(take)
                room -= len(take)
                state = 0
            else:
                name.append(c)
        elif state == 3:
            if c == "'" and prev != "\\":
                state = 0
            else:
                out.append(c)
                room -= 1
        prev = c
    return "".join(out)


def ub_parse_line(line: str) -> tuple:
    """parse_line(): split on blanks/tabs; returns (argv, too_many)."""
    argv = [w for w in re.split(r"[ \t]+", line) if w]
    return argv[:MAXARGS], len(argv) > MAXARGS


class UBootModel:
    """Executes a bootcmd the way the vendor U-Boot 2010.06 would, on an ordered env (list semantics:
    setenv deletes the old definition and appends the new one at the end)."""

    def __init__(self, entries):
        self.env = list(entries)
        self.trace, self.saved = [], []

    def get(self, key):
        return next((v for k, v in self.env if k == key), None)

    def setenv(self, key, value):
        self.env = [(k, v) for k, v in self.env if k != key]
        if value is not None:
            self.env.append((key, value))

    def run(self, cmd: str) -> list:
        if len(cmd) >= CBSIZE:
            raise LayoutError(f"command is {len(cmd)} chars; U-Boot refuses >= {CBSIZE} (CONFIG_SYS_CBSIZE)")
        for token in ub_split(cmd):
            argv, too_many = ub_parse_line(ub_process_macros(token, dict(self.env)))
            if not argv:
                raise LayoutError("empty command token")
            if too_many:
                raise LayoutError(f"{argv[0]}: more than {MAXARGS} arguments")
            if argv[0] not in UBOOT_COMMANDS:
                raise LayoutError(f"'{argv[0]}' is not a command of the vendor U-Boot")
            if len(argv) > UBOOT_COMMANDS[argv[0]]:
                raise LayoutError(f"{argv[0]}: {len(argv)} args > maxargs {UBOOT_COMMANDS[argv[0]]}")
            self.trace.append(tuple(argv))
            if argv[0] == "setenv":
                if len(argv) < 2:
                    raise LayoutError("setenv without a name")
                self.setenv(argv[1], " ".join(argv[2:]) if len(argv) > 2 else None)
            elif argv[0] == "saveenv":
                self.saved.append(list(self.env))
            elif argv[0] == "reset":
                break
        return self.trace


# ---- Linux 3.0.8 cmdlinepart model ------------------------------------------------------------
def memparse(text: str) -> tuple:
    m = re.match(r"(0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)([kKmMgG]?)", text)
    if not m:
        raise LayoutError(f"bad number in mtdparts: {text[:12]!r}")
    val = int(m.group(1), 0) if not m.group(1).startswith("0") or m.group(1).lower().startswith("0x") \
        else int(m.group(1), 8)
    val <<= {"": 0, "k": 10, "m": 20, "g": 30}[m.group(2).lower()]
    return val, text[m.end():]


def parse_cmdline_mtdparts(cmdline: str, master_size: int = FLASH_BYTES, dev: str = "hi_sfc") -> list:
    """Return [(name, offset, size, ro)] as drivers/mtd/cmdlinepart.c would build them."""
    specs = [a for a in cmdline.split() if a.startswith("mtdparts=")]
    if len(specs) != 1:
        raise LayoutError("exactly one mtdparts= expected")
    body = specs[0][len("mtdparts="):]
    if not body.startswith(dev + ":"):
        raise LayoutError(f"mtdparts must describe {dev}")
    rest, parts, fill_up = body[len(dev) + 1:], [], False
    while True:
        if fill_up:
            raise LayoutError("no partitions allowed after a fill-up partition")
        if rest.startswith("-"):
            size, rest, fill_up = None, rest[1:], True
        else:
            size, rest = memparse(rest)
            if size < 4096:
                raise LayoutError("partition size too small")
        off = None
        if rest.startswith("@"):
            off, rest = memparse(rest[1:])
        m = re.match(r"\(([^)]*)\)", rest)
        if not m:
            raise LayoutError("every partition needs a (name) here")
        name, rest = m.group(1), rest[m.end():]
        ro = rest.startswith("ro")
        rest = rest[2:] if ro else rest
        parts.append([name, off, size, ro])
        if not rest:
            break
        if not rest.startswith(","):
            raise LayoutError(f"unexpected text in mtdparts: {rest[:12]!r}")
        rest = rest[1:]
    pos, out = 0, []
    for name, off, size, ro in parts:
        off = pos if off is None else off
        size = master_size - off if size is None else size
        if off + size > master_size:
            raise LayoutError(f"{name}: partitioning exceeds flash size")
        out.append((name, off, size, ro))
        pos = off + size
    return out


def root_partition(cmdline: str, parts: list) -> str:
    root = next((a.split("=", 1)[1] for a in cmdline.split() if a.startswith("root=")), None)
    m = re.fullmatch(r"/dev/mtdblock(\d+)|1f([0-9a-f]{2})", root or "")
    if not m:
        raise LayoutError("root= must name an mtdblock device")
    idx = int(m.group(1)) if m.group(1) else int(m.group(2), 16)
    if idx >= len(parts):
        raise LayoutError("root= points past the partition table")
    return parts[idx][0]


# ---- validators / builders -----------------------------------------------------------------------
def check_split_probe(senv_img: bytes, what: str) -> None:
    """The vendor env image at 0x40000 also decides what the OpenIPC kernel finds at 0x50000. A uImage header
    there would make the kernel replace the planned partition table and root with its own."""
    if not SENV_OFF <= SPLIT_PROBE_OFF < SENV_OFF + SENV_LEN:
        raise LayoutError("0x50000 is no longer inside the vendor env")
    rel = SPLIT_PROBE_OFF - SENV_OFF
    if senv_img[rel:rel + len(UIMAGE_MAGIC)] == UIMAGE_MAGIC:
        raise LayoutError(f"{what} would hold a uImage header at 0x50000: the OpenIPC kernel would ignore mtdparts=")


def check_base_env(entries) -> dict:
    env = dict(entries)
    bootcmd = env.get("bootcmd")
    if bootcmd != STOCK_BOOTCMD:
        raise LayoutError("base env bootcmd is not the stock one (armed, edited or another unit)")
    if any(TRIAL_MARK in f"{k}={v}" for k, v in entries):
        raise LayoutError("base env still carries the trial marker")
    if "bootargs" not in env:
        raise LayoutError("base env has no bootargs")
    if re.search(r"['$\\]", bootcmd) or bootcmd != " ".join(bootcmd.split()):
        raise LayoutError("stock bootcmd cannot be carried inside a quoted U-Boot literal")
    return env


def check_cmdline(cmdline: str, layout, root: str) -> list:
    parts = parse_cmdline_mtdparts(cmdline)
    if [tuple(p) for p in parts] != [tuple(p) for p in layout]:
        raise LayoutError("kernel would see a different partition table than planned")
    if root_partition(cmdline, parts) != root:
        raise LayoutError(f"root= does not point at {root}")
    return parts


def build_trial(base_img: bytes) -> tuple:
    """Return (armed env image, report). The armed env differs from the base only in bootcmd."""
    entries = parse_env(base_img)
    base = check_base_env(entries)
    cmd = trial_bootcmd(base["bootcmd"])
    armed_entries = replace_values(entries, {"bootcmd": cmd})
    armed = build_env(armed_entries)
    check_split_probe(armed, "the armed trial env")
    model = UBootModel(parse_env(armed))
    trace = model.run(cmd)
    want = [("setenv", "bootcmd", *STOCK_BOOTCMD.split()), ("saveenv",),
            ("setenv", "bootargs", *TRIAL_BOOTARGS.split()), ("setenv", "verify", "y"), ("sf", "probe", "0"),
            ("sf", "read", f"0x{LOAD_ADDR:x}", f"0x{TRIAL_OFF:x}", f"0x{TK_LEN:x}"), ("bootm", f"0x{LOAD_ADDR:x}"),
            ("reset",)]
    if trace != want:
        raise LayoutError("armed bootcmd does not execute as planned")
    if len(model.saved) != 1 or dict(model.saved[0]) != base:
        raise LayoutError("saveenv would not store the stock environment")
    saved_img = build_env(model.saved[0])
    if TRIAL_MARK.encode() in saved_img or dict(parse_env(saved_img)) != base:
        raise LayoutError("the env U-Boot saves before the trial boot is not the stock one")
    if model.get("bootargs") != TRIAL_BOOTARGS or model.get("verify") != "y":
        raise LayoutError("trial bootargs/verify not set")
    parts = check_cmdline(TRIAL_BOOTARGS, TRIAL_LAYOUT, "rootfs")
    writable = [(n, o, s) for n, o, s, ro in parts if not ro]
    if writable != [("tl", TL_OFF, TL_LEN), ("senv", SENV_OFF, SENV_LEN)]:
        raise LayoutError("only tl and senv may be writable during the trial")
    if any(o < UBOOT_LEN for _, o, _ in writable):
        raise LayoutError("a writable trial partition overlaps the vendor U-Boot")
    tokens = ub_split(cmd)
    report = {"bootcmd_chars": len(cmd), "cbsize": CBSIZE, "bootcmd_margin_chars": CBSIZE - 1 - len(cmd),
              "commands": len(tokens), "max_argc": max(len(ub_parse_line(ub_process_macros(t, {}))[0]) for t in tokens),
              "maxargs": MAXARGS, "changed_keys": ["bootcmd"], "saved_env_equals_stock": True,
              "env_bytes_used": len(b"".join(f"{k}={v}".encode() + b"\0" for k, v in armed_entries)) + 1,
              "trial_partitions": [p[0] for p in parts], "writable_partitions": [w[0] for w in writable]}
    return armed, report


def probe_bootcmd(base_bootcmd: str) -> str:
    """saveenv probe: restore the stock bootcmd, save, then boot stock as usual."""
    return f"setenv bootcmd '{base_bootcmd}';saveenv;{base_bootcmd};reset"


def stock_equivalent(img: bytes, base_img: bytes) -> bool:
    """True when img is a valid env with exactly the base's keys and values (order and padding may differ)."""
    try:
        return dict(parse_env(img)) == dict(parse_env(base_img))
    except LayoutError:
        return False


def build_probe(base_img: bytes) -> tuple:
    """Return (probe env image, report). Booting it proves the vendor U-Boot accepts an env we built, runs a
    quoted setenv and a working saveenv, and still boots the stock firmware; nothing else changes."""
    entries = parse_env(base_img)
    base = check_base_env(entries)
    cmd = probe_bootcmd(base["bootcmd"])
    probe = build_env(replace_values(entries, {"bootcmd": cmd}))
    model = UBootModel(parse_env(probe))
    trace = model.run(cmd)
    stock = [tuple(t.split()) for t in ub_split(STOCK_BOOTCMD)]
    if trace != [("setenv", "bootcmd", *STOCK_BOOTCMD.split()), ("saveenv",), *stock, ("reset",)]:
        raise LayoutError("probe bootcmd does not execute as planned")
    if len(model.saved) != 1 or dict(model.saved[0]) != base or model.saved[0][-1][0] != "bootcmd":
        raise LayoutError("probe saveenv would not store the stock environment")
    report = {"bootcmd_chars": len(cmd), "bootcmd_margin_chars": CBSIZE - 1 - len(cmd), "commands": len(ub_split(cmd)),
              "changed_keys": ["bootcmd"], "saved_env_equals_stock": True, "saved_bootcmd_position": "last"}
    return probe, report


def check_probe_result(live_img: bytes, base_img: bytes) -> dict:
    """Classify the vendor env read after the probe boot. Success = valid CRC, same keys/values as the base and
    bootcmd moved to the end (only U-Boot's own setenv + saveenv produce that order)."""
    try:
        entries = parse_env(live_img)
    except LayoutError as exc:
        return {"ok": False, "reason": f"live env invalid ({exc}): U-Boot fell back to its default env"}
    env = dict(entries)
    if env.get("bootcmd", "").startswith("setenv bootcmd "):
        return {"ok": False, "reason": "probe env still in place: U-Boot did not run it or saveenv failed"}
    if any(TRIAL_MARK in f"{k}={v}" for k, v in entries):
        return {"ok": False, "reason": "live env carries the trial marker"}
    if env != dict(parse_env(base_img)):
        return {"ok": False, "reason": "live env differs from the base env"}
    if entries[-1][0] != "bootcmd":
        return {"ok": False, "reason": "bootcmd is not the last key: not saved by the probe"}
    return {"ok": True, "reason": "vendor U-Boot ran the probe env and saveenv stored the stock env"}


def build_final(base_img: bytes) -> tuple:
    entries = parse_env(base_img)
    check_base_env(entries)
    final_entries = replace_values(entries, {"bootcmd": FINAL_BOOTCMD, "bootargs": FINAL_BOOTARGS, "verify": "y"})
    final = build_env(final_entries)
    check_split_probe(final, "the final env")
    model = UBootModel(parse_env(final))
    trace = model.run(FINAL_BOOTCMD)
    want = [("sf", "probe", "0"), ("sf", "read", f"0x{LOAD_ADDR:x}", f"0x{FW_OFF:x}", f"0x{FW_KERNEL_LEN:x}"),
            ("bootm", f"0x{LOAD_ADDR:x}"), ("reset",)]
    if trace != want or model.saved:
        raise LayoutError("final bootcmd does not execute as planned")
    parts = check_cmdline(FINAL_BOOTARGS, FINAL_LAYOUT, "rootfs")
    if sum(p[2] for p in parts) != FLASH_BYTES or not parts[0][3]:
        raise LayoutError("final layout must cover 8 MiB with a read-only boot partition")
    changed = sorted(k for k, v in final_entries if dict(entries).get(k) != v)
    report = {"bootcmd_chars": len(FINAL_BOOTCMD), "bootargs_chars": len(FINAL_BOOTARGS),
              "bootcmd_margin_chars": CBSIZE - 1 - len(FINAL_BOOTCMD), "changed_keys": changed,
              "final_partitions": [p[0] for p in parts], "split_probe_0x50000": "vendor env, no uImage header"}
    return final, report


def check_restore(img: bytes) -> list:
    entries = parse_env(img)
    check_base_env(entries)
    return entries


def uimage_info(data: bytes) -> dict:
    if len(data) < 64 or data[:4] != b"\x27\x05\x19\x56":
        raise LayoutError("not a uImage")
    hdr = bytearray(data[:64])
    hcrc = struct.unpack(">I", hdr[4:8])[0]
    hdr[4:8] = b"\0\0\0\0"
    size, load, ep, dcrc = struct.unpack(">IIII", data[12:28])
    if zlib.crc32(bytes(hdr)) & 0xFFFFFFFF != hcrc:
        raise LayoutError("uImage header CRC mismatch")
    if len(data) < 64 + size or zlib.crc32(data[64:64 + size]) & 0xFFFFFFFF != dcrc:
        raise LayoutError("uImage data CRC mismatch")
    return {"total": 64 + size, "load": load, "ep": ep, "os": data[28], "arch": data[29], "type": data[30],
            "comp": data[31], "name": data[32:64].split(b"\0")[0].decode("ascii", "replace")}


def power_fail_table() -> list:
    return [
        {"step": "probe-arm: env write (2 x 64 KiB)", "if_interrupted":
         f"env CRC bad -> vendor default env ({STOCK_DEFAULT_BOOTARGS_MEM}, stock layout) -> stock boots"},
        {"step": "probe boot: U-Boot saveenv", "if_interrupted": "env CRC bad -> vendor default env -> stock boots"},
        {"step": "trial-arm: bakfs write", "if_interrupted": "stock boots (bakfs only holds the trial image)"},
        {"step": "trial-arm: env write (2 x 64 KiB)", "if_interrupted":
         f"env CRC bad -> vendor default env ({STOCK_DEFAULT_BOOTARGS_MEM}, stock layout) -> stock boots"},
        {"step": "trial boot: U-Boot saveenv", "if_interrupted": "env CRC bad -> vendor default env -> stock boots"},
        {"step": "trial kernel bad CRC / panic / hang + power cycle", "if_interrupted":
         "stock bootcmd already saved -> stock boots"},
        {"step": "final: OpenIPC env 0x60000 + first firmware block 0x70000", "if_interrupted":
         "stock intact (area unused by the stock layout)"},
        {"step": "final: firmware from 0x80000 + overlay erase", "if_interrupted":
         "stock kernel gone, env still stock -> U-Boot stops (bad magic) -> SPI recovery"},
        {"step": "final: vendor env write (last)", "if_interrupted":
         "env CRC bad -> default env -> stock bootcmd on OpenIPC data -> SPI recovery"},
        {"step": "final: done", "if_interrupted": "boots OpenIPC (verify=y; bad CRC -> reset loop -> SPI recovery)"},
    ]


def plan(firmware: bytes = None) -> dict:
    for layout in (FINAL_LAYOUT, TRIAL_LAYOUT):
        mtdparts(layout)
    if sum(s for _, _, s, _ in FINAL_LAYOUT) != FLASH_BYTES or sum(s for _, _, s in STOCK_PARTS) != FLASH_BYTES:
        raise LayoutError("layouts must cover 8 MiB")
    if FW_OFF + FW_LEN != OVERLAY_OFF or OVERLAY_OFF != FINAL_LAYOUT[4][1] or TP_OFF + TP_LEN != FLASH_BYTES:
        raise LayoutError("internal geometry mismatch")
    out = {
        "flash_bytes": FLASH_BYTES, "erase_bytes": ERASE,
        "never_written": {"vendor_uboot": [f"0x{UBOOT_OFF:x}", f"0x{UBOOT_OFF + UBOOT_LEN:x}"]},
        "final_layout": [{"name": n, "offset": f"0x{o:x}", "kib": s // KIB, "ro": ro} for n, o, s, ro in FINAL_LAYOUT],
        "final_mtdparts": mtdparts(FINAL_LAYOUT), "final_bootcmd": FINAL_BOOTCMD, "final_bootargs": FINAL_BOOTARGS,
        "trial_layout": [{"name": n, "offset": f"0x{o:x}", "kib": s // KIB, "ro": ro} for n, o, s, ro in TRIAL_LAYOUT],
        "trial_bootargs": TRIAL_BOOTARGS, "trial_bootcmd_template": trial_bootcmd("<stock bootcmd>"),
        "trial_bootcmd_chars": len(trial_bootcmd(STOCK_BOOTCMD)), "cbsize": CBSIZE, "maxargs": MAXARGS,
        "probe_bootcmd_template": probe_bootcmd("<stock bootcmd>"),
        "probe_bootcmd_chars": len(probe_bootcmd(STOCK_BOOTCMD)), "final_bootcmd_chars": len(FINAL_BOOTCMD),
        "overlay_kib": OVERLAY_LEN // KIB, "trial_payload_limit_bytes": TP_LEN, "trial_kernel_slot_bytes": TK_LEN,
        "weak_bit": {"abs": f"0x{WEAK_BIT_ABS:x}", "stock": "bakfs+0x%x" % part_at(WEAK_BIT_ABS, STOCK_PARTS)[1],
                     "trial": "tk+0x%x" % part_at(WEAK_BIT_ABS, TRIAL_LAYOUT)[1],
                     "final": "rootfs+0x%x" % part_at(WEAK_BIT_ABS, FINAL_LAYOUT)[1]},
        "power_fail": power_fail_table(),
        "kernel_split_probe": {"offset": f"0x{SPLIT_PROBE_OFF:x}", "holds": "vendor env (printable text, zero padded)",
                               "effect": "no uImage header there, so the kernel takes mtdparts= and root="},
    }
    if firmware is not None:
        if len(firmware) != FW_LEN:
            raise LayoutError(f"firmware.bin must be {FW_LEN} bytes")
        k = uimage_info(firmware)
        if not (k["total"] <= FW_KERNEL_LEN and k["load"] == 0x80008000 and k["arch"] == 2 and k["os"] == 5):
            raise LayoutError("firmware kernel does not fit the 1856 KiB slot or is not an ARM Linux uImage")
        sq = firmware[FW_KERNEL_LEN:FW_KERNEL_LEN + 96]
        if sq[:4] != b"hsqs":
            raise LayoutError("no SquashFS rootfs at 0x1d0000 inside firmware.bin")
        used = struct.unpack("<Q", sq[40:48])[0]
        out["firmware"] = {"sha256": hashlib.sha256(firmware).hexdigest(), "kernel_uimage_bytes": k["total"],
                           "kernel_slot_margin_bytes": FW_KERNEL_LEN - k["total"], "kernel_comp": k["comp"],
                           "rootfs_bytes_used": used, "rootfs_slot_bytes": FW_LEN - FW_KERNEL_LEN,
                           "rootfs_slot_margin_bytes": FW_LEN - FW_KERNEL_LEN - used,
                           "weak_bit_in_firmware_rootfs_offset": f"0x{WEAK_BIT_ABS - FW_OFF - FW_KERNEL_LEN:x}"}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="print the capacity/geometry plan (no private input)")
    p.add_argument("--firmware", type=Path)
    p.add_argument("--out", type=Path)
    c = sub.add_parser("check-env", help="classify a 128 KiB vendor env image (prints no values)")
    c.add_argument("image", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            result = plan(args.firmware.read_bytes() if args.firmware else None)
            text = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if args.out:
                if args.out.exists():
                    raise LayoutError("--out exists; refusing to overwrite")
                args.out.write_text(text)
            print(text, end="")
        else:
            img = args.image.read_bytes()
            entries = parse_env(img)
            env = dict(entries)
            state = ("armed" if TRIAL_MARK in env.get("bootcmd", "") else
                     "probe" if env.get("bootcmd") == probe_bootcmd(STOCK_BOOTCMD) else
                     "stock" if env.get("bootcmd") == STOCK_BOOTCMD else
                     "final" if env.get("bootcmd") == FINAL_BOOTCMD else "unknown")
            print(json.dumps({"crc_ok": True, "keys": [k for k, _ in entries], "bootcmd_state": state,
                              "verify": env.get("verify") in ("y", "yes", "1")}, indent=2))
    except (LayoutError, OSError) as exc:
        print(f"stock_layout: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
