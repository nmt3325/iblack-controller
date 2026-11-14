#!/usr/bin/env python3
"""Validate pinned OpenIPC inputs and compute a DRY-RUN NOR migration map.

This is not a flasher: no sockets, subprocesses, erase/write ioctls or /dev
access. The only writable output is a new sanitized JSON report.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import zlib

FLASH_BYTES = 0x800000
ERASE_BYTES = 0x10000  # Model assumption; must be measured on the camera.
BOOT_BYTES = 0x40000
ENV_START = 0x40000
ENV_BYTES = 0x10000
FIRMWARE_START = 0x50000
MAX_KERNEL_BYTES = 0x200000
PINNED_FIRMWARE_SHA256 = "2e268c99608c4dcd4867eda8c35f9393dd4832cf8da05cc31af571bbd3457926"
PINNED_BOOTLOADER_SHA256 = "abc0948bcf6e7b5c9089188d5d9098b5a7e87713fa6cf4a7a65c3eaf0ad271f0"
SPLIT_LAYOUT_SOURCE = "https://github.com/OpenIPC/wiki/blob/master/en/nor-split-layout.md"


@dataclass(frozen=True)
class Partition:
    index: int
    name: str
    start: int
    size: int

    @property
    def end(self) -> int:
        return self.start + self.size


VENDOR_PARTITIONS = (
    Partition(0, "boot", 0x000000, 0x200000),
    Partition(1, "rootfs", 0x200000, 0x0C0000),
    Partition(2, "appfs", 0x2C0000, 0x280000),
    Partition(3, "mdat1", 0x540000, 0x020000),
    Partition(4, "mdat2", 0x560000, 0x020000),
    Partition(5, "bakfs", 0x580000, 0x280000),
)


def align_up(value: int, alignment: int = ERASE_BYTES) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("A nonnegative integer is required")
    if not isinstance(alignment, int) or isinstance(alignment, bool) or alignment <= 0:
        raise ValueError("A positive alignment is required")
    return ((value + alignment - 1) // alignment) * alignment


def vendor_segments(start: int, length: int) -> list[dict]:
    if any(not isinstance(x, int) or isinstance(x, bool) for x in (start, length)):
        raise ValueError("Byte offsets and lengths must be integers")
    if start < 0 or length < 0 or start + length > FLASH_BYTES:
        raise ValueError("Requested range is outside the 8 MiB flash model")
    end = start + length
    result = []
    for p in VENDOR_PARTITIONS:
        lo, hi = max(start, p.start), min(end, p.end)
        if hi > lo:
            result.append({"vendor_mtd_index": p.index, "vendor_name": p.name,
                           "flash_offset": lo, "mtd_local_offset": lo - p.start,
                           "payload_offset": lo - start, "bytes": hi - lo})
    if sum(x["bytes"] for x in result) != length:
        raise ValueError("Partition map has a gap or overlap")
    return result


def inspect_split_image(data: bytes) -> dict:
    if not 64 <= len(data) <= FLASH_BYTES - FIRMWARE_START:
        raise ValueError("Invalid firmware length")
    fields = struct.unpack(">7I4B32s", data[:64])
    magic, header_crc, _, payload_bytes, load, entry, data_crc = fields[:7]
    os_id, arch, image_type, compression = fields[7:11]
    if magic != 0x27051956:
        raise ValueError("Missing uImage header")
    header = bytearray(data[:64])
    header[4:8] = bytes(4)
    if zlib.crc32(header) & 0xFFFFFFFF != header_crc:
        raise ValueError("uImage header CRC mismatch")
    kernel_bytes = 64 + payload_bytes
    if kernel_bytes > MAX_KERNEL_BYTES or kernel_bytes > len(data):
        raise ValueError("Kernel exceeds the supported 2 MiB slot/read limit")
    if zlib.crc32(data[64:kernel_bytes]) & 0xFFFFFFFF != data_crc:
        raise ValueError("uImage data CRC mismatch")
    if (os_id, arch, image_type) != (5, 2, 2):
        raise ValueError("Expected a Linux ARM kernel uImage")
    rootfs_offset = align_up(kernel_bytes)
    if rootfs_offset + 96 > len(data):
        raise ValueError("Missing aligned squashfs superblock")
    sq = data[rootfs_offset:rootfs_offset + 96]
    if sq[:4] != b"hsqs":
        raise ValueError("Missing squashfs after the aligned kernel")
    block_bytes = struct.unpack_from("<I", sq, 12)[0]
    squashfs_compression = struct.unpack_from("<H", sq, 20)[0]
    major, minor = struct.unpack_from("<HH", sq, 28)
    bytes_used = struct.unpack_from("<Q", sq, 40)[0]
    if (major, minor) != (4, 0):
        raise ValueError("Expected squashfs 4.0")
    if not 4096 <= block_bytes <= 1048576 or block_bytes & (block_bytes - 1):
        raise ValueError("Invalid squashfs block size")
    if not 96 <= bytes_used <= len(data) - rootfs_offset:
        raise ValueError("Squashfs size exceeds the firmware")
    aligned_image_end = align_up(rootfs_offset + bytes_used)
    if len(data) != aligned_image_end:
        raise ValueError("Image padding differs from the 64 KiB split model")
    rootfs_data_start = FIRMWARE_START + aligned_image_end
    if rootfs_data_start >= FLASH_BYTES:
        raise ValueError("No overlay remains")
    return {"firmware_bytes": len(data), "firmware_sha256": hashlib.sha256(data).hexdigest(),
            "kernel_uimage_bytes": kernel_bytes, "kernel_compression_id": compression,
            "kernel_load_address": load, "kernel_entry_address": entry,
            "uimage_header_crc_valid": True, "uimage_data_crc_valid": True,
            "rootfs_offset_within_firmware": rootfs_offset,
            "rootfs_squashfs_bytes_used": bytes_used, "rootfs_block_bytes": block_bytes,
            "rootfs_compression_id": squashfs_compression,
            "kernel_flash_start": FIRMWARE_START,
            "rootfs_flash_start": FIRMWARE_START + rootfs_offset,
            "rootfs_data_flash_start": rootfs_data_start,
            "overlay_bytes": FLASH_BYTES - rootfs_data_start}


def boot_defaults(data: bytes, kernel_bytes: int) -> dict:
    if not 4096 <= len(data) <= BOOT_BYTES:
        raise ValueError("Bootloader does not fit the boot area")
    strings = re.findall(rb"[\x20-\x7e]{7,}", data)
    props = {}
    for raw in strings:
        for name in ("bootcmd", "bootargs", "mtdparts", "osmem", "totalmem", "baseaddr"):
            if raw.startswith((name + "=").encode()):
                props[name] = raw.decode().split("=", 1)[1]
    command = props.get("bootcmd", "")
    match = re.search(r"sf read\s+\S+\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)", command)
    if not match:
        raise ValueError("Cannot verify the bootloader's kernel read command")
    offset, length = (int(x, 16) for x in match.groups())
    if offset != FIRMWARE_START or length < kernel_bytes:
        raise ValueError("Bootloader reads the wrong offset or too little kernel data")
    return {"bootloader_bytes": len(data), "padded_boot_area_bytes": BOOT_BYTES,
            "bootloader_sha256": hashlib.sha256(data).hexdigest(),
            "kernel_read_offset": offset, "kernel_read_bytes": length,
            "public_default_bootcmd": command, "public_default_bootargs": props.get("bootargs"),
            "public_default_mtdparts": props.get("mtdparts"),
            "public_default_osmem": props.get("osmem"),
            "fixed_root_argument_alone_is_not_a_split_boot_failure": True,
            "reason": "The split-capable kernel discovers partitions/root itself; see upstream documentation.",
            "source": SPLIT_LAYOUT_SOURCE,
            "this_tool_does_not_emulate_kernel_partition_registration": True}


def build_plan(firmware: bytes, bootloader: bytes) -> dict:
    image = inspect_split_image(firmware)
    boot = boot_defaults(bootloader, image["kernel_uimage_bytes"])
    overlay_start = image["rootfs_data_flash_start"]
    return {"mode": "OFFLINE_DRY_RUN_ONLY", "flash_bytes": FLASH_BYTES,
            "assumed_erase_bytes": ERASE_BYTES, "physical_erase_geometry_measured": False,
            "target_soc": "hi3518ev100", "physical_soc_marking_confirmed": False,
            "image": image, "bootloader": boot,
            "vendor_environment": {"start": 0x40000, "bytes": 0x20000,
                                   "overlap_with_new_firmware_bytes": 0x10000},
            "openipc_environment": {"start": ENV_START, "bytes": ENV_BYTES},
            "firmware_program_map": vendor_segments(FIRMWARE_START, len(firmware)),
            "overlay_initialization_map": vendor_segments(overlay_start, FLASH_BYTES - overlay_start),
            "environment_program_map": vendor_segments(ENV_START, ENV_BYTES),
            "bootloader_program_map": vendor_segments(0, BOOT_BYTES),
            "vendor_private_partitions_overwritten": ["mdat1", "mdat2"],
            "all_copies_must_be_staged_before_destructive_work": True,
            "minimum_binary_staging_bytes_excluding_ramroot_and_runtime": len(firmware) + BOOT_BYTES + ENV_BYTES,
            "conceptual_order_not_an_atomic_upgrade": [
                "stage/hash all inputs in RAM and protect off-camera backups",
                "enter verified RAM-root runtime with watchdog ownership",
                "program/verify firmware across vendor MTD boundaries",
                "initialize/configure overlay without factory leftovers",
                "provision/verify 64 KiB OpenIPC environment privately",
                "program/verify compatible bootloader LAST",
                "reboot only after every check and explicit authorization"],
            "unresolved_deployment_gates": [
                "repeated real bakfs readback inconsistency",
                "physical SoC identification and new bootloader board test",
                "measured MTD erase size/write protection",
                "measured available RAM and complete RAM-root transition",
                "watchdog owner handoff/power-fail recovery",
                "AR0141 sensor ini and MT7601 private network configuration",
                "independent verified backup/recovery path"],
            "network_connections": 0, "real_device_writes": 0, "real_reboots": 0,
            "flash_write_allowed": False}


def read_regular(path: Path, limit: int) -> bytes:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
        raise ValueError("Only bounded regular-file inputs are accepted; no devices")
    return path.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--bootloader", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Refusing to replace an existing report")
        firmware = read_regular(args.firmware, FLASH_BYTES)
        bootloader = read_regular(args.bootloader, BOOT_BYTES)
        if hashlib.sha256(firmware).hexdigest() != PINNED_FIRMWARE_SHA256:
            raise ValueError("Firmware differs from the pinned research artifact")
        if hashlib.sha256(bootloader).hexdigest() != PINNED_BOOTLOADER_SHA256:
            raise ValueError("Bootloader differs from the pinned research artifact")
        result = build_plan(firmware, bootloader)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("PASS: pinned input integrity and cross-MTD map; deployment remains BLOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
