#!/usr/bin/env python3
# Dump FLV video tag structure (H.264/AVCC sanity check).
import collections
import struct
import sys

path = sys.argv[1]
d = open(path, "rb").read()
if d[:3] != b"FLV":
    sys.exit("not an FLV file")
i = struct.unpack(">I", d[5:9])[0] + 4
nal_len = 4
counts = collections.Counter()
fail_left = collections.Counter()
nal_types = collections.Counter()
rows = []
nbad = 0
vt = at = 0
while i + 11 <= len(d):
    t = d[i]
    size = int.from_bytes(d[i + 1:i + 4], "big")
    ts = int.from_bytes(d[i + 4:i + 7], "big") | (d[i + 7] << 24)
    body = d[i + 11:i + 11 + size]
    i += 11 + size + 4
    if t == 8:
        at += 1
        continue
    if t != 9 or not body:
        continue
    vt += 1
    ftype, codec = body[0] >> 4, body[0] & 15
    pkt = body[1] if len(body) > 1 else -1
    payload = body[5:]
    if codec == 7 and pkt == 0:
        nal_len = (payload[4] & 3) + 1
        rows.append("tag#%d AVC seq header profile=%d level=%d nal_length_size=%d avcC=%s" % (
            vt, payload[1], payload[3], nal_len, payload[:48].hex()))
        continue
    j, ok, walk = 0, True, []
    while j < len(payload):
        if j + nal_len > len(payload):
            ok = False
            break
        L = int.from_bytes(payload[j:j + nal_len], "big")
        if L <= 0 or j + nal_len + L > len(payload):
            ok = False
            break
        walk.append((j, L, payload[j + nal_len] & 0x1F))
        nal_types[payload[j + nal_len] & 0x1F] += 1
        j += nal_len + L
    counts[(ftype, pkt, ok)] += 1
    if not ok:
        left = len(payload) - j
        fail_left[left] += 1
        nbad += 1
        if nbad <= 3:
            rows.append("BAD tag#%d ts=%d ftype=%d payload=%d walk=%s fail_at=%d left=%d" % (
                vt, ts, ftype, len(payload), walk[:6], j, left))
            rows.append("    head=%s" % payload[:48].hex())
            rows.append("    at_fail=%s" % payload[j:j + 96].hex())
            rows.append("    tail=%s" % payload[-96:].hex())
            sc = []
            k = payload.find(b"\x00\x00\x01")
            while k != -1 and len(sc) < 8:
                sc.append(k)
                k = payload.find(b"\x00\x00\x01", k + 3)
            rows.append("    annexb_startcodes_at=%s" % sc)
    elif len(rows) < 8:
        rows.append("OK  tag#%d ts=%d ftype=%d payload=%d walk=%s" % (
            vt, ts, ftype, len(payload), walk[:6]))
print("video tags=%d audio tags=%d" % (vt, at))
for k, v in counts.most_common():
    print("  (frametype=%d pkt=%d avcc_ok=%s): %d" % (k[0], k[1], k[2], v))
print("NAL types:", dict(nal_types))
print("bytes left at failure:", dict(fail_left.most_common(8)))
for r in rows:
    print(r)
