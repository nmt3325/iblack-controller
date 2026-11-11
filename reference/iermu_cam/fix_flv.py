#!/usr/bin/env python3
# FLV tag payload utilities for this camera; see docs/MEDIA.md.
import struct
import sys

AAC_TRAILER = 89


def readn(f, n):
    b = b""
    while len(b) < n:
        c = f.read(n - len(b))
        if not c:
            break
        b += c
    return b


def fix_payload(p, nal_len, mode):
    j, last = 0, None
    while j < len(p):
        if j + nal_len > len(p):
            break
        L = int.from_bytes(p[j:j + nal_len], "big")
        if L <= 0 or j + nal_len + L > len(p):
            break
        last = j
        j += nal_len + L
    if j >= len(p) or last is None:
        return p, 0
    left = len(p) - j
    if mode == "truncate":
        return p[:j], left
    q = bytearray(p)
    q[last:last + nal_len] = (len(p) - last - nal_len).to_bytes(nal_len, "big")
    return bytes(q), left


def aac_ends_at(raw, m):
    # True if raw[:m] ends with the AAC END element (0b111) + zero byte-alignment
    if m < 2:
        return False
    v = (raw[m - 2] << 8) | raw[m - 1]
    if v & 0xFF == 0:
        return False
    t = (v & -v).bit_length() - 1
    return (v >> t) & 7 == 7


def main():
    src = sys.stdin.buffer if sys.argv[1] == "-" else open(sys.argv[1], "rb")
    dst = sys.stdout.buffer if sys.argv[2] == "-" else open(sys.argv[2], "wb")
    mode = sys.argv[3] if len(sys.argv) > 3 else "truncate"
    hdr = readn(src, 9)
    if len(hdr) < 9 or hdr[:3] != b"FLV":
        sys.exit("not an FLV stream")
    off = struct.unpack(">I", hdr[5:9])[0]
    extra = readn(src, off - 9) if off > 9 else b""
    dst.write(hdr + extra)
    readn(src, 4)
    dst.write(b"\x00\x00\x00\x00")
    nal_len = 4
    fixed = total = afixed = atotal = 0
    while True:
        th = readn(src, 11)
        if len(th) < 11:
            break
        size = int.from_bytes(th[1:4], "big")
        body = readn(src, size)
        if len(body) < size:
            break
        readn(src, 4)
        if th[0] == 9 and len(body) > 5 and (body[0] & 0x0F) == 7:
            if body[1] == 0 and len(body) > 9:
                nal_len = (body[9] & 3) + 1
            elif body[1] == 1:
                total += 1
                p, left = fix_payload(body[5:], nal_len, mode)
                if left:
                    fixed += 1
                    body = body[:5] + p
        elif th[0] == 8 and len(body) > 2 and (body[0] >> 4) == 10 and body[1] == 1:
            atotal += 1
            m = len(body) - 2 - AAC_TRAILER
            if m >= 2 and aac_ends_at(body[2:], m):
                body = body[:2 + m]
                afixed += 1
        th = bytes([th[0]]) + len(body).to_bytes(3, "big") + th[4:]
        dst.write(th + body + struct.pack(">I", 11 + len(body)))
        dst.flush()
    if dst is not sys.stdout.buffer:
        dst.close()
    sys.stderr.write("fix_flv: %d/%d video frames repaired (mode=%s), %d/%d audio frames trimmed\n" % (fixed, total, mode, afixed, atotal))


if __name__ == "__main__":
    main()
