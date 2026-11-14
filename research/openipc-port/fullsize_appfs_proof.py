#!/usr/bin/env python3
"""Offline-only, full-size SysUpdate 0x96 proof. Never emits an update image.

All firmware/MTD/socket/system/reboot operations run inside the pre-existing
Unicorn harness. This file has no network client and no physical-device writer.
The legacy pack_update.py size/opcode guards are intentionally not changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import time

MTD_BYTES = 0x280000
EXPECTED_IMAGE_SIZE = 2609152
EXPECTED_IMAGE_SHA256 = "a75982a0c1e1f61fe3e3c305df8afdd9c12eda52f7fc148a2a7567b1f040531f"
EXPECTED_ELF_SHA256 = "c76e218d9dffde035ca5ed28cae89fa2dd679c539f647f6329f8dcf083e0c6d9"
SOURCE_REVISION = "4d7a5cff36045cfeffb823570ae0a9813f7c5d20"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, required=True, help="Pinned stock SysUpdate ELF")
    parser.add_argument("--image", type=Path, required=True, help="Pinned private diagnostic squashfs")
    parser.add_argument("--output", type=Path, required=True, help="Sanitized JSON report only")
    args = parser.parse_args()
    os.umask(0o077)
    def bounded_input(path, maximum):
        st = path.stat()
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= maximum:
            parser.error("Only bounded regular-file research inputs are accepted")
        return path.read_bytes()

    elf = bounded_input(args.firmware, 8 * 1024 * 1024)
    image = bounded_input(args.image, MTD_BYTES)
    if sha256(elf) != EXPECTED_ELF_SHA256:
        parser.error("Stock ELF fingerprint differs; no emulation performed")
    if len(image) != EXPECTED_IMAGE_SIZE or sha256(image) != EXPECTED_IMAGE_SHA256:
        parser.error("Diagnostic image fingerprint differs; no envelope generated")
    if args.output.exists():
        parser.error("Refusing to overwrite an existing report")
    os.environ["IERMU_FIRMWARE"] = str(args.firmware.resolve())
    tools = Path(__file__).resolve().parent.parent / "sysupdate-offline"
    sys.path.insert(0, str(tools))
    from firmware_math import crc32, make_nonce
    from pack_update import Packer, header
    from offline_appfs_proof import AppUpdateVM

    # A fixed ARMv5 wrapper calls only one of the two already-tested firmware
    # ECB primitives. It runs 4 KiB per emu_start, avoiding 163k Python call
    # boundaries for a 2.6 MB image. No CLI-supplied address/code is accepted.
    # r4/r5/r6/r7 hold ctx/src/dst/end; r8 holds the host sentinel return PC.
    words = (
        0xE1A0800E, 0xE1A04000, 0xE1A05001, 0xE1A06002, 0xE0817003,
        0xE1A00004, 0xE1A01005, 0xE1A02006, 0xE59FC014, 0xE12FFF3C,
        0xE2855010, 0xE2866010, 0xE1550007, 0x3AFFFFF6, 0xE12FFF18,
    )

    def bulk_ecb(packer, nonce: bytes, data: bytes, decrypt: bool = False) -> bytes:
        if len(nonce) != 16 or not data or len(data) % 16:
            raise ValueError("Bounded whole-block ECB input required")
        if len(data) > MTD_BYTES + 32:
            raise ValueError("Offline ECB image bound exceeded")
        m = packer.m
        ctx = m.call(0x16CA4)
        nonce_ptr = m.alloc(17, nonce + b"\0")
        if m.call(0x16B00, ctx, nonce_ptr) != 0:
            raise RuntimeError("Firmware nonce setup failed")
        chunk_size = 4096
        src, dst = m.alloc(chunk_size), m.alloc(chunk_size)
        primitive = 0x163A8 if decrypt else 0x15C54
        code = m.alloc(64, struct.pack("<16I", *words, primitive))
        reg = m.reg
        out = bytearray()
        for off in range(0, len(data), chunk_size):
            chunk = data[off:off + chunk_size]
            m.uc.mem_write(src, chunk)
            m.failure = None
            for rr, value in (
                (reg.UC_ARM_REG_CPSR, 0x10),
                (reg.UC_ARM_REG_SP, 0x7F01F000),
                (reg.UC_ARM_REG_LR, m.sentinel),
                (reg.UC_ARM_REG_R0, ctx),
                (reg.UC_ARM_REG_R1, src),
                (reg.UC_ARM_REG_R2, dst),
                (reg.UC_ARM_REG_R3, len(chunk)),
            ):
                m.uc.reg_write(rr, value)
            m.uc.emu_start(code, m.sentinel, timeout=5_000_000, count=2_000_000)
            if m.failure:
                raise m.failure
            if m.uc.reg_read(reg.UC_ARM_REG_PC) != m.sentinel:
                raise RuntimeError("Fixed offline ECB wrapper did not return")
            out.extend(m.uc.mem_read(dst, len(chunk)))
        return bytes(out)

    packer = Packer(now=1700000000)
    nonce = make_nonce(1700000000)  # Synthetic, never printed or persisted.
    samples = (
        bytes(16), bytes(range(16)), bytes([255]) * 64,
        bytes((i * 37 + 11) % 256 for i in range(4096)), image[:8192],
    )
    for sample in samples:
        reference = packer.ecb(nonce, sample)
        encoded = bulk_ecb(packer, nonce, sample)
        if encoded != reference or bulk_ecb(packer, nonce, encoded, True) != sample:
            raise RuntimeError("Bulk wrapper differs from legacy firmware ECB")
    print("PASS: fixed ARM wrapper matches legacy ECB; no network or camera I/O", flush=True)

    def envelope(payload: bytes, corrupt_plain_crc: bool = False):
        # This builder is deliberately nested in an emulator-only main, returns
        # memory objects, and has no export/send option. Oversized cases below
        # exercise only virtual devices, not a deployment format generator.
        if not 0 < len(payload) <= MTD_BYTES + 1:
            raise ValueError("Virtual boundary test exceeds its bound")
        padded = payload + bytes((-(len(payload) + 4)) % 16)
        plain = padded + struct.pack(">I", crc32(padded) ^ int(corrupt_plain_crc))
        ct = bulk_ecb(packer, nonce, plain)
        body = struct.pack(">III", 0x01000001, len(payload), 5) + bytes(16)
        body += struct.pack(">I", crc32(ct)) + ct
        return header(0x96, nonce, len(body)), body, plain, ct

    started = time.monotonic()
    head, body, plain, ciphertext = envelope(image)
    if bulk_ecb(packer, nonce, ciphertext, True) != plain:
        raise RuntimeError("Full-size firmware encryption/decryption mismatch")
    print("PASS: full-size diagnostic envelope round trip in memory", flush=True)

    class FullSizeVM(AppUpdateVM):
        def __init__(self):
            self.allocation_sizes = []
            self.bounds_violation = None
            super().__init__()

        def _hook(self, uc, address, size, user):
            # Record the original descriptor before AppUpdateVM redirects it to
            # a strict, in-memory MTD. Never relax the parent bounds check.
            if address == 0x8A7C:
                fd = uc.reg_read(self.reg.UC_ARM_REG_R0)
                length = uc.reg_read(self.reg.UC_ARM_REG_R2)
                if fd in self.parts:
                    pos, capacity = self.positions[fd], len(self.parts[fd])
                    if not 0 <= pos <= pos + length <= capacity:
                        self.bounds_violation = {
                            "operation": "write", "virtual_fd": fd,
                            "partition": {33: "appfs", 34: "bakfs"}[fd],
                            "offset": pos, "requested_bytes": length,
                            "partition_bytes": capacity,
                            "bytes_beyond_partition": max(0, pos + length - capacity),
                        }
            return super()._hook(uc, address, size, user)

        def alloc(self, size, data=b""):
            if not 0 <= size <= 8 * 1024 * 1024 or len(data) > size:
                raise ValueError("Bounded full-size virtual allocation exceeded")
            ptr = self.heap
            self.heap += (size + 15) & ~15
            if self.heap > self.heap_end:
                raise RuntimeError("Full-size emulator heap bound exceeded")
            if data:
                self.uc.mem_write(ptr, data)
            self.allocation_sizes.append(size)
            return ptr

        def run_full(self, h, b):
            if h[0] != 0x96:
                raise ValueError("Only the virtual 0x96 proof is allowed")
            r, sp = self.reg, 0x7F01E000
            self.uc.reg_write(r.UC_ARM_REG_CPSR, 0x10)
            self.uc.reg_write(r.UC_ARM_REG_SP, sp)
            self.uc.reg_write(r.UC_ARM_REG_LR, self.sentinel)
            for number in range(13):
                self.uc.reg_write(getattr(r, "UC_ARM_REG_R" + str(number)), 0)
            for offset, value in ((0x1E4, 2560), (0x1F0, 2560), (0x1AC, 16), (0x1B8, 16)):
                self.uc.mem_write(sp + offset, struct.pack("<I", value))
            for rr, value in (
                (r.UC_ARM_REG_R0, 7), (r.UC_ARM_REG_R4, sp + 0x188),
                (r.UC_ARM_REG_R5, 0x181CC), (r.UC_ARM_REG_R7, 8), (r.UC_ARM_REG_R8, 7),
            ):
                self.uc.reg_write(rr, value)
            self.incoming = bytearray(h + b + header(0xFE, h[8:24], 0))
            self.uc.emu_start(0xCB58, self.sentinel, timeout=180_000_000, count=800_000_000)
            if self.failure:
                raise RuntimeError("Full-size ARM emulation failed") from self.failure
            if not self.stop_reason:
                raise RuntimeError("No bounded completion point reached")
            return self

    def run_case(name, h, b, expected=None, allow_virtual_bounds=False):
        vm = FullSizeVM()
        try:
            vm.run_full(h, b)
        except RuntimeError:
            # Only the deliberately oversized case may stop at this guard.
            # An actual MTD driver could short-write/error differently; this is
            # evidence of a firmware request, not a physical overflow proof.
            if (not allow_virtual_bounds or vm.bounds_violation is None
                    or str(vm.failure) != "Virtual MTD access out of range"):
                raise
            vm.stop_reason = "virtual_bounds_guard"
        app, bak = bytes(vm.parts[33]), bytes(vm.parts[34])
        statuses = [[x[0], x[1]] for x in vm.outgoing]
        valid_replies = all(crc32(x[:24]) == struct.unpack(">I", x[24:28])[0] for x in vm.outgoing)
        erase_count = sum(len(x) > 1 and x[1] == "MEMERASE" for x in vm.io)
        write_count = sum(len(x) > 1 and x[1] == "write" for x in vm.io)
        row = {
            "name": name, "stop_reason": vm.stop_reason, "status": statuses,
            "reply_header_crcs_valid": valid_replies, "remaining_input_bytes": len(vm.incoming),
            "mtd2_changed": any(x != 0xA5 for x in app),
            "mtd5_changed": any(x != 0x5A for x in bak),
            "virtual_erases": erase_count, "virtual_writes": write_count,
            "virtual_opened_devices": vm.opens,
            "virtual_bounds_violation": vm.bounds_violation,
            "largest_virtual_malloc_bytes": max(vm.allocation_sizes, default=0),
            "total_virtual_bump_allocated_bytes": sum((n + 15) & ~15 for n in vm.allocation_sizes),
            "emulated_reboot_requested": vm.stop_reason == "reboot_requested",
        }
        if expected is not None:
            backup = struct.pack("<II", len(expected) + 8, crc32(expected)) + expected
            row.update({
                "image_bytes": len(expected), "input_sha256": sha256(expected),
                "appfs_exact": app[:len(expected)] == expected,
                "bakfs_header_and_payload_exact": bak[:len(backup)] == backup,
                "appfs_payload_sha256": sha256(app[:len(expected)]),
                "bakfs_payload_sha256": sha256(bak[8:8 + len(expected)]),
                "appfs_tail_preserved": app[len(expected):] == bytes([0xA5]) * (MTD_BYTES - len(expected)),
                "bakfs_tail_preserved": bak[len(backup):] == bytes([0x5A]) * max(0, MTD_BYTES - len(backup)),
            })
        if vm.commands:
            raise RuntimeError("Unexpected simulated shell operation")
        print(json.dumps({"case": name, "stop_reason": vm.stop_reason,
                          "virtual_erases": erase_count, "virtual_writes": write_count,
                          "appfs_exact": row.get("appfs_exact"),
                          "bakfs_exact": row.get("bakfs_header_and_payload_exact")}), flush=True)
        return row

    cases = [run_case("pinned_fullsize_diagnostic", head, body, image)]
    bad_head = bytearray(head)
    bad_head[24] ^= 1
    bad_body = bytearray(body)
    bad_body[31] ^= 1
    cases.append(run_case("bad_header_crc", bytes(bad_head), body))
    cases.append(run_case("bad_cipher_crc", head, bytes(bad_body)))
    # Only the final ECB block changes for the malformed inner CRC.
    last = bytearray(plain[-16:])
    last[-1] ^= 1
    bad_ct = ciphertext[:-16] + bulk_ecb(packer, nonce, bytes(last))
    bad_plain_body = body[:28] + struct.pack(">I", crc32(bad_ct)) + bad_ct
    cases.append(run_case("bad_plain_crc", head, bad_plain_body))

    # These are observations, not an assumption that the firmware is fail-closed.
    # A too-big image can reveal a dangerous active/backup partial-write case.
    for name, size in (("exact_backup_capacity", MTD_BYTES - 8),
                       ("one_byte_beyond_backup_capacity", MTD_BYTES - 7)):
        payload = bytes(size)
        h, b, _, _ = envelope(payload)
        cases.append(run_case(name, h, b, payload,
                              allow_virtual_bounds=name == "one_byte_beyond_backup_capacity"))

    normal = cases[0]
    normal_ok = (normal["appfs_exact"] and normal["bakfs_header_and_payload_exact"]
                 and normal["emulated_reboot_requested"] and normal["reply_header_crcs_valid"]
                 and normal["remaining_input_bytes"] == 0 and normal["status"][-1] == [254, 0])
    reject_ok = all(not c["mtd2_changed"] and not c["mtd5_changed"]
                    and not c["virtual_erases"] and not c["virtual_writes"]
                    and not c["emulated_reboot_requested"] and c["reply_header_crcs_valid"]
                    and (c["stop_reason"] == "header_crc_rejected"
                         or any(status[0] == 255 for status in c["status"])) for c in cases[1:4])
    exact = cases[4]
    exact_ok = (exact["appfs_exact"] and exact["bakfs_header_and_payload_exact"]
                and exact["emulated_reboot_requested"] and exact["reply_header_crcs_valid"])
    oversized = cases[5]
    capacity_safe = (not oversized["mtd2_changed"] and not oversized["mtd5_changed"]
                     and not oversized["virtual_erases"] and not oversized["virtual_writes"]
                     and oversized["virtual_bounds_violation"] is None
                     and not oversized["emulated_reboot_requested"])
    report = {
        "passed": bool(normal_ok and reject_ok and exact_ok),
        "passed_scope": "Pinned image, three CRC rejections and exact capacity only; not deployment safety",
        "firmware_oversize_rejected_before_any_mutation": capacity_safe,
        "offline_maximum_image_bytes_for_backup": MTD_BYTES - 8,
        "diagnostic_spare_backup_bytes": MTD_BYTES - 8 - len(image), "source_revision": SOURCE_REVISION,
        "firmware_sha256": EXPECTED_ELF_SHA256, "diagnostic_image_sha256": sha256(image),
        "diagnostic_image_bytes": len(image), "envelope_bytes_in_memory": len(head) + len(body),
        "encrypted_envelope_persisted": False, "bulk_ecb_crosschecks": len(samples),
        "fullsize_encrypt_decrypt_roundtrip": True, "virtual_erase_block_bytes": 65536,
        "erase_geometry_is_assumed_not_measured": True, "cases": cases,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "network_connections": 0, "real_device_writes": 0, "real_reboots": 0,
        "host_shell_commands_executed": 0, "flash_write_allowed": False,
        "not_tested": ["real flash reliability", "real erase geometry", "actual reboot/boot",
                       "camera RAM availability", "camera watchdog handoff", "hardware sensor/Wi-Fi"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "case_count": len(cases),
                      "report": args.output.name, "flash_write_allowed": False}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
