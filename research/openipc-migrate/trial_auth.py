"""Offline contract for the small, static ARMv5 trial Wi-Fi authenticator.

The executable is built separately from public sources; never put it, a generated
configuration, or its containing runtime directory in Git.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
from pathlib import Path

SCHEMA = "openipc-trial-auth-v1"
PROFILE = "armv5-softfloat-static-event-only"
CAPABILITIES = ["nl80211", "wext", "wpa2-psk-ccmp", "fixed-events"]
EVENT_NAMES = ["CTRL-EVENT-CONNECTED", "CTRL-EVENT-DISCONNECTED",
               "CTRL-EVENT-SSID-TEMP-DISABLED", "CTRL-EVENT-ASSOC-REJECT", "CTRL-EVENT-SCAN-FAILED"]
MAX_BINARY = 1024 * 1024


class AuthError(Exception):
    pass


def _uleb(data: bytes, pos: int, end: int) -> tuple:
    value = shift = 0
    while pos < end and shift < 35:
        x = data[pos]
        pos += 1
        value |= (x & 127) << shift
        if x < 128:
            return value, pos
        shift += 7
    raise AuthError("auth runtime: malformed ARM attributes")


def _cpu_arch(data: bytes, shoff: int, entsize: int, count: int) -> int:
    """Read the aeabi file attribute Tag_CPU_arch, without invoking host tools."""
    if entsize != 40 or not count or shoff + entsize * count > len(data):
        raise AuthError("auth runtime: ARM attributes are required")
    for i in range(count):
        _, kind, _, _, off, size, *_ = struct.unpack_from("<10I", data, shoff + i * entsize)
        if kind != 0x70000003:
            continue
        end = off + size
        if end > len(data) or not size or data[off] != ord("A"):
            raise AuthError("auth runtime: malformed ARM attributes")
        pos = off + 1
        while pos < end:
            start = pos
            if pos + 4 > end:
                break
            length = struct.unpack_from("<I", data, pos)[0]
            vend_end = start + length
            if length < 5 or vend_end > end:
                break
            zero = data.find(b"\0", pos + 4, vend_end)
            if zero < 0:
                break
            vendor = data[pos + 4:zero]
            pos = zero + 1
            while vendor == b"aeabi" and pos < vend_end:
                substart = pos
                tag, pos = _uleb(data, pos, vend_end)
                if pos + 4 > vend_end:
                    break
                sublen = struct.unpack_from("<I", data, pos)[0]
                subend = substart + sublen
                pos += 4
                if subend <= pos or subend > vend_end:
                    break
                if tag == 1:
                    while pos < subend:
                        key, pos = _uleb(data, pos, subend)
                        if key in (4, 5) or (key >= 32 and key % 2):
                            z = data.find(b"\0", pos, subend)
                            if z < 0:
                                raise AuthError("auth runtime: malformed ARM attributes")
                            pos = z + 1
                        else:
                            value, pos = _uleb(data, pos, subend)
                            if key == 6:
                                return value
                            if key == 32:
                                z = data.find(b"\0", pos, subend)
                                if z < 0:
                                    raise AuthError("auth runtime: malformed ARM attributes")
                                pos = z + 1
                pos = subend
            pos = vend_end
    raise AuthError("auth runtime: missing aeabi CPU architecture")


def validate_elf(data: bytes) -> None:
    if not 84 <= len(data) <= MAX_BINARY or data[:7] != b"\x7fELF\x01\x01\x01":
        raise AuthError("auth runtime: expected a small ELF32 little-endian executable")
    (kind, machine, version, entry, phoff, shoff, flags, ehsize, phsize,
     phnum, shsize, shnum, _) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    if (kind, machine, version, ehsize, phsize) != (2, 40, 1, 52, 32):
        raise AuthError("auth runtime: expected an ARM EABI executable")
    if flags & 0xFF000000 != 0x05000000 or not flags & 0x200 or flags & 0x400:
        raise AuthError("auth runtime: ARM EABI5 soft-float is required")
    if not phnum or phoff < 52 or phoff + phnum * phsize > len(data):
        raise AuthError("auth runtime: invalid program headers")
    executable_entry = False
    for i in range(phnum):
        typ, off, va, _, filesz, memsz, perms, _ = struct.unpack_from("<8I", data, phoff + i * phsize)
        if typ in (2, 3):
            raise AuthError("auth runtime: dynamic linking or an interpreter is forbidden")
        if off + filesz > len(data) or filesz > memsz:
            raise AuthError("auth runtime: invalid load bounds")
        executable_entry |= typ == 1 and bool(perms & 1) and va <= entry < va + memsz
    if not executable_entry:
        raise AuthError("auth runtime: entry point is not executable")
    if _cpu_arch(data, shoff, shsize, shnum) not in (3, 4, 5):
        raise AuthError("auth runtime: CPU must be ARMv5, not ARMv6/ARMv7 or newer")


def _read(path: Path, secret=False) -> bytes:
    st = os.lstat(path)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
            st.st_mode & (0o077 if secret else 0o022)):
        raise AuthError("auth runtime: unsafe file ownership or permissions")
    if st.st_size > MAX_BINARY:
        raise AuthError("auth runtime: file exceeds the size limit")
    return path.read_bytes()


def runtime_files(directory: Path) -> dict:
    directory = Path(directory)
    binary = _read(directory / "wpa_supplicant")
    raw = _read(directory / "runtime.json", secret=True)
    try:
        meta = json.loads(raw)
    except (ValueError, UnicodeError):
        raise AuthError("auth runtime: malformed manifest") from None
    if (not isinstance(meta, dict) or meta.get("schema") != SCHEMA or
            meta.get("version") != "2.11" or meta.get("profile") != PROFILE or
            meta.get("capabilities") != CAPABILITIES):
        raise AuthError("auth runtime: unsupported manifest profile")
    if meta.get("bytes") != len(binary) or meta.get("sha256") != hashlib.sha256(binary).hexdigest():
        raise AuthError("auth runtime: executable differs from its manifest")
    validate_elf(binary)
    if any(name.encode("ascii") + b"\0" not in binary for name in EVENT_NAMES):
        raise AuthError("auth runtime: fixed authentication events were stripped from the executable")
    return {"wpa_supplicant": binary, "auth-runtime.json": raw,
            "auth.sha1": (hashlib.sha1(binary).hexdigest() + "\n").encode("ascii")}


def wpa_config(ssid: str, psk: str) -> bytes:
    try:
        ssid_bytes, psk_bytes = ssid.encode("ascii"), psk.encode("ascii")
    except (AttributeError, UnicodeError):
        raise AuthError("trial Wi-Fi settings must be printable ASCII") from None
    if not 1 <= len(ssid_bytes) <= 32 or any(x < 32 or x > 126 for x in ssid_bytes + psk_bytes):
        raise AuthError("invalid trial Wi-Fi settings")
    if re.fullmatch(r"[0-9a-fA-F]{64}", psk):
        key = psk.lower()
    elif 8 <= len(psk_bytes) <= 63:
        key = hashlib.pbkdf2_hmac("sha1", psk_bytes, ssid_bytes, 4096, 32).hex()
    else:
        raise AuthError("trial Wi-Fi needs an 8–63 character passphrase or 64 hex digits")
    return ("update_config=0\nap_scan=1\nnetwork={\n"
            f"    ssid={ssid_bytes.hex()}\n    scan_ssid=1\n    psk={key}\n"
            "    proto=RSN\n    key_mgmt=WPA-PSK\n    pairwise=CCMP\n"
            "    group=CCMP\n    ieee80211w=0\n}\n").encode("ascii")