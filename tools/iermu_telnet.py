#!/usr/bin/env python3
"""Narrow, opt-in SysUpdate telnet control for the fingerprinted iBlack firmware.

Firmware code executes only in Unicorn memory. All external functions are stubbed;
no emulated system(), network operation, file access, or syscall reaches the host.
The network client permits only the independently validated 0x92/0x93 requests.
"""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import time
import zlib

FIRMWARE_SHA256 = 'c76e218d9dffde035ca5ed28cae89fa2dd679c539f647f6329f8dcf083e0c6d9'
DEFAULT_FIRMWARE = Path(__file__).resolve().parents[1] / 'firmware/iblack-8m/extracted/base_rootfs/usr/bin/SysUpdate'
OPCODES = {'enable': 0x92, 'disable': 0x93}


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


def make_request(math: FirmwareMath, action: str, timestamp: int | None = None,
                 nonce: bytes | None = None, bad_plain_crc: bool = False,
                 maintenance_time: bool = False) -> tuple[bytes, bytes]:
    if action not in OPCODES:
        raise ValueError('Only enable / disable actions are supported')
    if nonce is None:
        nonce = make_nonce(math.now if timestamp is None else timestamp)
    plain = bytes(12)
    check = crc32(plain) ^ int(bad_plain_crc)
    block = math.crypt(nonce, plain + struct.pack('>I', check))
    body = struct.pack('>II', 0x01000001, 12) + bytes(20) + struct.pack('>I', crc32(block)) + block
    return make_header(OPCODES[action], nonce, len(body), days=0xfead if maintenance_time else 1), body


def offline_selftest(firmware: Path) -> dict:
    now = int(time.time())
    math = FirmwareMath(firmware, now)
    tests = {}
    for nonce in (make_nonce(now), bytes(16), b'A' * 16):
        block = bytes(range(16))
        if math.crypt(nonce, math.crypt(nonce, block), decrypt=True) != block:
            raise AssertionError('Firmware encrypt/decrypt roundtrip failed')
    tests['cipher_roundtrip_cases'] = 3
    for action, mode, expected in (('enable', 0, 'telnetd'), ('disable', 1, 'killall -9 telnetd')):
        h, b = make_request(math, action)
        r = math.handler_test(h, b, mode)
        tests[action] = r
        assert r['return'] == 0 and r['commands'] == [expected] and r['reply'] == [(254, 0)]
        assert r['reply_crc_valid'] and r['queued_bytes_remaining'] == 0
    h, b = make_request(math, 'enable')
    damaged = bytearray(b)
    damaged[-1] ^= 1
    for name, header, body in [('bad_cipher_crc', h, bytes(damaged)),
                               ('bad_plain_crc', *make_request(math, 'enable', bad_plain_crc=True)),
                               ('expired_time', *make_request(math, 'enable', timestamp=now-40*86400))]:
        r = math.handler_test(header, body, 0)
        tests[name] = r
        assert r['return'] == 0xffffffff and not r['commands'] and r['reply'][0][0] == 255
        assert r['reply_crc_valid']
    h, b = make_request(math, 'enable', timestamp=now-40*86400, maintenance_time=True)
    r = math.handler_test(h, b, 0)
    tests['maintenance_time'] = r
    assert r['return'] == 0 and r['commands'] == ['telnetd'] and r['reply'] == [(254, 0)]
    return {'firmware_sha256': FIRMWARE_SHA256, 'network_calls': 0, 'host_commands_executed': 0, 'tests': tests, 'passed': True}


def private_ipv4(host: str) -> str:
    addr = ipaddress.IPv4Address(host)
    if not any(addr in n for n in (ipaddress.IPv4Network('10.0.0.0/8'), ipaddress.IPv4Network('172.16.0.0/12'), ipaddress.IPv4Network('192.168.0.0/16'))):
        raise ValueError('Use an explicit RFC1918 camera IPv4 address')
    if addr.packed[-1] in (0, 255):
        raise ValueError('Use a unicast camera address')
    return str(addr)


