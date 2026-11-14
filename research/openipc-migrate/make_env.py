#!/usr/bin/env python3
"""Build the 64 KiB OpenIPC environment image for the migration.

--layout stockboot (default, the Wi-Fi-only iBlack plan of stock_layout.py):
the image is written at 0x60000, the "env" partition of the final layout. The
vendor U-Boot never reads it (it keeps its own env at 0x40000); the OpenIPC
userland does (fw_printenv: wlandev/wlanssid/wlanpass, osmem, ...), so the
layout keys (mtdparts, kernaddr/kernsize, rootaddr/rootsize, osmem) and
bootcmd/bootargs are replaced by the values the vendor env really boots with.
--layout openipc-uboot (legacy plan, NOT for a unit without UART): the image
is the env of the OpenIPC U-Boot at 0x40000 with its own default layout.

Offline only. Reads the pinned OpenIPC nor U-Boot, decompresses its LZMA
payload, extracts the built-in default environment (later duplicates win, as
in U-Boot's own import), applies a small allowlist of additions -- wlandev
plus the Wi-Fi keys read from a PRIVATE key=value file -- and writes
CRC32(LE) + data, NUL-padded to 64 KiB, into a private directory OUTSIDE the
repository. U-Boot replaces its whole environment when the CRC is valid, so
the image must carry the full default set, not only the additions.

Why: erasing the env leaves the OpenIPC network scripts without wlandev /
wlanssid / wlanpass, so a unit that is only reachable over Wi-Fi would come
up unreachable after the migration.

Secret values are never printed or written to the summary (key names, sizes,
CRC status and the image sha256 only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import stat
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_layout  # noqa: E402

PINNED_UBOOT_SHA256 = "1e76dac4f843ba1f20767a3b9b0e2d4a6b553bca0550e4baf091c4a147ca1cd3"
LZMA_OFFSET = 0x3264          # LZMA-alone payload inside the pinned nor U-Boot
ENV_SIZE = 0x10000            # 64 KiB env sector (non-redundant)
LAYOUTS = ("stockboot", "openipc-uboot")
STOCKBOOT_OVERRIDES = {**stock_layout.OIPC_ENV_LAYOUT, "bootcmd": stock_layout.FINAL_BOOTCMD,
                       "bootargs": stock_layout.FINAL_BOOTARGS}
DEFAULT_WLANDEV = "mt7601u-hi3518ev100-camhi"
ALLOWED_KEYS = ("wlandev", "wlanssid", "wlanpass", "wlanmac", "ethaddr", "netaddr_fallback")
SECRET_KEYS = ("wlanssid", "wlanpass")
REQUIRED_DEFAULT_KEYS = ("bootcmd", "bootdelay", "baudrate", "ipaddr", "serverip", "mtdparts")


class EnvError(Exception):
    pass


def repo_root() -> Path:
    p = Path(__file__).resolve().parent
    for cand in (p, *p.parents):
        if (cand / ".git").exists():
            return cand
    return p.parents[1]


def decompress_uboot(raw: bytes, offset: int = LZMA_OFFSET) -> bytes:
    try:
        out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(raw[offset:])
    except lzma.LZMAError as exc:
        raise EnvError(f"no LZMA payload at 0x{offset:x}: {exc}") from exc
    if not out:
        raise EnvError(f"empty LZMA payload at 0x{offset:x}")
    return out


def _parse_block(blob: bytes, start: int):
    end = blob.find(b"\0\0", start)
    if end < 0:
        return None
    entries = []
    for raw in blob[start:end].split(b"\0"):
        if not raw or not all(0x20 <= c < 0x7f for c in raw):
            return None
        k, _, v = raw.partition(b"=")
        if not k:
            return None
        # A bare "name" or "name=" is legal: U-Boot's himport_r deletes it.
        entries.append((k.decode(), v.decode()))
    return entries


def extract_default_env(blob: bytes) -> list:
    """Return the ordered (key, value) list of the built-in default env."""
    cands = []
    pos = blob.find(b"\0bootcmd=")
    while pos >= 0:
        entries = _parse_block(blob, pos + 1)
        if entries and len(entries) >= 10 and {k for k, _ in entries} >= set(REQUIRED_DEFAULT_KEYS):
            cands.append(entries)
        pos = blob.find(b"\0bootcmd=", pos + 1)
    if len(cands) != 1:
        raise EnvError(f"expected exactly one default environment, found {len(cands)}")
    return cands[0]


def effective(entries: list) -> tuple:
    """Apply U-Boot himport_r semantics in order: a later duplicate replaces the
    value, and a bare "name" / empty "name=" deletes the variable.
    Returns (entries, duplicate_keys, deletion_keys)."""
    values, seen, dups, deletions = {}, set(), set(), set()
    for k, v in entries:
        if k in seen:
            dups.add(k)
        seen.add(k)
        if v == "":
            values.pop(k, None)
            deletions.add(k)
        else:
            values[k] = v
    return list(values.items()), sorted(dups), sorted(deletions)


def parse_private_vars(path: Path) -> dict:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
        raise EnvError("private vars must be a small regular file")
    if st.st_mode & 0o077:
        raise EnvError("private vars file must not be group/world accessible (chmod 600)")
    out = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvError(f"line {n}: expected key=value")
        k, v = line.split("=", 1)
        k = k.strip()
        if k not in ALLOWED_KEYS:
            raise EnvError(f"line {n}: key '{k}' is not in the allowlist {ALLOWED_KEYS}")
        if not v or not all(0x20 <= ord(c) < 0x7f for c in v):
            raise EnvError(f"line {n}: value for '{k}' must be non-empty printable ASCII")
        out[k] = v
    ssid, psk = out.get("wlanssid"), out.get("wlanpass")
    if ssid is not None and len(ssid.encode()) > 32:
        raise EnvError("wlanssid longer than 32 bytes")
    is_hex64 = psk is not None and len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk)
    if psk is not None and not (8 <= len(psk) <= 63 or is_hex64):
        raise EnvError("wlanpass must be 8..63 characters or 64 hex digits")
    return out


def build_env_image(entries: list, additions: dict, layout: str = "openipc-uboot") -> bytes:
    if layout not in LAYOUTS:
        raise EnvError(f"unknown layout {layout!r}")
    merged, _, _ = effective(list(entries) + sorted(additions.items()))
    if layout == "stockboot":
        merged = stock_layout.replace_values(merged, STOCKBOOT_OVERRIDES)
    data = b"".join(f"{k}={v}".encode() + b"\0" for k, v in merged) + b"\0"
    if len(data) > ENV_SIZE - 4:
        raise EnvError("environment does not fit into 64 KiB")
    data += b"\0" * (ENV_SIZE - 4 - len(data))
    return struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF) + data


def parse_env_image(img: bytes) -> dict:
    if len(img) != ENV_SIZE:
        raise EnvError("env image must be exactly 64 KiB")
    crc, data = struct.unpack("<I", img[:4])[0], img[4:]
    if zlib.crc32(data) & 0xFFFFFFFF != crc:
        raise EnvError("env image CRC mismatch")
    entries = _parse_block(data, 0)
    if entries is None:
        raise EnvError("env image data is malformed")
    return dict(entries)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uboot", type=Path, required=True)
    ap.add_argument("--uboot-sha256", default=PINNED_UBOOT_SHA256)
    ap.add_argument("--private-vars", type=Path, help="key=value file (0600) with wlanssid/wlanpass etc.")
    ap.add_argument("--wlandev", default=DEFAULT_WLANDEV)
    ap.add_argument("--allow-no-wifi", action="store_true", help="permit an image without wlanssid/wlanpass")
    ap.add_argument("--layout", choices=LAYOUTS, default="stockboot",
                    help="stockboot: env partition 0x60000 of the stock-U-Boot plan (default)")
    ap.add_argument("--output-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        raw = args.uboot.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.uboot_sha256:
            raise EnvError("U-Boot sha256 does not match the pin")
        out = args.output_dir.resolve()
        root = repo_root().resolve()
        if out == root or root in out.parents:
            raise EnvError("refusing to write the env image inside the repository")
        additions = {"wlandev": args.wlandev}
        if args.private_vars:
            additions.update(parse_private_vars(args.private_vars))
        if not all(k in additions for k in SECRET_KEYS) and not args.allow_no_wifi:
            raise EnvError("wlanssid/wlanpass missing: the unit would be unreachable over Wi-Fi "
                           "(pass --allow-no-wifi only if Ethernet is available)")
        defaults = extract_default_env(decompress_uboot(raw))
        base, dups, deletions = effective(defaults)
        img = build_env_image(defaults, additions, args.layout)
        parsed = parse_env_image(img)
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        stem = "env-openipc" if args.layout == "stockboot" else "env"
        img_path, sum_path = out / f"{stem}.img", out / f"{stem}-summary.json"
        if img_path.exists() or sum_path.exists():
            raise EnvError("refusing to overwrite an existing env image/summary")
        fd = os.open(img_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(img)
        summary = {
            "uboot_sha256": args.uboot_sha256, "layout": args.layout, "env_bytes": len(img),
            "layout_keys_replaced": sorted(STOCKBOOT_OVERRIDES) if args.layout == "stockboot" else [],
            "env_sha256": hashlib.sha256(img).hexdigest(), "crc_ok": True,
            "default_keys": len(base), "default_duplicates_resolved": dups,
            "default_deletion_entries": deletions,
            "added_or_overridden_keys": sorted(additions),
            "secret_keys_present": {k: k in additions for k in SECRET_KEYS},
            "total_keys": len(parsed), "wlandev": parsed.get("wlandev"),
        }
        sum_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        os.chmod(sum_path, 0o600)
    except (EnvError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: env image written ({summary['total_keys']} keys, sha256 {summary['env_sha256']}); "
          "secrets not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
