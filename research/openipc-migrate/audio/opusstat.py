#!/usr/bin/env python3
"""Summarise an Ogg/Opus capture from majestic: the channel count in OpusHead, the TOC of every packet
(configuration, stereo flag, frame length) and how many seconds the packets decode to.

With the wall-clock length of the capture it also prints decoded/wall-clock: about 0.5 with the channel
bug (10 ms stereo packets stamped as 20 ms), about 1.0 with audio/libmajaudiofix.so. The input is
/audio.opus as saved by curl, or RTSP audio copied into Ogg (ffmpeg -i rtsp://... -vn -c:a copy x.ogg).

usage: opusstat.py FILE [WALLCLOCK_SECONDS]
"""
from __future__ import annotations

import collections
import json
import struct
import sys


def packets(data: bytes):
    """Yields (packet, granule position of the page that completes it) from an Ogg stream."""
    pos, cur = 0, b""
    while True:
        i = data.find(b"OggS", pos)
        if i < 0 or i + 27 > len(data):
            return
        gran = struct.unpack_from("<q", data, i + 6)[0]
        nseg = data[i + 26]
        seg = data[i + 27:i + 27 + nseg]
        off = i + 27 + nseg
        for n in seg:
            cur += data[off:off + n]
            off += n
            if n < 255:
                yield cur, gran
                cur = b""
        pos = off


def frame_ms(config: int) -> float:
    """Frame length of a TOC configuration (RFC 6716, 3.1)."""
    if config < 12:  # SILK
        return (10, 20, 40, 60)[config % 4]
    if config < 16:  # hybrid
        return (10, 20)[config % 2]
    return (2.5, 5, 10, 20)[config % 4]  # CELT


def summary(data: bytes, wall: float | None = None) -> dict:
    head = None
    configs, stereo = collections.Counter(), collections.Counter()
    total_ms, count, grans = 0.0, 0, []
    for pkt, gran in packets(data):
        if pkt.startswith(b"OpusHead"):
            _ver, ch, pre, rate, _gain, fam = struct.unpack_from("<BBHIhB", pkt, 8)
            head = {"channels": ch, "pre_skip": pre, "input_rate": rate, "mapping_family": fam}
            continue
        if not pkt or pkt.startswith(b"OpusTags"):
            continue
        toc = pkt[0]
        config, s, c = toc >> 3, (toc >> 2) & 1, toc & 3
        frames = 1 if c == 0 else 2 if c in (1, 2) else (pkt[1] & 0x3F if len(pkt) > 1 else 0)
        configs[config] += 1
        stereo[s] += 1
        total_ms += frames * frame_ms(config)
        count += 1
        grans.append(gran)
    out = {
        "bytes": len(data), "opus_head": head, "packets": count,
        "toc_config": dict(sorted(configs.items())), "toc_stereo": dict(sorted(stereo.items())),
        "frame_ms": sorted({frame_ms(c) for c in configs}),
        "decoded_seconds": round(total_ms / 1000, 3),
        "granule_first_last": [grans[0], grans[-1]] if grans else None,
    }
    if wall:
        out["wallclock_seconds"] = wall
        out["decoded_per_wallclock"] = round(total_ms / 1000 / wall, 3)
        out["packets_per_second"] = round(count / wall, 1)
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (1, 2):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    with open(argv[0], "rb") as f:
        data = f.read()
    print(json.dumps(summary(data, float(argv[1]) if len(argv) > 1 else None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
