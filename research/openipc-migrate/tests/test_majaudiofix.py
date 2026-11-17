#!/usr/bin/env python3
"""audio/: the audio fixes for majestic (libmajaudiofix.so, the S95majestic wrapper, opusstat.py).

There is no ARM CPU here. The library is checked as an ELF file: an ARM EABI5 soft-float shared object that needs
only musl's libc.so, is bound at load time, has no text relocations and no executable stack, exports exactly its
three wrappers (HI_MPI_AI_SetPubAttr, HI_MPI_ADEC_CreateChn, HI_MPI_ADEC_SendStream) and imports only dlsym and
syslog; HI_MPI_AI_SetPubAttr is what audio/majaudiofix.S assembles to. Then the library runs in a small ARM
interpreter (tests/arm_emu.py) against fakes of musl (dlsym, syslog), libmpi.so and majestic (config_get_int): the
microphone wrapper calls the real function first and then, only on success and only for mono, turns u32ChnCnt 2
into 1; the speaker wrappers turn audio.outputVolume into the documented gain when the LPCM decoder channel is
created and hand libmpi a scaled copy of every packet, bit for bit (s * gain + 0x8000) >> 16 saturated, while the
caller's struct and samples (mapped read-only, with nothing mapped behind them) are only read. If OM_ARM_BINUTILS
and OM_OIPC_ROOTFS name the binutils and the unpacked OpenIPC rootfs, audio/build.sh must rebuild the checked-in
file byte for byte. The wrapper runs under host BusyBox against a fake root (S95MAJESTIC_ROOT). On unit 2 the
library ran inside majestic (audio/README.md, results/unit2-audio.json, results/unit2-speaker.json).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
AUDIO = BASE / "audio"
LIB = AUDIO / "libmajaudiofix.so"
WRAPPER = AUDIO / "S95majestic"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
sys.path.insert(0, str(AUDIO))
sys.path.insert(0, str(HERE))
import arm_emu  # noqa: E402
import opusstat  # noqa: E402

# HI_MPI_AI_SetPubAttr as assembled from majaudiofix.S (binutils 2.40), then its name string
CODE = (0xE92D4070, 0xE1A04000, 0xE1A05001, 0xE3E00000, 0xE28F1058, 0xEBFFFFF3, 0xE3500000, 0x0A000011,
        0xE1A0C000, 0xE1A00004, 0xE1A01005, 0xE12FFF3C, 0xE1A06000, 0xE3560000, 0x1A000008, 0xE3550000,
        0x0A000006, 0xE595200C, 0xE3520000, 0x1A000003, 0xE595201C, 0xE3520002, 0x03A02001, 0x0585201C,
        0xE1A00006, 0xE8BD8070, 0xE3E00000, 0xE8BD8070)
NAME = b"HI_MPI_AI_SetPubAttr\0"
WRAPPERS = ("HI_MPI_ADEC_CreateChn", "HI_MPI_ADEC_SendStream", "HI_MPI_AI_SetPubAttr")
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

    def test_exports_the_wrappers_and_imports_only_dlsym_and_syslog(self):
        syms = self.elf["syms"]
        exported = sorted((s["name"], s["type"], s["bind"], s["size"]) for s in syms if s["defined"])
        self.assertEqual(exported, [("HI_MPI_ADEC_CreateChn", 2, 1, 140), ("HI_MPI_ADEC_SendStream", 2, 1, 284),
                                    ("HI_MPI_AI_SetPubAttr", 2, 1, len(CODE) * 4 + 24)])  # FUNC, GLOBAL
        self.assertEqual(sorted(s["name"] for s in syms if not s["defined"] and s["name"]), ["dlsym", "syslog"])

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
        # the latest record of what is installed on unit 2 (unit2-audio.json keeps the microphone-only library)
        res = json.loads((BASE / "results" / "unit2-speaker.json").read_text())
        files = res["camera_changes"]["overlay_files"]
        for dest, src in (("/usr/lib/libmajaudiofix.so", LIB), ("/etc/init.d/S95majestic", WRAPPER)):
            self.assertEqual(files[dest]["sha256"], hashlib.sha256(src.read_bytes()).hexdigest(), dest)
        self.assertEqual(files["/usr/lib/libmajaudiofix.so"]["size"], len(self.data))

    @unittest.skipUnless(os.environ.get("OM_ARM_BINUTILS") and os.environ.get("OM_OIPC_ROOTFS"),
                         "set OM_ARM_BINUTILS and OM_OIPC_ROOTFS to rebuild the library")
    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.so"
            subprocess.run(["sh", str(AUDIO / "build.sh"), os.environ["OM_ARM_BINUTILS"],
                            os.environ["OM_OIPC_ROOTFS"], str(out)], check=True, timeout=60)
            self.assertEqual(out.read_bytes(), self.data)


RTLD_DEFAULT, RTLD_NEXT = 0, 0xFFFFFFFF  # musl
INT_MIN = 0x80000000  # what majestic's config_get_int returns for a key that is not set
PT_LPCM = 23
FAIL = 0xFFFFFFFF  # what a wrapper returns when libmpi lacks the real function
LIB_BASE, DATA_BASE, TRAPS = 0x40000000, 0x10000000, 0x7E000000
STACK_BASE, STACK_SIZE = 0x7FF00000, 0x10000
SP = STACK_BASE + STACK_SIZE - 64
GAIN_MSG = "majaudiofix: speaker outputVolume {} -> gain {}/65536"
# AIO_ATTR_S as majestic fills it: 8 kHz, 16 bit, I2S master, mono, 0, 30 frames, 320 points, 2 channels, 0
AIO_MONO = struct.pack("<9I", 8000, 1, 0, 0, 0, 30, 320, 2, 0)
CORNERS = [-32768, -32767, -16385, -16384, -12345, -2, -1, 0, 1, 2, 12345, 16383, 16384, 32766, 32767]


def gain_of(volume: int) -> int:
    """audio.outputVolume -> Q16 gain as majaudiofix.S documents it: 0 = mute, else (volume - 50) * 0.6 dB."""
    return 0 if volume == 0 else round(65536 * 10 ** ((volume - 50) * 0.03))


def scaled(samples, gain: int) -> list:
    return [max(-32768, min(32767, (s * gain + 0x8000) >> 16)) for s in samples]


def pcm(samples) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def samples_of(data: bytes) -> list:
    return list(struct.unpack(f"<{len(data) // 2}h", data[:len(data) // 2 * 2]))


def stream(buf: int, length: int) -> bytes:
    """AUDIO_STREAM_S: pStream, u32PhyAddr, u32Len, (padding), u64TimeStamp, u32Seq, (padding)."""
    return struct.pack("<IIIIQII", buf, 0x5EED0004, length, 0xA5A5A5A5, 0x1122334455667788, 4242, 0x5A5A5A5A)


class Process:
    """libmajaudiofix.so mapped into an emulated majestic. musl's dlsym and syslog, the three libmpi.so functions
    and majestic's config_get_int are Python fakes that record their calls."""

    def __init__(self, volume=None, *, majestic=True, missing=(), results=None):
        self.volume, self.majestic, self.missing = volume, majestic, set(missing)
        self.results = dict(results or {})
        self.calls, self.sent = [], []
        fakes = {"dlsym": self.dlsym, "syslog": self.syslog, "config_get_int": self.config_get_int,
                 "HI_MPI_AI_SetPubAttr": self.set_pub_attr, "HI_MPI_ADEC_CreateChn": self.create_chn,
                 "HI_MPI_ADEC_SendStream": self.send_stream}
        self.trap = {name: TRAPS + 16 * i for i, name in enumerate(fakes)}
        self.mem = arm_emu.Memory()
        self.exports = arm_emu.load(self.mem, LIB.read_bytes(), LIB_BASE,
                                    {"dlsym": self.trap["dlsym"], "syslog": self.trap["syslog"]})
        self.mem.map(STACK_BASE, STACK_SIZE, writable=True, name="stack")
        self.cpu = arm_emu.Cpu(self.mem, {self.trap[name]: fn for name, fn in fakes.items()})
        self.heap = DATA_BASE

    def put(self, data: bytes, *, writable: bool = False, offset: int = 0) -> int:
        """Maps the caller's data (read-only unless writable) at `offset` from a page, nothing mapped behind it."""
        addr = self.heap + offset
        self.mem.map(addr, len(data), writable=writable, name=f"caller data at {addr:#x}", data=data)
        self.heap = (addr + len(data) + 0x1FFF) & ~0xFFF
        return addr

    def call(self, name: str, *args: int) -> int:
        return self.cpu.call(self.exports[name], *args, sp=SP)

    def stack_use(self) -> int:
        """How far below the caller's sp the last call went."""
        return SP - self.cpu.min_sp

    def gain(self) -> int:
        """The library's only writable memory after loading: the Q16 gain HI_MPI_ADEC_SendStream applies."""
        rw = [r for r in self.mem.regions if r[2] and r[4].startswith("segment")]
        assert len(rw) == 1 and len(rw[0][1]) == 4, [(hex(r[0]), len(r[1])) for r in rw]
        return struct.unpack("<I", rw[0][1])[0]

    def dlsym(self, cpu) -> int:
        handle, name = cpu.r[0], self.mem.cstring(cpu.r[1])
        self.calls.append(("dlsym", handle, name))
        # musl's dlsym finds the calling DSO (for RTLD_NEXT) by lr: it must point into the library's code
        if not self.mem.region(cpu.r[14], 4)[3]:
            raise arm_emu.ArmFault(f"dlsym called with lr {cpu.r[14]:#x} outside the library's code")
        if name == "config_get_int" and handle == RTLD_DEFAULT:
            return self.trap[name] if self.majestic else 0
        if handle != RTLD_NEXT or not name.startswith("HI_MPI_") or name not in self.trap:
            raise arm_emu.ArmFault(f"dlsym({handle:#x}, {name!r})")
        return 0 if name in self.missing else self.trap[name]

    def syslog(self, cpu) -> None:
        fmt = self.mem.cstring(cpu.r[1])
        n = fmt.count("%d")
        if not re.fullmatch(r"(?:[^%]|%d)*", fmt) or n > 2:
            raise arm_emu.ArmFault(f"syslog format {fmt!r}")
        self.calls.append(("syslog", cpu.r[0], fmt % tuple(arm_emu.signed(v) for v in cpu.r[2:2 + n])))

    def config_get_int(self, cpu) -> int:
        self.calls.append(("config_get_int", self.mem.cstring(cpu.r[0]), self.mem.cstring(cpu.r[1])))
        return INT_MIN if self.volume is None else self.volume

    def set_pub_attr(self, cpu) -> int:
        dev, attr = cpu.r[0], cpu.r[1]
        self.calls.append(("HI_MPI_AI_SetPubAttr", dev, attr, self.mem.read(attr, 36) if attr else None))
        return self.results.get("HI_MPI_AI_SetPubAttr", 0)

    def create_chn(self, cpu) -> int:
        self.calls.append(("HI_MPI_ADEC_CreateChn", cpu.r[0], cpu.r[1]))
        return self.results.get("HI_MPI_ADEC_CreateChn", 0)

    def send_stream(self, cpu) -> int:
        chn, ptr, block = cpu.r[0], cpu.r[1], cpu.r[2]
        self.calls.append(("HI_MPI_ADEC_SendStream", chn, ptr, block))
        hdr = self.mem.read(ptr, 32) if ptr else None
        buf, _phy, length = struct.unpack_from("<III", hdr) if hdr else (0, 0, 0)
        data = self.mem.read(buf, length) if buf and 0 < length <= 0x10000 else None
        self.sent.append({"ptr": ptr, "sp": cpu.r[13], "hdr": hdr, "data": data})
        return self.results.get("HI_MPI_ADEC_SendStream", 0)


