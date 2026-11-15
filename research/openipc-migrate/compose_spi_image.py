#!/usr/bin/env python3
"""Compose 8 MiB SPI-programmer images for the iBlack (Hi3518EV100) when the network final cannot finish.

Offline only: nothing here contacts a camera. Two modes:

  openipc  The exact flash content the network final writes: the vendor U-Boot (0x0-0x40000) copied
           from the unit's own dump, env-final.img at 0x40000 (vendor env, boots 0x70000),
           env-openipc.img at 0x60000, firmware.bin at 0x70000, rootfs_data (0x720000-) erased.
           The result is cross-checked against a replay of the installer's write plan on the dump
           (installer/om_stockboot.sh: write0 oenv/fw0, fin_plan, env-final last).
  restore  The unit's dump, optionally with mdat1/mdat2 replaced by the backups the installer
           exported right before its point of no return.

Outputs are created 0600 and never overwritten. The printed summary holds sizes, booleans, region
names and the output SHA-256 only (env values such as the MAC address or Wi-Fi keys are never printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_layout as sl  # noqa: E402

BS = sl.ERASE
PARTS = {i: (o, s) for i, (_, o, s) in enumerate(sl.STOCK_PARTS)}      # stock mtd0..mtd5
MDAT = {"mdat1": PARTS[3], "mdat2": PARTS[4]}
# fin_plan of installer/om_stockboot.sh: (stock mtd index, first firmware block, blocks); mtd0 is per block.
FIN_MTD0 = (1, 8, 24)                      # firmware blocks 1..24 -> absolute blocks 8..31
FIN_PARTS = ((5, 81, 26), (4, 79, 2), (3, 77, 2), (2, 37, 40), (1, 25, 12))


class ComposeError(ValueError):
    pass


def need(cond, msg):
    if not cond:
        raise ComposeError(msg)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read(path, size=None, what="file") -> bytes:
    data = Path(path).read_bytes()
    need(size is None or len(data) == size, f"{what} must be exactly {size} bytes (got {len(data)})")
    return data


def uimage(buf: bytes, off: int, limit: int) -> dict:
    hdr = bytearray(buf[off:off + 64])
    if len(hdr) < 64 or bytes(hdr[:4]) != sl.UIMAGE_MAGIC:
        return {"magic": False}
    hcrc, size, dcrc = struct.unpack(">I", hdr[4:8])[0], struct.unpack(">I", hdr[12:16])[0], struct.unpack(">I", hdr[24:28])[0]
    hdr[4:8] = b"\0\0\0\0"
    fits = 64 + size <= limit
    return {"magic": True, "hcrc": zlib.crc32(bytes(hdr)) & 0xFFFFFFFF == hcrc, "fits": fits,
            "dcrc": fits and zlib.crc32(buf[off + 64:off + 64 + size]) & 0xFFFFFFFF == dcrc}


def squashfs(buf: bytes, off: int, limit: int) -> dict:
    if buf[off:off + 4] != b"hsqs":
        return {"magic": False}
    used = struct.unpack("<Q", buf[off + 40:off + 48])[0]
    return {"magic": True, "fits": 0 < used <= limit}


def check_inputs(dump, env_final, env_openipc, firmware, uboot_sha256=None) -> dict:
    need(len(dump) == sl.FLASH_BYTES, "the dump must be the full 8 MiB flash")
    if uboot_sha256:
        need(sha(dump[:sl.UBOOT_LEN]) == uboot_sha256.lower(), "the dump's U-Boot differs from the pinned U-Boot")
    try:
        fenv = dict(sl.parse_env(env_final, sl.SENV_LEN))
    except sl.LayoutError as e:
        raise ComposeError(f"env-final.img: {e}") from None
    need(fenv.get("bootcmd") == sl.FINAL_BOOTCMD, "env-final.img does not carry the final bootcmd")
    need(fenv.get("bootargs") == sl.FINAL_BOOTARGS, "env-final.img does not carry the final bootargs")
    need(fenv.get("verify") == "y", "env-final.img lacks verify=y")
    need(sl.TRIAL_MARK not in env_final.decode("latin-1"), "env-final.img carries the trial marker")
    need(len(env_openipc) == sl.OIPC_ENV_LEN and sl.env_crc_ok(env_openipc), "env-openipc.img is not a valid 64 KiB env")
    need(b"\0bootcmd=" in b"\0" + env_openipc[4:], "env-openipc.img has no bootcmd")
    need(len(firmware) == sl.FW_LEN, "firmware.bin must be the 0x6b0000-byte nor-lite-split image")
    k = uimage(firmware, 0, sl.FW_KERNEL_LEN)
    need(all(k.get(x) for x in ("magic", "hcrc", "fits", "dcrc")), "firmware.bin kernel uImage is not valid")
    r = squashfs(firmware, sl.FW_KERNEL_LEN, sl.FW_LEN - sl.FW_KERNEL_LEN)
    need(r["magic"] and r["fits"], "firmware.bin has no SquashFS rootfs at 0x1d0000")
    return {"uboot_pinned": bool(uboot_sha256), "env_final": True, "env_openipc": True,
            "kernel_uimage": True, "rootfs_squashfs": True}


def compose_openipc(dump, env_final, env_openipc, firmware) -> bytes:
    img = bytearray(b"\xff" * sl.FLASH_BYTES)
    img[:sl.UBOOT_LEN] = dump[:sl.UBOOT_LEN]
    img[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] = env_final
    img[sl.OIPC_ENV_OFF:sl.OIPC_ENV_OFF + sl.OIPC_ENV_LEN] = env_openipc
    img[sl.FW_OFF:sl.FW_OFF + sl.FW_LEN] = firmware
    return bytes(img)


def replay_final(dump, env_final, env_openipc, firmware) -> bytes:
    """Replay the installer's final write sequence on the dump (what a completed network final leaves)."""
    img = bytearray(dump)
    blk = lambda src, i: src[i * BS:(i + 1) * BS]                       # noqa: E731
    img[sl.OIPC_ENV_OFF:sl.OIPC_ENV_OFF + BS] = env_openipc             # write0 O 0 OENV_BLK 1
    img[sl.FW_OFF:sl.FW_OFF + BS] = blk(firmware, 0)                    # write0 F 0 FW_BLK 1
    s, p, n = FIN_MTD0                                                  # fin0 F 1 8 24
    for k in range(n):
        img[(p + k) * BS:(p + k + 1) * BS] = blk(firmware, s + k)
    for idx, s, n in FIN_PARTS:                                         # finp: erase whole, write n blocks
        off, size = PARTS[idx]
        img[off:off + size] = b"\xff" * size
        for k in range(n):
            img[off + k * BS:off + (k + 1) * BS] = blk(firmware, s + k)
    img[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] = env_final              # env-final LAST
    return bytes(img)


