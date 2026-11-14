#!/usr/bin/env python3
"""Offline planner for the stock iBlack -> OpenIPC network (online) migration.

This module performs NO camera access, NO network I/O and NO flash writes. It
reads the pinned public OpenIPC artifacts (firmware.bin and the nor U-Boot),
validates their structure against the real on-flash boot contract, and emits a
sanitized absolute write plan plus the mapping of every absolute range onto the
STOCK 6-partition MTD layout the installer actually runs under on its first
pass. It is a plan generator, not a flasher and not an update-envelope builder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import struct
import zlib
from dataclasses import dataclass, asdict
from pathlib import Path

FLASH_BYTES = 0x800000          # 8 MiB NOR (from prior hardware analysis)
ERASE_BYTES = 0x10000           # 64 KiB sector ASSUMPTION; verify via MEMGETINFO
UIMAGE_MAGIC = 0x27051956
SQUASHFS_LE = 0x73717368        # 'hsqs' little-endian u32
FW_BASE = 0x50000               # OpenIPC firmware offset for this SoC
OVERLAY_BASE = 0x700000         # where this image's rootfs_data must start
SOC = "hi3518ev100"
BOOT_LEN = 0x40000             # bootloader region incl. 0xFF padding
ENV_BASE = 0x40000
ENV_LEN = 0x10000

# OpenIPC target NOR tiling: 256k boot, 64k env, firmware to end of chip.
TARGET = [
    ("boot", 0x0, 0x40000),
    ("env", 0x40000, 0x10000),
    ("firmware", 0x50000, FLASH_BYTES - 0x50000),
]

# STOCK mtdparts the installer's first pass runs under (from the vendor dump):
# 2048K(boot),768K(rootfs),2560K(appfs),128K(mdat1),128K(mdat2),2560K(bakfs)
STOCK_PARTS = [
    ("boot", 0x000000, 0x200000),
    ("rootfs", 0x200000, 0x0c0000),
    ("appfs", 0x2c0000, 0x280000),
    ("mdat1", 0x540000, 0x020000),
    ("mdat2", 0x560000, 0x020000),
    ("bakfs", 0x580000, 0x280000),
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bounded(path: Path, maximum: int) -> bytes:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= maximum:
        raise SystemExit(f"Refusing non-regular/oversized input: {path}")
    return path.read_bytes()


@dataclass
class Region:
    name: str
    abs_start: int
    abs_end: int
    source: str
    erase_only: bool


def uimage_fields(img: bytes):
    if len(img) < 64 or struct.unpack(">I", img[:4])[0] != UIMAGE_MAGIC:
        raise SystemExit("firmware.bin does not start with a uImage magic")
    hdr = bytearray(img[:64])
    stored_hcrc = struct.unpack(">I", hdr[4:8])[0]
    struct.pack_into(">I", hdr, 4, 0)
    hcrc_ok = (zlib.crc32(bytes(hdr)) & 0xFFFFFFFF) == stored_hcrc
    data_size = struct.unpack(">I", img[12:16])[0]
    load = struct.unpack(">I", img[16:20])[0]
    entry = struct.unpack(">I", img[20:24])[0]
    stored_dcrc = struct.unpack(">I", img[24:28])[0]
    data = img[64:64 + data_size]
    dcrc_ok = len(data) == data_size and (zlib.crc32(data) & 0xFFFFFFFF) == stored_dcrc
    name = img[32:64].split(b"\0", 1)[0].decode("latin1")
    return data_size, load, entry, hcrc_ok, dcrc_ok, name


def analyze_firmware(fw: bytes) -> dict:
    data_size, load, entry, hcrc_ok, dcrc_ok, name = uimage_fields(fw)
    blocks = (64 + data_size + ERASE_BYTES - 1) // ERASE_BYTES
    sq_off = blocks * ERASE_BYTES
    sq_ok = fw[sq_off:sq_off + 4] == struct.pack("<I", SQUASHFS_LE)
    bytes_used = struct.unpack("<I", fw[sq_off + 40:sq_off + 44])[0] if sq_ok else 0
    overlay_rel = sq_off + (bytes_used + ERASE_BYTES - 1) // ERASE_BYTES * ERASE_BYTES
    soc = name.split("-")[-1]
    checks = {
        "uimage_magic_ok": True,
        "uimage_header_crc_ok": hcrc_ok,
        "uimage_data_crc_ok": dcrc_ok,
        "kernel_within_2mib": 64 + data_size <= 0x200000,
        "squashfs_follows_kernel": sq_ok,
        "soc_matches": soc == SOC,
        "overlay_base_is_0x700000": FW_BASE + overlay_rel == OVERLAY_BASE,
        "image_ends_at_overlay": FW_BASE + len(fw) == OVERLAY_BASE,
        "fits_before_chip_end": FW_BASE + len(fw) <= FLASH_BYTES,
    }
    return {
        "name": name, "soc": soc, "kernel_bytes": data_size,
        "load": load, "entry": entry, "kernel_blocks": blocks,
        "squashfs_rel_offset": sq_off, "squashfs_bytes_used": bytes_used,
        "overlay_abs": FW_BASE + overlay_rel, "image_bytes": len(fw),
        "checks": checks, "all_ok": all(checks.values()),
    }


def map_to_stock(abs_start: int, abs_end: int) -> list:
    """Split an absolute flash range into (stock mtd index, in-part range)."""
    out = []
    for idx, (pname, pstart, psize) in enumerate(STOCK_PARTS):
        pend = pstart + psize
        lo, hi = max(abs_start, pstart), min(abs_end, pend)
        if lo < hi:
            out.append({
                "stock_mtd": idx, "stock_name": pname,
                "dev_char": f"/dev/mtd{idx}", "dev_block": f"/dev/mtdblock{idx}",
                "offset_in_part": lo - pstart, "length": hi - lo,
                "abs_start": lo, "abs_end": hi,
            })
    return out


def boot_image(uboot: bytes) -> bytes:
    """The exact 256 KiB written at 0x0: U-Boot followed by 0xFF padding."""
    return uboot + b"\xff" * (BOOT_LEN - len(uboot))


def build_plan(fw: bytes, uboot: bytes, env_mode: str = "image") -> dict:
    if env_mode not in ("image", "erase"):
        raise SystemExit("env_mode must be 'image' or 'erase'")
    if not 0 < len(uboot) <= BOOT_LEN:
        raise SystemExit("nor U-Boot does not fit the 256 KiB boot partition")
    fa = analyze_firmware(fw)
    env_src = ("generated 64 KiB env image (make_env.py: built-in defaults + "
               "wlandev/Wi-Fi keys from a private file)" if env_mode == "image"
               else "erase->U-Boot built-in default env (no Wi-Fi settings)")
    regions = [
        Region("bootloader", 0x0, BOOT_LEN,
               "u-boot-hi3518ev100-nor.bin + 0xFF padding to 256 KiB", False),
        Region("env", ENV_BASE, ENV_BASE + ENV_LEN, env_src, env_mode == "erase"),
        Region("firmware", FW_BASE, FW_BASE + len(fw), "firmware.bin.hi3518ev100", False),
        Region("overlay", OVERLAY_BASE, FLASH_BYTES, "erase->firstboot formats", True),
    ]
    tiles = all(a.abs_end == b.abs_start for a, b in zip(regions, regions[1:]))
    inv = {
        "uboot_fits_boot": 0 < len(uboot) <= BOOT_LEN,
        "env_is_64k": regions[1].abs_end - regions[1].abs_start == ENV_LEN,
        "firmware_starts_0x50000": regions[2].abs_start == FW_BASE,
        "firmware_ends_at_overlay": regions[2].abs_end == OVERLAY_BASE,
        "overlay_to_chip_end": regions[3].abs_end == FLASH_BYTES,
        "regions_tile_whole_chip": (regions[0].abs_start == 0 and tiles
                                    and regions[-1].abs_end == FLASH_BYTES),
        "regions_erase_aligned": all(r.abs_start % ERASE_BYTES == 0 and
                                     r.abs_end % ERASE_BYTES == 0 for r in regions),
    }
    plan_regions = []
    for r in regions:
        plan_regions.append({
            **asdict(r),
            "abs_bytes": r.abs_end - r.abs_start,
            "stock_mtd_map": map_to_stock(r.abs_start, r.abs_end),
        })
    # Power-fail ordering. The default writes the RECOVERY-capable nor U-Boot
    # first: the stock U-Boot cannot boot the split layout at all, while the
    # new one sets bootfail when the kernel CRC at 0x50000 fails and then runs
    # fwupd (TFTP). The legacy "bootloader last" order is kept for comparison.
    write_order = ["bootloader", "env", "firmware", "overlay"]
    write_order_alt = ["firmware", "env", "overlay", "bootloader"]
    ordering_rationale = {
        "default": "bootloader-first",
        "why": ("the stock U-Boot cannot boot the OpenIPC split layout, while the new "
                "U-Boot detects a bad kernel CRC at 0x50000, sets bootfail and runs fwupd"),
        "recovery_path": ("fwupd = tftpboot firmware.bin.hi3518ev100 over Ethernet from the "
                          "serverip/ipaddr defaults compiled into the public U-Boot "
                          "(they must be set for the LAN before relying on it)"),
        "recovery_caveat": ("an Ethernet port on the iBlack is unconfirmed (the unit is used "
                            "over Wi-Fi); without it the firmware window is recoverable "
                            "only by UART/SPI"),
        "unrecoverable_window": ("the 256 KiB bootloader write at 0x0, and any failure of "
                                 "the new U-Boot itself (untested on this board)"),
        "independent_recovery_still_required": "UART/SPI for the first unit",
    }
    crosses = sorted({m["stock_name"] for r in plan_regions for m in r["stock_mtd_map"]})
    bimg = boot_image(uboot)
    expected_erase = bimg + b"\xff" * ENV_LEN + fw + b"\xff" * (FLASH_BYTES - OVERLAY_BASE)
    return {
        "soc": SOC, "flash_bytes": FLASH_BYTES, "erase_bytes": ERASE_BYTES,
        "erase_bytes_is_assumption": True,
        "firmware": fa, "uboot_bytes": len(uboot), "env_mode": env_mode,
        "boot_image_sha256": sha256(bimg),
        "expected_flash_sha256_if_env_erased": (
            sha256(expected_erase) if len(expected_erase) == FLASH_BYTES else None),
        "final_flash_depends_on_old_contents": not inv["regions_tile_whole_chip"],
        "target_layout": [{"name": n, "start": s, "size": z} for n, s, z in TARGET],
        "regions": plan_regions, "invariants": inv,
        "write_order": write_order,
        "write_order_alt": write_order_alt,
        "ordering_rationale": ordering_rationale,
        "stock_partitions_touched": crosses,
        "private_backup_required": ["mdat1", "mdat2"],
        "flash_write_allowed": False,
        "all_ok": fa["all_ok"] and all(inv.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--firmware", type=Path, required=True)
    ap.add_argument("--uboot", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--env-mode", choices=("image", "erase"), default="image")
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing report")
    fw = read_bounded(args.firmware, FLASH_BYTES)
    uboot = read_bounded(args.uboot, 0x40000)
    plan = build_plan(fw, uboot, args.env_mode)
    plan["inputs"] = {
        "firmware_sha256": sha256(fw), "firmware_bytes": len(fw),
        "uboot_sha256": sha256(uboot), "uboot_bytes": len(uboot),
    }
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print("PASS: migration plan built; all_ok=%s; no camera/flash I/O" % plan["all_ok"])
    return 0 if plan["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
