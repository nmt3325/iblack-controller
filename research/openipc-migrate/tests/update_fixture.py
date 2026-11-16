"""Synthetic OpenIPC update images for the guard and check_update tests (no real firmware needed)."""
from __future__ import annotations

import io
import struct
import tarfile
import zlib

SOC = "hi3518ev100"


def uimage(size: int, name: str = "Linux-3.0.8-hi3518ev100", data_len: int | None = None, magic=0x27051956,
           bad_dcrc: bool = False) -> bytes:
    """A legacy uImage of SIZE bytes: 64-byte header (valid CRCs) and SIZE-64 data bytes."""
    data = bytes((i * 7) & 0xFF for i in range(size - 64))
    data_len = len(data) if data_len is None else data_len
    dcrc = zlib.crc32(data[:data_len]) ^ (1 if bad_dcrc else 0)
    fields = [magic, 0, 0, data_len, 0x80008000, 0x80008000, dcrc, 5, 2, 2, 0, name.encode()]
    hcrc = zlib.crc32(struct.pack(">IIIIIIIBBBB32s", *fields))
    fields[1] = hcrc
    return struct.pack(">IIIIIIIBBBB32s", *fields) + data


def squashfs(size: int, used: int | None = None, magic=b"hsqs") -> bytes:
    """A squashfs 4.0 superblock (xz, bytes_used=USED) padded to SIZE bytes."""
    used = size - 3000 if used is None else used
    sb = bytearray(96)
    sb[0:4] = magic
    sb[20:22] = struct.pack("<H", 4)
    sb[28:30] = struct.pack("<H", 4)  # major version 4
    sb[40:48] = struct.pack("<Q", used)
    return bytes(sb) + b"\xa5" * (size - 96)


def tgz(members: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uname, info.gname = len(data), 0o644, "runner", "runner"
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def plain(kernel: bytes, rootfs: bytes, extra: dict | None = None) -> bytes:
    """A plain NOR package as OpenIPC publishes it (md5sum files first)."""
    import hashlib
    m = {f"uImage.{SOC}.md5sum": f"{hashlib.md5(kernel).hexdigest()}  uImage.{SOC}\n".encode(),
         f"rootfs.squashfs.{SOC}.md5sum": f"{hashlib.md5(rootfs).hexdigest()}  rootfs.squashfs.{SOC}\n".encode(),
         f"uImage.{SOC}": kernel, f"rootfs.squashfs.{SOC}": rootfs}
    m.update(extra or {})
    return tgz(m)