def check_openipc_image(img, dump) -> dict:
    need(len(img) == sl.FLASH_BYTES, "image size")
    need(img[:sl.UBOOT_LEN] == dump[:sl.UBOOT_LEN], "U-Boot region changed")
    need(img[sl.SPLIT_PROBE_OFF:sl.SPLIT_PROBE_OFF + 4] != sl.UIMAGE_MAGIC, "uImage magic at the 0x50000 split probe")
    k = uimage(img, sl.FW_OFF, sl.FW_KERNEL_LEN)
    need(all(k.get(x) for x in ("magic", "hcrc", "fits", "dcrc")), "kernel uImage at 0x70000")
    r = squashfs(img, sl.FW_OFF + sl.FW_KERNEL_LEN, sl.FW_LEN - sl.FW_KERNEL_LEN)
    need(r["magic"] and r["fits"], "SquashFS at 0x240000")
    need(img[sl.OVERLAY_OFF:] == b"\xff" * sl.OVERLAY_LEN, "rootfs_data must be erased")
    return {"uboot_unchanged": True, "no_split_probe_header": True, "kernel_at_0x70000": True,
            "squashfs_at_0x240000": True, "rootfs_data_erased": True}


def compose_restore(dump, mdat1=None, mdat2=None) -> tuple:
    img = bytearray(dump)
    replaced = []
    for name, data in (("mdat1", mdat1), ("mdat2", mdat2)):
        if data is None:
            continue
        off, size = MDAT[name]
        need(len(data) == size, f"{name} backup must be exactly {size} bytes")
        if bytes(img[off:off + size]) != data:
            replaced.append(name)
        img[off:off + size] = data
    return bytes(img), replaced


def write_private(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    need(Path(path).read_bytes() == data, "read-back of the written image differs")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("openipc", "restore"))
    ap.add_argument("--dump", required=True, help="the unit's full 8 MiB SPI dump")
    ap.add_argument("--out", required=True, help="output image (created 0600, never overwritten)")
    ap.add_argument("--uboot-sha256", help="pin: SHA-256 of the dump's U-Boot (0x0-0x40000)")
    ap.add_argument("--env-final"); ap.add_argument("--env-openipc"); ap.add_argument("--firmware")
    ap.add_argument("--mdat1"); ap.add_argument("--mdat2")
    a = ap.parse_args(argv)
    try:
        dump = read(a.dump, sl.FLASH_BYTES, "the dump")
        if a.uboot_sha256:
            need(sha(dump[:sl.UBOOT_LEN]) == a.uboot_sha256.lower(), "the dump's U-Boot differs from the pinned U-Boot")
        out = {"mode": a.mode, "bytes": sl.FLASH_BYTES, "uboot_sha256_matches_pin": bool(a.uboot_sha256)}
        if a.mode == "openipc":
            need(a.env_final and a.env_openipc and a.firmware, "openipc needs --env-final, --env-openipc and --firmware")
            ef, eo, fw = read(a.env_final), read(a.env_openipc), read(a.firmware)
            out["inputs"] = check_inputs(dump, ef, eo, fw, a.uboot_sha256)
            img = compose_openipc(dump, ef, eo, fw)
            out["image"] = check_openipc_image(img, dump)
            need(img == replay_final(dump, ef, eo, fw), "composition differs from the installer replay")
            out["equals_installer_replay"] = True
        else:
            img, replaced = compose_restore(dump, read(a.mdat1) if a.mdat1 else None, read(a.mdat2) if a.mdat2 else None)
            out["mdat_replaced"] = replaced
            out["identical_to_dump"] = img == dump
        write_private(Path(a.out), img)
        out["out"] = Path(a.out).name
        out["sha256"] = sha(img)
    except (ComposeError, OSError) as e:
        print(f"compose_spi_image: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
