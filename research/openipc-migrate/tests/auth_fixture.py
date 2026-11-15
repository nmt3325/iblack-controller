"""Synthetic ARM ELF for OFFLINE builder tests; it is never executed."""
import hashlib
import json
import struct
from pathlib import Path

import trial_auth as ta


def elf(cpu_arch=5) -> bytes:
    attrs = b"\x06" + bytes([cpu_arch])
    sub = b"\x01" + struct.pack("<I", len(attrs) + 5) + attrs
    vendor = b"aeabi\0" + sub
    arm = b"A" + struct.pack("<I", 4 + len(vendor)) + vendor
    data = bytearray(768)
    data[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 2, 40, 1, 0x10080, 52, 256,
                     0x05000200, 52, 32, 1, 40, 2, 0)
    struct.pack_into("<8I", data, 52, 1, 0, 0x10000, 0x10000, len(data), len(data), 5, 4096)
    struct.pack_into("<10I", data, 296, 0, 0x70000003, 0, 0, 384, len(arm), 0, 0, 1, 0)
    data[384:384 + len(arm)] = arm
    # The patch's name table, then one call-site format prefix per event (name and a space).
    markers = b"".join(x.encode("ascii") + b"\0" for x in ta.EVENT_NAMES)
    markers += b"".join(x.encode("ascii") + b" \0" for x in ta.EVENT_NAMES)
    assert 512 + len(markers) <= len(data)
    data[512:512 + len(markers)] = markers
    return bytes(data)


def runtime(path: Path) -> Path:
    path.mkdir(mode=0o700, exist_ok=True)
    binary = elf()
    (path / "wpa_supplicant").write_bytes(binary)
    (path / "wpa_supplicant").chmod(0o755)
    meta = {"schema": ta.SCHEMA, "profile": ta.PROFILE, "version": "2.11",
            "capabilities": ta.CAPABILITIES, "bytes": len(binary),
            "sha256": hashlib.sha256(binary).hexdigest(),
            "source": "synthetic offline test fixture; not runnable"}
    (path / "runtime.json").write_text(json.dumps(meta) + "\n")
    (path / "runtime.json").chmod(0o600)
    return path