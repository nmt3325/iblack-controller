#!/usr/bin/env python3
"""audio/: the microphone fix for majestic (libmajaudiofix.so, the S95majestic wrapper, opusstat.py).

There is no ARM CPU here, so the library is checked as an ELF file: an ARM EABI5 soft-float shared object that
needs only musl's libc.so, is bound at load time, has no text relocations and no executable stack, exports only
HI_MPI_AI_SetPubAttr and imports only dlsym, and its code is what audio/majaudiofix.S assembles to: call the real
function first, then, only on success and only for mono, turn u32ChnCnt 2 into 1 (the only store into memory).
If OM_ARM_BINUTILS and OM_OIPC_ROOTFS name the binutils and the unpacked OpenIPC rootfs, audio/build.sh must
rebuild the checked-in file byte for byte. The wrapper runs under host BusyBox against a fake root
(S95MAJESTIC_ROOT). On unit 2 the library ran inside majestic (audio/README.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
AUDIO = BASE / "audio"
LIB = AUDIO / "libmajaudiofix.so"
WRAPPER = AUDIO / "S95majestic"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
sys.path.insert(0, str(AUDIO))
import opusstat  # noqa: E402

# HI_MPI_AI_SetPubAttr as assembled from majaudiofix.S (binutils 2.40), then its name string
CODE = (0xE92D4070, 0xE1A04000, 0xE1A05001, 0xE3E00000, 0xE28F1058, 0xEBFFFFF6, 0xE3500000, 0x0A000011,
        0xE1A0C000, 0xE1A00004, 0xE1A01005, 0xE12FFF3C, 0xE1A06000, 0xE3560000, 0x1A000008, 0xE3550000,
        0x0A000006, 0xE595200C, 0xE3520000, 0x1A000003, 0xE595201C, 0xE3520002, 0x03A02001, 0x0585201C,
        0xE1A00006, 0xE8BD8070, 0xE3E00000, 0xE8BD8070)
NAME = b"HI_MPI_AI_SetPubAttr\0"
LD_SO_NOISE = re.compile(r"^ERROR: ld\.so: object .* from LD_PRELOAD cannot be preloaded .*: ignored\.$")


def parse_elf(data: bytes) -> dict:
    assert data[:4] == b"\x7fELF" and data[4] == 1 and data[5] == 1, "not a 32-bit little-endian ELF"
    (e_type, e_machine, _ver, _entry, phoff, _shoff, flags, _ehsize, phentsize, phnum,
     _shentsize, _shnum, _shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    phdrs = [struct.unpack_from("<IIIIIIII", data, phoff + i * phentsize) for i in range(phnum)]
    loads = [p for p in phdrs if p[0] == 1]

    def off(vaddr: int) -> int:
        for _t, p_off, p_vaddr, _pa, p_filesz, _ms, _fl, _al in loads:
            if p_vaddr <= vaddr < p_vaddr + p_filesz:
                return p_off + vaddr - p_vaddr
        raise AssertionError(f"address {vaddr:#x} is not in a PT_LOAD segment")

    dyn = next(p for p in phdrs if p[0] == 2)
    tags = []
    for i in range(dyn[4] // 8):
        tag, val = struct.unpack_from("<iI", data, dyn[1] + i * 8)
        if tag == 0:
            break
        tags.append((tag, val))
    d = dict(tags)
    strtab = off(d[5])

    def string(i: int) -> str:
        return data[strtab + i:data.index(b"\0", strtab + i)].decode()

    nsyms = struct.unpack_from("<II", data, off(d[4]))[1]  # DT_HASH: nbucket, nchain
    syms = []
    for i in range(1, nsyms):
        name, value, size, info, _other, shndx = struct.unpack_from("<IIIBBH", data, off(d[6]) + i * 16)
        syms.append({"name": string(name), "value": value, "size": size, "bind": info >> 4, "type": info & 15,
                     "defined": shndx != 0})
    return {"type": e_type, "machine": e_machine, "flags": flags, "phdrs": phdrs, "tags": tags, "syms": syms,
            "needed": [string(v) for t, v in tags if t == 1], "soname": [string(v) for t, v in tags if t == 14],
            "off": off}


class Library(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = LIB.read_bytes()
        cls.elf = parse_elf(cls.data)

    def test_arm_eabi5_shared_object_for_musl(self):
        e = self.elf
        self.assertEqual((e["type"], e["machine"], e["flags"]), (3, 40, 0x05000200))  # ET_DYN, ARM, EABI5 soft-float
        self.assertEqual(e["needed"], ["libc.so"])
        self.assertEqual(e["soname"], ["libmajaudiofix.so"])
        self.assertNotIn(3, [p[0] for p in e["phdrs"]], "PT_INTERP")
        stack = [p for p in e["phdrs"] if p[0] == 0x6474E551]
        self.assertEqual(len(stack), 1, "PT_GNU_STACK")
        self.assertEqual(stack[0][6] & 1, 0, "executable stack")
        for p in e["phdrs"]:
            if p[0] == 1:
                self.assertNotEqual(p[6] & 3, 3, "a PT_LOAD segment that is both writable and executable")

    def test_bound_now_without_text_relocations(self):
        d = dict(self.elf["tags"])
        self.assertTrue(d.get(30, 0) & 8 or 24 in d or d.get(0x6FFFFFFB, 0) & 1, "BIND_NOW")
        self.assertNotIn(22, d, "DT_TEXTREL")
        self.assertEqual(d.get(30, 0) & 4, 0, "DF_TEXTREL")
        self.assertIn(4, d, "DT_HASH")
        self.assertIn(0x6FFFFEF5, d, "DT_GNU_HASH")

    def test_exports_only_the_wrapper_and_imports_only_dlsym(self):
        syms = self.elf["syms"]
        exported = [(s["name"], s["type"], s["bind"], s["size"]) for s in syms if s["defined"]]
        self.assertEqual(exported, [("HI_MPI_AI_SetPubAttr", 2, 1, len(CODE) * 4 + 24)])  # FUNC, GLOBAL
        self.assertEqual([s["name"] for s in syms if not s["defined"] and s["name"]], ["dlsym"])

    def test_code_is_majaudiofix_s(self):
        f = next(s for s in self.elf["syms"] if s["name"] == "HI_MPI_AI_SetPubAttr")
        o = self.elf["off"](f["value"])
        words = struct.unpack_from(f"<{len(CODE)}I", self.data, o)
        self.assertEqual([f"{w:08x}" for w in words], [f"{w:08x}" for w in CODE])
        tail = self.data[o + len(CODE) * 4:o + f["size"]]
        self.assertEqual(tail[:len(NAME)], NAME)
        adr = words.index(0xE28F1058)  # adr r1, .Lname: pc (+8) + 0x58 must be the name
        self.assertEqual(f["value"] + adr * 4 + 8 + 0x58, f["value"] + len(CODE) * 4)
        # single data transfers (bits 27-26 = 01) with L = 0 are stores; there is exactly one
        stores = [i for i, w in enumerate(words) if w >> 28 != 15 and (w >> 26) & 3 == 1 and not (w >> 20) & 1]
        self.assertEqual([words[i] for i in stores], [0x0585201C])  # streq r2, [r5, #28] (u32ChnCnt)
        order = [words.index(w) for w in (0xE12FFF3C, 0xE595200C, 0xE595201C, 0xE3520002, 0x03A02001, 0x0585201C)]
        self.assertEqual(order, sorted(order), "the real call must come first, then enSoundmode, then u32ChnCnt")

    def test_recorded_hashes(self):
        res = json.loads((BASE / "results" / "unit2-audio.json").read_text())
        files = res["camera_changes"]["overlay_files"]
        for dest, src in (("/usr/lib/libmajaudiofix.so", LIB), ("/etc/init.d/S95majestic", WRAPPER)):
            self.assertEqual(files[dest]["sha256"], hashlib.sha256(src.read_bytes()).hexdigest(), dest)

    @unittest.skipUnless(os.environ.get("OM_ARM_BINUTILS") and os.environ.get("OM_OIPC_ROOTFS"),
                         "set OM_ARM_BINUTILS and OM_OIPC_ROOTFS to rebuild the library")
    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.so"
            subprocess.run(["sh", str(AUDIO / "build.sh"), os.environ["OM_ARM_BINUTILS"],
                            os.environ["OM_OIPC_ROOTFS"], str(out)], check=True, timeout=60)
            self.assertEqual(out.read_bytes(), self.data)


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class Wrapper(unittest.TestCase):
    ROM_STUB = '#!/bin/sh\necho "$0|$#|$1|$2|${LD_PRELOAD-unset}"\nexit 7\n'

    def setUp(self):
        self.root = r = Path(tempfile.mkdtemp(prefix="s95majestic-"))
        self.addCleanup(shutil.rmtree, r, True)
        for sub in ("bin", "rom/etc/init.d", "usr/lib"):
            (r / sub).mkdir(parents=True)
        (r / "bin/sh").symlink_to(BUSYBOX)
        (r / "rom/etc/init.d/S95majestic").write_text(self.ROM_STUB)
        shutil.copy(LIB, r / "usr/lib/libmajaudiofix.so")
        self.fix = f"{r}/usr/lib/libmajaudiofix.so"
        self.rom = f"{r}/rom/etc/init.d/S95majestic"

    def run_wrapper(self, *args, preload=None):
        env = {"PATH": str(self.root / "bin"), "S95MAJESTIC_ROOT": str(self.root)}
        if preload is not None:
            env["LD_PRELOAD"] = preload
        r = subprocess.run([BUSYBOX, "sh", str(WRAPPER), *args], env=env, capture_output=True, text=True, timeout=30)
        # the host's glibc loader cannot use the ARM library and says so; that is not the wrapper's output
        err = [line for line in r.stderr.splitlines() if not LD_SO_NOISE.match(line)]
        return r.returncode, r.stdout.splitlines(), err

    def test_file(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK), "S95majestic must be executable (rcS runs '$i start')")
        r = subprocess.run([BUSYBOX, "sh", "-n", str(WRAPPER)], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_preloads_the_fix_and_hands_over_to_the_rom_script(self):
        rc, out, err = self.run_wrapper("start", "a b")
        self.assertEqual((rc, out, err), (7, [f"{self.rom}|2|start|a b|{self.fix}"], []))
        # the firmware script finds its supervisor by the basename of $0
        self.assertEqual(out[0].split("|")[0].rsplit("/", 1)[1], "S95majestic")

    def test_keeps_other_preloads_and_adds_the_fix_once(self):
        rc, out, _ = self.run_wrapper("stop", preload="/x.so")
        self.assertEqual(out, [f"{self.rom}|1|stop||{self.fix} /x.so"])
        rc, out, _ = self.run_wrapper("stop", preload=f"/a.so {self.fix}")
        self.assertEqual(out, [f"{self.rom}|1|stop||/a.so {self.fix}"])

    def test_without_the_library_it_only_hands_over(self):
        os.unlink(self.fix)
        self.assertEqual(self.run_wrapper("restart"), (7, [f"{self.rom}|1|restart||unset"], []))
        self.assertEqual(self.run_wrapper("reload", preload="/x.so")[1], [f"{self.rom}|1|reload||/x.so"])

    def test_missing_rom_script(self):
        os.unlink(self.rom)
        self.assertEqual(self.run_wrapper("start"), (1, [], [f"S95majestic: {self.rom} is missing"]))


def ogg(packets, granule=0) -> bytes:
    """One Ogg page per packet (lacing values of 255 for long packets); CRC left 0, opusstat does not check it."""
    out = b""
    for seq, pkt in enumerate(packets):
        lacing = [255] * (len(pkt) // 255) + [len(pkt) % 255]
        out += (b"OggS\0\0" + struct.pack("<qIII", granule, 1, seq, 0) + bytes([len(lacing)]) + bytes(lacing) + pkt)
    return out


def opus_head(channels: int) -> bytes:
    return b"OpusHead" + struct.pack("<BBHIhB", 1, channels, 0, 8000, 0, 0)


TAGS = b"OpusTags" + struct.pack("<I", 0) + struct.pack("<I", 0)


class OpusStat(unittest.TestCase):
    def test_bug_and_fix(self):
        bug = opusstat.summary(ogg([opus_head(2), TAGS] + [b"\x04" + b"\x55" * 9] * 50), 1.0)  # SILK NB 10 ms stereo
        self.assertEqual((bug["opus_head"]["channels"], bug["packets"], bug["toc_config"], bug["toc_stereo"],
                          bug["frame_ms"], bug["decoded_seconds"], bug["decoded_per_wallclock"]),
                         (2, 50, {0: 50}, {1: 50}, [10], 0.5, 0.5))
        fix = opusstat.summary(ogg([opus_head(1), TAGS] + [b"\x08" + b"\x55" * 9] * 50), 1.0)  # SILK NB 20 ms mono
        self.assertEqual((fix["opus_head"]["channels"], fix["toc_config"], fix["toc_stereo"], fix["frame_ms"],
                          fix["decoded_seconds"], fix["decoded_per_wallclock"], fix["packets_per_second"]),
                         (1, {1: 50}, {0: 50}, [20], 1.0, 1.0, 50.0))

    def test_frame_counts_long_packets_and_configs(self):
        pkts = [b"\x09" + b"\0" * 20,              # code 1: two 20 ms frames
                b"\x0b\x03" + b"\0" * 30,          # code 3: three 20 ms frames
                b"\xf8" + b"\1" * 600,             # CELT 20 ms, spread over three lacing segments
                b"\x70" + b"\0" * 4]               # hybrid 10 ms
        s = opusstat.summary(ogg([opus_head(1), TAGS] + pkts))
        self.assertEqual((s["packets"], s["decoded_seconds"], s["frame_ms"]), (4, 0.13, [10, 20]))
        self.assertNotIn("decoded_per_wallclock", s)
        self.assertEqual([opusstat.frame_ms(c) for c in (0, 1, 2, 3, 12, 13, 16, 17, 18, 31)],
                         [10, 20, 40, 60, 10, 20, 2.5, 5, 10, 20])

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.opus"
            f.write_bytes(ogg([opus_head(1), TAGS] + [b"\x08\0"] * 10))
            r = subprocess.run([sys.executable, str(AUDIO / "opusstat.py"), str(f), "0.2"], capture_output=True,
                               text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual((out["decoded_seconds"], out["decoded_per_wallclock"]), (0.2, 1.0))
            r = subprocess.run([sys.executable, str(AUDIO / "opusstat.py")], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
