"""Unit tests use synthetic headers; they do not prove a filesystem boots."""
import hashlib
from pathlib import Path
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import flash_layout as f


def sample_image(payload=b"synthetic-kernel"):
    h = bytearray(struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), 0x80008000,
                              0x80008000, zlib.crc32(payload), 5, 2, 2, 0, b"unit-test"))
    struct.pack_into(">I", h, 4, zlib.crc32(h))
    root_offset = f.align_up(64 + len(payload))
    sq = bytearray(128)
    sq[:4] = b"hsqs"
    struct.pack_into("<I", sq, 12, 131072)
    struct.pack_into("<H", sq, 20, 4)
    struct.pack_into("<HH", sq, 28, 4, 0)
    struct.pack_into("<Q", sq, 40, len(sq))
    data = bytes(h) + payload
    data += bytes([255]) * (root_offset - len(data))
    data += sq
    data += bytes([255]) * (f.align_up(len(data)) - len(data))
    return data


def sample_boot(offset="0x50000", length="0x300000"):
    data = (f"bootcmd=sf probe 0; sf read ${{baseaddr}} {offset} {length}; bootm ${{baseaddr}}\0"
            "bootargs=root=/dev/mtdblock3 mtdparts=${mtdparts}\0"
            "mtdparts=hi_sfc:256k(boot),64k(env),2048k(kernel),5120k(rootfs),-(rootfs_data)\0").encode()
    return data + bytes([255]) * (4096 - len(data))


class PartitionTests(unittest.TestCase):
    def test_vendor_map_covers_flash(self):
        self.assertEqual(sum(p.size for p in f.VENDOR_PARTITIONS), f.FLASH_BYTES)
        self.assertEqual([p.end for p in f.VENDOR_PARTITIONS[:-1]],
                         [p.start for p in f.VENDOR_PARTITIONS[1:]])

    def test_cross_mtd_firmware_map(self):
        segs = f.vendor_segments(0x50000, 0x6B0000)
        self.assertEqual([x["vendor_mtd_index"] for x in segs], list(range(6)))
        self.assertEqual([x["bytes"] for x in segs],
                         [0x1B0000, 0xC0000, 0x280000, 0x20000, 0x20000, 0x180000])
        self.assertEqual(segs[0]["mtd_local_offset"], 0x50000)
        self.assertEqual(sum(x["bytes"] for x in segs), 0x6B0000)
        self.assertEqual([x["payload_offset"] for x in segs],
                         [0, 0x1B0000, 0x270000, 0x4F0000, 0x510000, 0x530000])

    def test_overlay_only_uses_end_of_bakfs(self):
        self.assertEqual(f.vendor_segments(0x700000, 0x100000),
                         [{"vendor_mtd_index": 5, "vendor_name": "bakfs", "flash_offset": 0x700000,
                           "mtd_local_offset": 0x180000, "payload_offset": 0, "bytes": 0x100000}])

    def test_environment_and_boot_maps_do_not_touch_payload(self):
        env = f.vendor_segments(0x40000, 0x10000)
        boot = f.vendor_segments(0, 0x40000)
        self.assertEqual(env[0]["flash_offset"] + env[0]["bytes"], f.FIRMWARE_START)
        self.assertEqual(boot[0]["flash_offset"] + boot[0]["bytes"], env[0]["flash_offset"])

    def test_zero_length_ranges(self):
        self.assertEqual(f.vendor_segments(0, 0), [])
        self.assertEqual(f.vendor_segments(f.FLASH_BYTES, 0), [])

    def test_invalid_ranges_rejected(self):
        for start, size in ((-1, 1), (0, -1), (f.FLASH_BYTES, 1), (True, 1), (0, 1.5)):
            with self.subTest(start=start, size=size), self.assertRaises(ValueError):
                f.vendor_segments(start, size)

    def test_alignment(self):
        self.assertEqual(f.align_up(1), 65536)
        self.assertEqual(f.align_up(65536), 65536)
        self.assertEqual(f.align_up(0), 0)
        for value, alignment in ((-1, 65536), (1, 0), (1, True), (True, 65536)):
            with self.subTest(value=value, alignment=alignment), self.assertRaises(ValueError):
                f.align_up(value, alignment)


class ImageTests(unittest.TestCase):
    def test_valid_synthetic_structure(self):
        data = sample_image()
        got = f.inspect_split_image(data)
        self.assertEqual(got["rootfs_offset_within_firmware"], 0x10000)
        self.assertEqual(got["rootfs_flash_start"], 0x60000)
        self.assertEqual(got["rootfs_data_flash_start"], 0x70000)
        self.assertEqual(got["overlay_bytes"], 0x790000)
        self.assertEqual(got["firmware_sha256"], hashlib.sha256(data).hexdigest())

    def test_bad_magic_and_header_crc_rejected(self):
        for offset in (0, 4, 12):
            data = bytearray(sample_image())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                f.inspect_split_image(bytes(data))

    def test_bad_kernel_payload_crc_rejected(self):
        data = bytearray(sample_image())
        data[64] ^= 1
        with self.assertRaises(ValueError):
            f.inspect_split_image(bytes(data))

    def test_missing_squashfs_rejected(self):
        data = bytearray(sample_image())
        data[0x10000] = 0
        with self.assertRaises(ValueError):
            f.inspect_split_image(bytes(data))

    def test_bad_squashfs_geometry_rejected(self):
        for off, value, fmt in ((12, 12000, "<I"), (28, 3, "<H"), (40, 0x800000, "<Q")):
            data = bytearray(sample_image())
            struct.pack_into(fmt, data, 0x10000 + off, value)
            with self.subTest(offset=off), self.assertRaises(ValueError):
                f.inspect_split_image(bytes(data))

    def test_unexpected_trailing_padding_rejected(self):
        with self.assertRaises(ValueError):
            f.inspect_split_image(sample_image() + bytes(65536))

    def test_large_kernel_rejected(self):
        with self.assertRaises(ValueError):
            f.inspect_split_image(sample_image(bytes(0x200000 - 64 + 1)))

    def test_bootloader_reads_correct_offset(self):
        got = f.boot_defaults(sample_boot(), 80)
        self.assertEqual(got["kernel_read_offset"], 0x50000)
        self.assertTrue(got["fixed_root_argument_alone_is_not_a_split_boot_failure"])

    def test_wrong_boot_offset_or_short_read_rejected(self):
        for boot in (sample_boot("0x80000"), sample_boot(length="0x10")):
            with self.assertRaises(ValueError):
                f.boot_defaults(boot, 80)

    def test_oversized_bootloader_rejected(self):
        with self.assertRaises(ValueError):
            f.boot_defaults(bytes(0x40001), 80)

    def test_plan_never_authorizes_deployment(self):
        p = f.build_plan(sample_image(), sample_boot())
        self.assertFalse(p["flash_write_allowed"])
        self.assertEqual(p["network_connections"], 0)
        self.assertEqual(p["real_device_writes"], 0)
        self.assertEqual(p["vendor_private_partitions_overwritten"], ["mdat1", "mdat2"])
        self.assertGreater(len(p["unresolved_deployment_gates"]), 0)

    def test_device_input_rejected(self):
        with self.assertRaises(ValueError):
            f.read_regular(Path("/dev/null"), 0x800000)


if __name__ == "__main__":
    unittest.main()