def status(host: str, timeout: float = 2) -> dict:
    """Port check only; never supplies a username or password."""
    host = private_ipv4(host)
    result = {'host': host, 'port': 23, 'open': False, 'login_prompt': False}
    try:
        with socket.create_connection((host, 23), timeout=timeout) as s:
            result['open'] = True
            s.settimeout(timeout)
            data = bytearray()
            end = time.monotonic() + timeout
            while time.monotonic() < end and len(data) < 4096:
                try:
                    chunk = s.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data.extend(chunk)
                # Decline options; do not send application-level credentials/data.
                reply = bytearray()
                for i in range(len(chunk)-2):
                    if chunk[i] == 255 and chunk[i+1] in (251, 253):
                        reply.extend((255, 254 if chunk[i+1] == 251 else 252, chunk[i+2]))
                if reply:
                    s.sendall(reply)
                if b'login:' in data.lower() or b'username:' in data.lower():
                    result['login_prompt'] = True
                    break
            result['banner_sha256'] = hashlib.sha256(data).hexdigest()
            result['banner_bytes'] = len(data)
    except ConnectionRefusedError:
        result['reason'] = 'connection_refused'
    except OSError as e:
        result['reason'] = type(e).__name__
    return result


def recv_exact(s: socket.socket, count: int) -> bytes:
    out = bytearray()
    while len(out) < count:
        b = s.recv(count-len(out))
        if not b:
            raise RuntimeError('Camera closed the control connection before replying')
        out.extend(b)
    return bytes(out)


def apply_request(host: str, action: str, firmware: Path, timeout: float, timestamp: int | None = None,
                  maintenance_time: bool = False) -> dict:
    host = private_ipv4(host)
    if action not in OPCODES:
        raise ValueError('Invalid action')
    math = FirmwareMath(firmware)
    h, b = make_request(math, action, timestamp=timestamp, maintenance_time=maintenance_time)
    checked = math.handler_test(h, b, 0 if action == 'enable' else 1)
    expected = 'telnetd' if action == 'enable' else 'killall -9 telnetd'
    if checked['return'] != 0 or checked['commands'] != [expected] or checked['reply'] != [(254, 0)]:
        raise RuntimeError('Offline firmware validation rejected request; nothing sent')
    before = status(host)
    already = before['open'] if action == 'enable' else before.get('reason') == 'connection_refused'
    if already:
        return {'host': host, 'action': action, 'sent': False, 'already_in_requested_state': True,
                'confirmed': before.get('login_prompt', False) if action == 'enable' else True, 'before': before}
    with socket.create_connection((host, 3508), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(h + b)
        reply = recv_exact(s, 28)
        valid_crc = crc32(reply[:24]) == struct.unpack('>I', reply[24:28])[0]
        if not valid_crc:
            raise RuntimeError('Camera reply has invalid header CRC; no further command sent')
        # Handler waits for a 28-byte ACK following a successful reply.
        if reply[0:2] == b'\xfe\0':
            s.sendall(make_header(0xfe, h[8:24]))
    time.sleep(1)
    after = status(host)
    return {'host': host, 'action': action, 'sent': True, 'request_bytes': len(h)+len(b),
            'request_sha256': hashlib.sha256(h+b).hexdigest(), 'maintenance_time': maintenance_time,
            'response_opcode': reply[0], 'response_status': reply[1], 'response_crc_valid': valid_crc,
            'before': before, 'after': after,
            'confirmed': reply[:2] == b'\xfe\0' and
                ((after['open'] and after['login_prompt']) if action == 'enable' else
                 after.get('reason') == 'connection_refused')}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('status', 'selftest', 'enable', 'disable'))
    p.add_argument('--host', help='Explicit private camera IPv4; no scanning')
    p.add_argument('--firmware', type=Path, default=DEFAULT_FIRMWARE)
    p.add_argument('--apply', action='store_true', help='Required to transmit enable/disable requests')
    p.add_argument('--timeout', type=float, default=8)
    p.add_argument('--timestamp', type=int, help='Request timestamp, only if camera clock differs from host')
    p.add_argument('--maintenance-time', action='store_true',
                   help='Use firmware built-in maintenance date flag (for unsynchronized camera clocks)')
    a = p.parse_args()
    if not 0 < a.timeout <= 60:
        p.error('--timeout must be between 0 and 60 seconds')
    if a.action == 'selftest':
        result = offline_selftest(a.firmware)
    else:
        if not a.host:
            p.error('--host is required')
        if a.action == 'status':
            result = status(a.host)
        elif not a.apply:
            p.error('Use --apply to explicitly enable/disable telnet')
        else:
            result = apply_request(a.host, a.action, a.firmware, a.timeout, a.timestamp, a.maintenance_time)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('confirmed', result.get('passed', True)) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({'error': str(exc), 'confirmed': False}, ensure_ascii=False))
        raise SystemExit(1)
