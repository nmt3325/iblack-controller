#!/usr/bin/env python3
"""Offline-only SysUpdate v5/bakfs envelope builder; no transport or flash I/O.
Offsets are valid only for the SHA-256 pinned in iermu_telnet.FirmwareMath.
This is a test-vector generator, NOT a ready-to-flash firmware image tool.
"""
import struct
import sys
import zlib
from firmware_math import FirmwareMath, DEFAULT_FIRMWARE, make_nonce


def crc32(data):
    return (zlib.crc32(data, 0x10) ^ 0x10) & 0xffffffff


def header(opcode, nonce, body_length, days=0xfead):
    if len(nonce) != 16 or not 0 <= days <= 0xffff:
        raise ValueError('Invalid nonce / time window')
    h = bytes([opcode, 0]) + struct.pack('<H', days) + struct.pack('>I', body_length) + nonce
    return h + struct.pack('>I', crc32(h))


class Packer:
    def __init__(self, now=1700000000):
        self.m = FirmwareMath(DEFAULT_FIRMWARE, now=now)

    def ecb(self, nonce, data, decrypt=False):
        if len(data) % 16 or len(nonce) != 16:
            raise ValueError('RC6 requires 16-byte blocks and a 16-byte nonce')
        ctx = self.m.call(0x16ca4)
        n = self.m.alloc(17, nonce + b'\0')
        if self.m.call(0x16b00, ctx, n) != 0:
            raise RuntimeError('Firmware nonce setup failed')
        src, dst = self.m.alloc(16), self.m.alloc(16)
        out = bytearray()
        for pos in range(0, len(data), 16):
            self.m.uc.mem_write(src, data[pos:pos+16])
            self.m.call(0x163a8 if decrypt else 0x15c54, ctx, src, dst)
            out.extend(self.m.uc.mem_read(dst, 16))
        return bytes(out)

    def pack(self, opcode, image, nonce=None, maintenance=True, image_type=5,
             bad_plain_crc=False, magic=0x01000001):
        if opcode != 0x94:
            raise ValueError('Only the offline-tested 0x94/v5 body is supported')
        if not 0 < len(image) <= 256 * 1024 - 8:
            raise ValueError('Invalid offline test image size')
        if nonce is None:
            nonce = make_nonce(self.m.now)
        # The final FOUR bytes of the aligned plaintext are the inner CRC.
        padded = image + bytes((-(len(image) + 4)) % 16)
        plain = padded + struct.pack('>I', crc32(padded) ^ int(bad_plain_crc))
        ct = self.ecb(nonce, plain)
        # A 32-byte body header; image_type=5 is checked by sub_9498.
        body = struct.pack('>III', magic, len(image), image_type) + bytes(16)
        body += struct.pack('>I', crc32(ct)) + ct
        return header(opcode, nonce, len(body), 0xfead if maintenance else 1), body

    def verify(self, head, body, image):
        if len(head) != 28 or len(body) < 48 or (len(body) - 32) % 16:
            return {'all_ok': False, 'reason': 'invalid envelope sizes'}
        ct = body[32:]
        plain = self.ecb(head[8:24], ct, decrypt=True)
        checks = {
            'header_crc_ok': crc32(head[:24]) == struct.unpack('>I', head[24:28])[0],
            'body_length_ok': len(body) == struct.unpack('>I', head[4:8])[0],
            'magic_ok': struct.unpack('>I', body[:4])[0] == 0x01000001,
            'image_length_ok': struct.unpack('>I', body[4:8])[0] == len(image),
            'image_type_ok': struct.unpack('>I', body[8:12])[0] == 5,
            'cipher_crc_ok': crc32(ct) == struct.unpack('>I', body[28:32])[0],
            'plain_crc_ok': crc32(plain[:-4]) == struct.unpack('>I', plain[-4:])[0],
            'image_roundtrip_ok': plain[:len(image)] == image,
        }
        checks['all_ok'] = all(checks.values())
        return checks


if __name__ == '__main__':
    p = Packer()
    for size in (1, 12, 13, 16, 63, 64, 65):
        image = bytes((i * 37 + 11) % 256 for i in range(size))
        h, b = p.pack(0x94, image)
        assert p.verify(h, b, image)['all_ok']
    print('PASS: 7 offline envelope/CRC/RC6 roundtrips; no network or flash I/O')
