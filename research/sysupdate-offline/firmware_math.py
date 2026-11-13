"""Offline-only ARM math / I/O-stubbed harness, extracted from iermu_telnet.
No network client is included. The pinned repository ELF is used by default.
IERMU_FIRMWARE overrides the ELF path; IERMU_REPO overrides the repository root.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import struct
import time
import zlib
FIRMWARE_SHA256 = 'c76e218d9dffde035ca5ed28cae89fa2dd679c539f647f6329f8dcf083e0c6d9'
REPO_ROOT = Path(os.environ.get('IERMU_REPO', str(Path(__file__).resolve().parents[2])))
DEFAULT_FIRMWARE = Path(os.environ.get('IERMU_FIRMWARE', str(REPO_ROOT / 'firmware/iblack-8m/extracted/base_rootfs/usr/bin/SysUpdate')))


def crc32(data: bytes) -> int:
    """SysUpdate reflected CRC: initial and final xor 0xffffffef."""
    return (zlib.crc32(data, 0x10) ^ 0x10) & 0xffffffff

def make_header(opcode: int, nonce: bytes, body_length: int = 0, days: int = 1) -> bytes:
    if opcode not in (0x92, 0x93, 0xfe) or len(nonce) != 16:
        raise ValueError('Only telnet control / acknowledgment headers are supported')
    head = bytes([opcode, 0]) + struct.pack('<H', days) + struct.pack('>I', body_length) + nonce
    return head + struct.pack('>I', crc32(head))

def make_nonce(timestamp: int) -> bytes:
    if not 0 <= timestamp <= 0xffffffff:
        raise ValueError('timestamp must fit the firmware 32-bit time field')
    n = bytearray(os.urandom(16))
    n[6], n[8], n[7], n[3] = timestamp.to_bytes(4, 'big')
    return bytes(n)

class FirmwareMath:
    """Execute selected mathematical routines, plus an I/O-stubbed handler test."""
    def __init__(self, firmware: Path, now: int | None = None):
        try:
            from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_INTR
            from unicorn import arm_const
        except ImportError as e:
            raise RuntimeError('Install optional dependency: unicorn==2.1.4') from e
        raw = Path(firmware).read_bytes()
        if hashlib.sha256(raw).hexdigest() != FIRMWARE_SHA256:
            raise ValueError('Unsupported firmware SHA-256; refusing to use offsets from another build')
        if raw[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', raw, 18)[0] != 40:
            raise ValueError('Expected ARM ELF32 little-endian firmware')
        self.reg = arm_const
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map(0x8000, 0x12000)
        phoff = struct.unpack_from('<I', raw, 28)[0]
        phsize, phnum = struct.unpack_from('<HH', raw, 42)
        for i in range(phnum):
            typ, off, va, _, filesz, memsz, _, _ = struct.unpack_from('<IIIIIIII', raw, phoff + i * phsize)
            if typ == 1:
                if not (0x8000 <= va <= va + memsz <= 0x1a000):
                    raise ValueError('Unexpected ELF segment layout')
                self.uc.mem_write(va, raw[off:off+filesz])
        self.heap = 0x01000000
        self.heap_end = self.heap + 0x01000000
        self.uc.mem_map(self.heap, 0x01000000)
        self.uc.mem_map(0x7f000000, 0x20000)
        self.sentinel = 0x7fff0000
        self.uc.mem_map(self.sentinel, 0x1000)
        self.uc.mem_map(0x60000000, 0x1000)  # private RAM, never physical MMIO
        self.uc.mem_write(0x181dc, struct.pack('<I', 0x60000000))
        table = []
        for n in range(256):
            c = n
            for _ in range(8):
                c = (c >> 1) ^ (0xedb88320 if c & 1 else 0)
            table.append(c)
        self.uc.mem_write(0x181e0, struct.pack('<256I', *table))
        self.now = int(time.time()) if now is None else now
        self.incoming = bytearray()
        self.outgoing = []
        self.commands = []
        self.calls = []
        self.failure = None
        self.uc.hook_add(UC_HOOK_CODE, self._hook, begin=0x899c, end=0x8bdc)
        self.uc.hook_add(UC_HOOK_CODE, self._hook, begin=0x8ce4, end=0x8ce4)
        self.uc.hook_add(UC_HOOK_CODE, self._hook, begin=0x8dc8, end=0x8dc8)
        self.uc.hook_add(UC_HOOK_INTR, self._interrupt)

    def _interrupt(self, uc, number, user):
        self.failure = RuntimeError('Firmware syscall / interrupt blocked')
        uc.emu_stop()

    def alloc(self, size: int, data: bytes = b'') -> int:
        if not 0 <= size <= 1024*1024 or len(data) > size:
            raise ValueError('Invalid isolated allocation')
        ptr = self.heap
        self.heap += (size + 15) & ~15
        if self.heap > self.heap_end:
            raise RuntimeError('Emulator heap limit')
        if data:
            self.uc.mem_write(ptr, data)
        return ptr

    def _cstr(self, ptr: int, limit: int = 4096) -> bytes:
        out = bytearray()
        for i in range(limit):
            x = bytes(self.uc.mem_read(ptr+i, 1))
            if x == b'\0':
                return bytes(out)
            out.extend(x)
        raise RuntimeError('Unterminated emulator string')

    def _hook(self, uc, address, size, user):
        # The only imported operations allowed by the isolated routines.
        try:
            R = self.reg
            a = [uc.reg_read(r) for r in (R.UC_ARM_REG_R0, R.UC_ARM_REG_R1, R.UC_ARM_REG_R2)]
            self.calls.append(address)
            if address == 0x8a10:  # malloc
                result = self.alloc(a[0])
            elif address == 0x8bd8:  # free -- private emulator memory only
                result = 0
            elif address == 0x8ba8:  # strlen
                result = len(self._cstr(a[0]))
            elif address == 0x89f8:  # memcpy
                if a[2] > 1024*1024:
                    raise RuntimeError('Oversized emulated memcpy')
                uc.mem_write(a[0], bytes(uc.mem_read(a[1], a[2])))
                result = a[0]
            elif address == 0x8ae8:  # memcmp
                x, y = (bytes(uc.mem_read(p, a[2])) for p in a[:2])
                result = (x > y) - (x < y)
            elif address == 0x8b6c:  # ftime
                uc.mem_write(a[0], struct.pack('<IHHHH', self.now, 0, 0, 0, 0))
                result = 0
            elif address == 0x8a04:  # system -- NEVER executed
                cmd = self._cstr(a[0], 64)
                if cmd not in (b'telnetd', b'killall -9 telnetd'):
                    raise RuntimeError('Unexpected emulated command blocked')
                self.commands.append(cmd.decode('ascii'))
                result = 0
            elif address == 0x8dc8:  # recvexact -- queued memory, NEVER a socket
                count = min(a[2], len(self.incoming))
                if count:
                    uc.mem_write(a[1], bytes(self.incoming[:count]))
                del self.incoming[:count]
                result = count
            elif address == 0x8ce4:  # sendexact -- record only
                if a[2] > 4096:
                    raise RuntimeError('Unexpected emulated response size')
                self.outgoing.append(bytes(uc.mem_read(a[1], a[2])))
                result = a[2]
            else:
                raise RuntimeError(f'Unapproved firmware import at 0x{address:x} blocked')
            uc.reg_write(R.UC_ARM_REG_R0, result & 0xffffffff)
            uc.reg_write(R.UC_ARM_REG_PC, uc.reg_read(R.UC_ARM_REG_LR))
        except Exception as e:
            self.failure = e
            uc.emu_stop()

    def call(self, address: int, *args: int) -> int:
        # Callers cannot select arbitrary firmware entrypoints.
        if address not in (0x16ca4, 0x16b00, 0x15c54, 0x163a8, 0x9ec4, 0x8d34):
            raise ValueError('Firmware entrypoint is not allowlisted')
        R = self.reg
        self.failure = None
        self.uc.reg_write(R.UC_ARM_REG_CPSR, 0x10)  # ARM user mode
        self.uc.reg_write(R.UC_ARM_REG_SP, 0x7f01f000)
        self.uc.reg_write(R.UC_ARM_REG_LR, self.sentinel)
        for r, v in zip((R.UC_ARM_REG_R0, R.UC_ARM_REG_R1, R.UC_ARM_REG_R2, R.UC_ARM_REG_R3), args):
            self.uc.reg_write(r, v)
        self.uc.emu_start(address, self.sentinel, timeout=5_000_000, count=2_000_000)
        if self.failure:
            raise self.failure
        pc = self.uc.reg_read(R.UC_ARM_REG_PC)
        if pc != self.sentinel:
            raise RuntimeError(f'Isolated firmware call did not return (pc=0x{pc:x})')
        return self.uc.reg_read(R.UC_ARM_REG_R0)

    def crypt(self, nonce: bytes, block: bytes, decrypt: bool = False) -> bytes:
        if len(nonce) != 16 or len(block) != 16:
            raise ValueError('Exactly one nonce and one block are required')
        ctx = self.call(0x16ca4)
        n = self.alloc(17, nonce + b'\0')
        if self.call(0x16b00, ctx, n) != 0:
            raise RuntimeError('Firmware nonce setup failed')
        src, dst = self.alloc(16, block), self.alloc(16)
        self.call(0x163a8 if decrypt else 0x15c54, ctx, src, dst)
        return bytes(self.uc.mem_read(dst, 16))

    def handler_test(self, header: bytes, body: bytes, mode: int) -> dict:
        if len(header) != 28 or len(body) != 48 or mode not in (0, 1):
            raise ValueError('Unexpected test vector')
        hp = self.alloc(28, header)
        self.incoming = bytearray(body + make_header(0xfe, header[8:24]))
        self.outgoing, self.commands = [], []
        code = self.call(0x9ec4, 7, hp, 64, mode)
        return {'return': code, 'commands': list(self.commands),
                'reply': [(p[0], p[1]) for p in self.outgoing],
                'reply_crc_valid': all(crc32(p[:24]) == struct.unpack('>I', p[24:28])[0] for p in self.outgoing),
                'queued_bytes_remaining': len(self.incoming)}
