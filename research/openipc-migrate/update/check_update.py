#!/usr/bin/env python3
"""Does an OpenIPC update fit a migrated iBlack/iErmu camera? Host side, read-only, nothing is sent anywhere.

  check_update.py openipc.hi3518ev100-nor-lite.tgz [--json]
  check_update.py --kernel uImage.hi3518ev100 --rootfs rootfs.squashfs.hi3518ev100 [--json]

The camera boots its vendor U-Boot with fixed slots (stock_layout.FINAL_LAYOUT): kernel 0x1D0000 bytes, rootfs
0x4E0000 bytes. An update fits when it is a plain package (uImage + squashfs for hi3518ev100, never the split
firmware.bin, which the updater writes from 0x070000 in one piece into rootfs_data), the uImage is whole (header
and data CRC) and built for hi3518ev100, and each image fits its slot. update/sysupgrade-guard.sh applies the same
limits on the camera. Exit status: 0 fits, 1 does not fit (the reasons are printed), 2 usage or read error.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import tarfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_layout as sl  # noqa: E402

SOC = "hi3518ev100"
SLOTS = {name: size for name, _off, size, _ro in sl.FINAL_LAYOUT}
KERNEL_SLOT, ROOTFS_SLOT = SLOTS["kernel"], SLOTS["rootfs"]
KNOWN_MEMBERS = {f"uImage.{SOC}", f"rootfs.squashfs.{SOC}", f"uImage.{SOC}.md5sum", f"rootfs.squashfs.{SOC}.md5sum"}


def check_kernel(data: bytes) -> dict:
    r = {"bytes": len(data), "slot": KERNEL_SLOT, "spare": KERNEL_SLOT - len(data), "problems": []}
    p = r["problems"]
    if len(data) > KERNEL_SLOT:
        p.append(f"kernel is {len(data)} bytes, {len(data) - KERNEL_SLOT} more than the kernel slot ({KERNEL_SLOT})")
    if len(data) < 64 or data[:4] != sl.UIMAGE_MAGIC:
        p.append("kernel is not a uImage")
        return r
    (_m, hcrc, _t, size, _load, _ep, dcrc, _os, _arch, _typ, _comp, name) = struct.unpack(">IIIIIIIBBBB32s", data[:64])
    r["name"] = name.rstrip(b"\0").decode("ascii", "replace")
    if zlib.crc32(data[:4] + b"\0\0\0\0" + data[8:64]) != hcrc:
        p.append("uImage header CRC is wrong")
    if 64 + size > len(data):
        p.append(f"uImage is cut short ({len(data)} of {64 + size} bytes)")
    elif zlib.crc32(data[64:64 + size]) != dcrc:
        p.append("uImage data CRC is wrong")
    if SOC not in r["name"]:
        p.append(f"uImage is built for {r['name']!r}, not {SOC}")
    return r


def check_rootfs(data: bytes) -> dict:
    r = {"bytes": len(data), "slot": ROOTFS_SLOT, "spare": ROOTFS_SLOT - len(data), "problems": []}
    p = r["problems"]
    if len(data) > ROOTFS_SLOT:
        p.append(f"rootfs is {len(data)} bytes, {len(data) - ROOTFS_SLOT} more than the rootfs slot ({ROOTFS_SLOT})")
    if len(data) < 96 or data[:4] != b"hsqs":
        p.append("rootfs is not a squashfs")
        return r
    used = struct.unpack_from("<Q", data, 40)[0]
    r["bytes_used"] = used
    if used > len(data):
        p.append(f"squashfs is cut short ({len(data)} of {used} bytes)")
    return r


def check_package(path: Path) -> dict:
    res = {"package": path.name, "problems": []}
    try:
        with tarfile.open(path, "r:gz") as tf:
            members = {m.name[2:] if m.name.startswith("./") else m.name: m for m in tf.getmembers()
                       if m.name not in (".", "./")}
            split = sorted(n for n in members if n.startswith("firmware"))
            if split:
                res["problems"].append(f"split package ({split[0]}): firmware.bin is written from 0x070000 in one "
                                       "piece and would overrun rootfs_data on this camera")
                return res
            other = sorted(set(members) - KNOWN_MEMBERS)
            if other:
                res["problems"].append(f"not a plain {SOC} package (it carries {', '.join(other[:3])})")
                return res
            for key, name, fn in (("kernel", f"uImage.{SOC}", check_kernel),
                                  ("rootfs", f"rootfs.squashfs.{SOC}", check_rootfs)):
                if name not in members:
                    res["problems"].append(f"{name} is missing")
                    continue
                res[key] = fn(tf.extractfile(members[name]).read())
    except (OSError, tarfile.TarError, EOFError, zlib.error) as e:
        raise SystemExit(f"cannot read {path}: {e}") from None
    return res


def verdict(res: dict) -> dict:
    problems = list(res["problems"])
    for key in ("kernel", "rootfs"):
        problems += res.get(key, {}).get("problems", [])
    res["fits"] = not problems
    res["all_problems"] = problems
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("package", nargs="?", type=Path, help="OpenIPC NOR package (.tgz)")
    ap.add_argument("--kernel", type=Path)
    ap.add_argument("--rootfs", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if bool(a.package) == bool(a.kernel or a.rootfs):
        ap.error("give a package, or --kernel and/or --rootfs")
    try:
        if a.package:
            res = check_package(a.package)
        else:
            res = {"problems": []}
            if a.kernel:
                res["kernel"] = check_kernel(a.kernel.read_bytes())
            if a.rootfs:
                res["rootfs"] = check_rootfs(a.rootfs.read_bytes())
    except OSError as e:
        print(f"cannot read: {e}", file=sys.stderr)
        return 2
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    res = verdict(res)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        for key in ("kernel", "rootfs"):
            if key in res:
                r = res[key]
                print(f"{key}: {r['bytes']} bytes, slot {r['slot']}, spare {r['spare']}"
                      + (f" ({r['name']})" if r.get("name") else ""))
        if res["fits"]:
            print("FITS: install it with the guard's local route (see update/README.md)")
        else:
            print("DOES NOT FIT:")
            for p in res["all_problems"]:
                print("  - " + p)
    return 0 if res["fits"] else 1


if __name__ == "__main__":
    sys.exit(main())