class Emulated(unittest.TestCase):
    """The library's code, run instruction by instruction (tests/arm_emu.py)."""

    @staticmethod
    def create(p: Process, en_type: int = PT_LPCM, chn: int = 0) -> tuple:
        attr = p.put(struct.pack("<IIII", en_type, 20, 0, 0x12340000))  # ADEC_CHN_ATTR_S, pValue -> ADEC_ATTR_LPCM_S
        return attr, p.call("HI_MPI_ADEC_CreateChn", chn, attr)

    @staticmethod
    def send(p: Process, data: bytes, *, offset: int = 0, chn: int = 0, block: int = 1, buf: int = None) -> tuple:
        if buf is None:
            buf = p.put(data, offset=offset)
        hdr_bytes = stream(buf, len(data))
        hdr = p.put(hdr_bytes)
        return buf, hdr, hdr_bytes, p.call("HI_MPI_ADEC_SendStream", chn, hdr, block)

    def lpcm(self, volume) -> Process:
        p = Process(volume)
        _attr, rc = self.create(p)
        self.assertEqual(rc, 0)
        p.calls.clear()
        return p

    def assert_passed_on(self, p: Process, hdr: int, hdr_bytes: bytes, data, *, chn: int = 0, block: int = 1):
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_SendStream"),
                                   ("HI_MPI_ADEC_SendStream", chn, hdr, block)])
        self.assertEqual((p.sent[-1]["hdr"], p.sent[-1]["data"]), (hdr_bytes, data))
        self.assertEqual(p.stack_use(), 32)

    def assert_scaled(self, p: Process, buf: int, hdr: int, hdr_bytes: bytes, data: bytes, gain: int, *,
                      chn: int = 0, block: int = 1):
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_SendStream"),
                                   ("HI_MPI_ADEC_SendStream", chn, mock.ANY, block)])
        sent = p.sent[-1]
        copy = sent["ptr"]
        # libmpi gets a copy of the struct at sp, pointing at the samples right behind it
        self.assertEqual((copy, copy % 8), (sent["sp"], 0))
        self.assertTrue(STACK_BASE <= copy < SP, hex(copy))
        self.assertEqual(sent["hdr"], struct.pack("<I", copy + 32) + hdr_bytes[4:])
        even = len(data) // 2 * 2
        self.assertEqual(sent["data"], pcm(scaled(samples_of(data), gain)) + data[even:])
        self.assertEqual(p.stack_use(), 64 + (len(data) + 7 & ~7))
        # the caller's packet is as it was (it is read-only: a write would have faulted)
        self.assertEqual((p.mem.read(hdr, 32), p.mem.read(buf, len(data))), (hdr_bytes, data))

    def test_loads_with_unity_gain(self):
        p = Process()
        self.assertEqual(sorted(p.exports), list(WRAPPERS))
        self.assertEqual(p.gain(), 65536)  # 0 dB until a decoder channel is created

    def test_microphone_mono_becomes_one_channel_after_the_driver_saw_two(self):
        p = Process()
        a = p.put(AIO_MONO, writable=True)
        self.assertEqual(p.call("HI_MPI_AI_SetPubAttr", 0, a), 0)
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_AI_SetPubAttr"),
                                   ("HI_MPI_AI_SetPubAttr", 0, a, AIO_MONO)])  # the driver still sees 2
        self.assertEqual(p.mem.read(a, 36), AIO_MONO[:28] + struct.pack("<I", 1) + AIO_MONO[32:])
        self.assertEqual(p.stack_use(), 16)

    def test_microphone_everything_else_is_left_alone(self):
        stereo = AIO_MONO[:12] + struct.pack("<I", 1) + AIO_MONO[16:]
        for attr, kw, rc in ((stereo, {}, 0),
                             (AIO_MONO[:28] + struct.pack("<I", 1) + AIO_MONO[32:], {}, 0),
                             (AIO_MONO[:28] + struct.pack("<I", 4) + AIO_MONO[32:], {}, 0),
                             (AIO_MONO, {"results": {"HI_MPI_AI_SetPubAttr": 0xA0158003}}, 0xA0158003),
                             (AIO_MONO, {"missing": {"HI_MPI_AI_SetPubAttr"}}, FAIL)):
            with self.subTest(attr=attr.hex(), kw=kw):
                p = Process(**kw)
                a = p.put(attr, writable=True)
                self.assertEqual(p.call("HI_MPI_AI_SetPubAttr", 1, a), rc)
                real = [] if kw.get("missing") else [("HI_MPI_AI_SetPubAttr", 1, a, attr)]
                self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_AI_SetPubAttr")] + real)
                self.assertEqual(p.mem.read(a, 36), attr)
        p = Process()
        self.assertEqual(p.call("HI_MPI_AI_SetPubAttr", 0, 0), 0)
        self.assertEqual(p.calls[1:], [("HI_MPI_AI_SetPubAttr", 0, 0, None)])

    def test_volume_to_gain(self):
        self.assertEqual([gain_of(v) for v in (0, 1, 30, 49, 50, 51, 70, 100)],
                         [0, 2221, 16462, 61162, 65536, 70223, 260904, 2072430])
        for volume in (None, -7, INT_MIN + 1, *range(101), 101, 0x7FFFFFFF):
            with self.subTest(volume=volume):
                p = Process(volume)
                attr, rc = self.create(p)
                v = 50 if volume is None else max(0, min(100, arm_emu.signed(volume & arm_emu.MASK)))
                g = gain_of(v)
                self.assertEqual(rc, 0)
                self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn"),
                                           ("HI_MPI_ADEC_CreateChn", 0, attr),
                                           ("dlsym", RTLD_DEFAULT, "config_get_int"),
                                           ("config_get_int", "audio", "outputVolume"),
                                           ("syslog", 6, GAIN_MSG.format(v, g))])  # LOG_INFO
                self.assertEqual(p.gain(), g)
                if v:
                    self.assertAlmostEqual(20 * math.log10(g / 65536), (v - 50) * 0.6, delta=0.005)
                self.assertEqual(p.stack_use(), 32)

    def test_unity_gain_passes_the_callers_packet_on(self):
        data = pcm(CORNERS)
        for volume in ("no decoder channel yet", None, 50):
            with self.subTest(volume=volume):
                p = Process() if volume == "no decoder channel yet" else self.lpcm(volume)
                buf, hdr, hdr_bytes, rc = self.send(p, data)
                self.assertEqual(rc, 0)
                self.assert_passed_on(p, hdr, hdr_bytes, data)

    def test_scaled_copy(self):
        rng = random.Random(3518)
        data = pcm(CORNERS + [rng.randint(-32768, 32767) for _ in range(320 - len(CORNERS))])  # 640 bytes
        for volume in (0, 1, 30, 49, 51, 70, 100):
            with self.subTest(volume=volume):
                p = self.lpcm(volume)
                buf, hdr, hdr_bytes, rc = self.send(p, data, chn=0, block=1)
                self.assertEqual(rc, 0)
                self.assert_scaled(p, buf, hdr, hdr_bytes, data, gain_of(volume))
                # the next one (2048 bytes, the most the recv_audio plugin sends) from a released stack again
                p.calls.clear()
                more = (data * 4)[:2048]
                buf, hdr, hdr_bytes, rc = self.send(p, more, chn=3, block=0)
                self.assert_scaled(p, buf, hdr, hdr_bytes, more, gain_of(volume), chn=3, block=0)

    def test_any_alignment_and_an_odd_last_byte(self):
        rng = random.Random(18)
        for offset in (0, 1, 2, 3):
            for tail in (b"", b"\xab"):
                with self.subTest(offset=offset, tail=tail):
                    data = pcm(CORNERS + [rng.randint(-32768, 32767) for _ in range(40)]) + tail
                    p = self.lpcm(30)
                    buf, hdr, hdr_bytes, rc = self.send(p, data, offset=offset)
                    self.assertEqual(rc, 0)
                    self.assert_scaled(p, buf, hdr, hdr_bytes, data, gain_of(30))
        p = self.lpcm(70)
        buf, hdr, hdr_bytes, rc = self.send(p, b"\x7f")  # a single byte: nothing to scale
        self.assert_scaled(p, buf, hdr, hdr_bytes, b"\x7f", gain_of(70))

    def test_packets_it_leaves_alone(self):
        p = self.lpcm(30)
        buf, hdr, hdr_bytes, _ = self.send(p, b"")  # empty
        self.assert_passed_on(p, hdr, hdr_bytes, None)
        p.calls.clear()
        _buf, hdr, hdr_bytes, _ = self.send(p, pcm(CORNERS), buf=0)  # no samples
        self.assert_passed_on(p, hdr, hdr_bytes, None)
        p.calls.clear()
        self.assertEqual(p.call("HI_MPI_ADEC_SendStream", 0, 0, 1), 0)  # no struct
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_SendStream"), ("HI_MPI_ADEC_SendStream", 0, 0, 1)])
        self.assertEqual(p.stack_use(), 32)
        big = bytes(range(256)) * 32
        p.calls.clear()
        buf, hdr, hdr_bytes, _ = self.send(p, big + b"\x01")  # 8193 bytes: too big for the stack copy
        self.assert_passed_on(p, hdr, hdr_bytes, big + b"\x01")
        p.calls.clear()
        buf, hdr, hdr_bytes, _ = self.send(p, big)  # 8192 bytes: still scaled
        self.assert_scaled(p, buf, hdr, hdr_bytes, big, gain_of(30))

    def test_a_failed_create_keeps_the_gain_and_a_new_channel_rereads_the_volume(self):
        p = self.lpcm(30)
        self.assertEqual(p.gain(), gain_of(30))
        p.volume, p.results["HI_MPI_ADEC_CreateChn"] = 70, 0xA0188004  # HI_ERR_ADEC_EXIST
        attr, rc = self.create(p)
        self.assertEqual(rc, 0xA0188004)
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn"), ("HI_MPI_ADEC_CreateChn", 0, attr)])
        self.assertEqual(p.gain(), gain_of(30))
        p.calls.clear()
        del p.results["HI_MPI_ADEC_CreateChn"]
        attr, rc = self.create(p)
        self.assertEqual((rc, p.calls[-1], p.gain()), (0, ("syslog", 6, GAIN_MSG.format(70, gain_of(70))), gain_of(70)))

    def test_other_decoders_and_other_programs_get_unity_gain(self):
        p = self.lpcm(30)
        attr, rc = self.create(p, en_type=37)  # not PT_LPCM
        self.assertEqual(rc, 0)
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn"), ("HI_MPI_ADEC_CreateChn", 0, attr)])
        self.assertEqual(p.gain(), 65536)
        p = Process(30)
        self.assertEqual(p.call("HI_MPI_ADEC_CreateChn", 2, 0), 0)  # no attributes
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn"), ("HI_MPI_ADEC_CreateChn", 2, 0)])
        self.assertEqual(p.gain(), 65536)
        p = Process(30, majestic=False)  # no config_get_int: no syslog either
        attr, rc = self.create(p)
        self.assertEqual(p.calls, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn"), ("HI_MPI_ADEC_CreateChn", 0, attr),
                                   ("dlsym", RTLD_DEFAULT, "config_get_int")])
        self.assertEqual((rc, p.gain()), (0, 65536))

    def test_missing_libmpi_functions(self):
        p = Process(30, missing={"HI_MPI_ADEC_CreateChn", "HI_MPI_ADEC_SendStream"})
        _attr, rc = self.create(p)
        self.assertEqual((rc, p.calls, p.gain()), (FAIL, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_CreateChn")], 65536))
        p.calls.clear()
        *_, rc = self.send(p, pcm(CORNERS))
        self.assertEqual((rc, p.calls, p.sent), (FAIL, [("dlsym", RTLD_NEXT, "HI_MPI_ADEC_SendStream")], []))

    def test_errors_of_libmpi_are_returned(self):
        for volume in (50, 30):
            with self.subTest(volume=volume):
                p = self.lpcm(volume)
                p.results["HI_MPI_ADEC_SendStream"] = 0xA018800F  # HI_ERR_ADEC_BUF_FULL
                *_, rc = self.send(p, pcm(CORNERS), block=0)
                self.assertEqual(rc, 0xA018800F)


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
