#!/usr/bin/env python3
# Minimal RTMP play probe (Python stdlib only) for the iermu camera on the LAN.
# usage:
#   python3 rtmp_probe.py scan [apps_csv] [streams_csv] [secs]
#   python3 rtmp_probe.py play APP STREAM [secs] [out.flv]
#   python3 rtmp_probe.py p80
import os
import socket
import struct
import sys
import time

HOST = os.environ.get("CAMERA_IP", "192.168.10.34")
PORT = int(os.environ.get("CAMPORT", "1935"))

DEFAULT_APPS = ["live", "hls", "rtmp", "stream", "vod", "iermu", "app", "cam",
                "camera", "flash", "media", "video", "baidu", "ipc"]
DEFAULT_STREAMS = ["live", "stream", "0", "1", "ch0", "ch1", "main", "sub",
                   "stream0", "stream1", "av0", "av0_0", "11", "12", "live0",
                   "livestream", "video", "mainstream", "substream", "channel0",
                   "ch0_0", "0_0", "iermuly842027", "ly842027", "842027"]
BAD_CODES = ("StreamNotFound", "Failed", "Rejected", "Reject", "UnpublishNotify",
             "Play.Stop", "BadName")


def short(v, n=400):
    s = repr(v)
    return s if len(s) <= n else s[:n] + "..."


def amf_enc(v):
    if v is None:
        return b"\x05"
    if isinstance(v, bool):
        return b"\x01" + (b"\x01" if v else b"\x00")
    if isinstance(v, (int, float)):
        return b"\x00" + struct.pack(">d", float(v))
    if isinstance(v, str):
        b = v.encode()
        return b"\x02" + struct.pack(">H", len(b)) + b
    if isinstance(v, dict):
        out = b"\x03"
        for k, x in v.items():
            kb = k.encode()
            out += struct.pack(">H", len(kb)) + kb + amf_enc(x)
        return out + b"\x00\x00\x09"
    raise TypeError(type(v))


def amf_dec(b, i=0):
    t = b[i]
    i += 1
    if t == 0x00:
        return struct.unpack(">d", b[i:i + 8])[0], i + 8
    if t == 0x01:
        return b[i] != 0, i + 1
    if t == 0x02:
        n = struct.unpack(">H", b[i:i + 2])[0]
        i += 2
        return b[i:i + n].decode("utf-8", "replace"), i + n
    if t in (0x03, 0x08):
        if t == 0x08:
            i += 4
        o = {}
        while i + 2 <= len(b):
            n = struct.unpack(">H", b[i:i + 2])[0]
            i += 2
            if n == 0:
                if i < len(b) and b[i] == 0x09:
                    i += 1
                break
            k = b[i:i + n].decode("utf-8", "replace")
            i += n
            v, i = amf_dec(b, i)
            o[k] = v
        return o, i
    if t in (0x05, 0x06):
        return None, i
    if t == 0x0A:
        n = struct.unpack(">I", b[i:i + 4])[0]
        i += 4
        arr = []
        for _ in range(n):
            v, i = amf_dec(b, i)
            arr.append(v)
        return arr, i
    if t == 0x0B:
        return struct.unpack(">d", b[i:i + 8])[0], i + 10
    if t == 0x0C:
        n = struct.unpack(">I", b[i:i + 4])[0]
        i += 4
        return b[i:i + n].decode("utf-8", "replace"), i + n
    raise ValueError("amf0 type 0x%02x at %d" % (t, i - 1))


def amf_all(b):
    out, i = [], 0
    while i < len(b):
        try:
            v, i = amf_dec(b, i)
        except Exception as e:
            out.append("<amf err %s>" % e)
            break
        out.append(v)
    return out


class Rtmp:
    def __init__(self, host, port, timeout=4.0):
        self.s = socket.create_connection((host, port), timeout)
        self.s.settimeout(timeout)
        self.buf = b""
        self.in_cs = 128
        self.out_cs = 128
        self.st = {}
        self.rx = 0
        self.win = 2500000
        self.acked = 0

    def recvn(self, n):
        while len(self.buf) < n:
            c = self.s.recv(65536)
            if not c:
                raise EOFError("connection closed")
            self.buf += c
            self.rx += len(c)
        d, self.buf = self.buf[:n], self.buf[n:]
        return d

    def handshake(self):
        self.s.sendall(b"\x03" + struct.pack(">II", 0, 0) + os.urandom(1528))
        s0 = self.recvn(1)
        s1 = self.recvn(1536)
        self.s.sendall(s1)
        self.recvn(1536)
        return s0

    def send_msg(self, csid, mtype, sid, payload, ts=0):
        out = bytearray([csid & 0x3F])
        out += (ts & 0xFFFFFF).to_bytes(3, "big") + len(payload).to_bytes(3, "big")
        out += bytes([mtype]) + struct.pack("<I", sid)
        for off in range(0, len(payload), self.out_cs):
            if off:
                out.append(0xC0 | (csid & 0x3F))
            out += payload[off:off + self.out_cs]
        self.s.sendall(bytes(out))

    def set_chunk_size(self, n):
        self.send_msg(2, 1, 0, struct.pack(">I", n))
        self.out_cs = n

    def cmd(self, csid, sid, *vals):
        self.send_msg(csid, 20, sid, b"".join(amf_enc(v) for v in vals))

    def read_msg(self):
        b0 = self.recvn(1)[0]
        fmt, csid = b0 >> 6, b0 & 0x3F
        if csid == 0:
            csid = 64 + self.recvn(1)[0]
        elif csid == 1:
            x = self.recvn(2)
            csid = 64 + x[0] + 256 * x[1]
        st = self.st.setdefault(csid, {"ts": 0, "len": 0, "type": 0, "sid": 0,
                                       "delta": 0, "ext": False, "data": b""})
        start = not st["data"]
        if fmt == 0:
            h = self.recvn(11)
            raw = int.from_bytes(h[0:3], "big")
            st["len"] = int.from_bytes(h[3:6], "big")
            st["type"] = h[6]
            st["sid"] = struct.unpack("<I", h[7:11])[0]
            st["ext"] = raw == 0xFFFFFF
            st["ts"] = struct.unpack(">I", self.recvn(4))[0] if st["ext"] else raw
            st["delta"] = 0
        elif fmt in (1, 2):
            h = self.recvn(7 if fmt == 1 else 3)
            raw = int.from_bytes(h[0:3], "big")
            if fmt == 1:
                st["len"] = int.from_bytes(h[3:6], "big")
                st["type"] = h[6]
            st["ext"] = raw == 0xFFFFFF
            st["delta"] = struct.unpack(">I", self.recvn(4))[0] if st["ext"] else raw
            st["ts"] = (st["ts"] + st["delta"]) & 0xFFFFFFFF
        else:
            if st["ext"]:
                self.recvn(4)
            if start:
                st["ts"] = (st["ts"] + st["delta"]) & 0xFFFFFFFF
        need = st["len"] - len(st["data"])
        st["data"] += self.recvn(min(need, self.in_cs))
        if len(st["data"]) >= st["len"]:
            data, st["data"] = st["data"], b""
            if self.rx - self.acked > self.win // 2:
                self.send_msg(2, 3, 0, struct.pack(">I", self.rx & 0xFFFFFFFF))
                self.acked = self.rx
            return st["type"], st["sid"], st["ts"], data
        return None

    def handle_ctrl(self, mtype, data):
        if mtype == 1:
            self.in_cs = struct.unpack(">I", data[:4])[0] & 0x7FFFFFFF
            return "SetChunkSize %d" % self.in_cs
        if mtype == 2:
            return "Abort"
        if mtype == 3:
            return "Ack"
        if mtype == 4:
            ev = struct.unpack(">H", data[:2])[0]
            if ev == 6:
                self.send_msg(2, 4, 0, struct.pack(">H", 7) + data[2:6])
            return "UserCtrl ev=%d %s" % (ev, data[2:].hex())
        if mtype == 5:
            self.win = struct.unpack(">I", data[:4])[0]
            return "WinAckSize %d" % self.win
        if mtype == 6:
            self.send_msg(2, 5, 0, struct.pack(">I", 2500000))
            return "PeerBandwidth %d" % struct.unpack(">I", data[:4])[0]
        return None


def vdesc(b):
    if not b:
        return None
    if b[0] & 0x80:
        return "enhanced-rtmp fourcc=%r" % bytes(b[1:5])
    codec = b[0] & 0x0F
    name = {7: "H.264/AVC", 12: "H.265/HEVC(id12)", 2: "Sorenson H.263",
            4: "VP6", 3: "Screen"}.get(codec, "codec id %d" % codec)
    return "%s [%s]" % (name, bytes(b[:5]).hex())


def adesc(b):
    if not b:
        return None
    f = b[0] >> 4
    return {10: "AAC", 7: "G.711 A-law", 8: "G.711 mu-law", 2: "MP3", 1: "ADPCM",
            11: "Speex", 3: "PCM-LE", 0: "PCM"}.get(f, "sound fmt %d" % f)


def split_aggregate(data, base_ts):
    out, i, first = [], 0, None
    while i + 11 <= len(data):
        t = data[i]
        size = int.from_bytes(data[i + 1:i + 4], "big")
        ts = int.from_bytes(data[i + 4:i + 7], "big") | (data[i + 7] << 24)
        if first is None:
            first = ts
        out.append((t, base_ts + (ts - first), data[i + 11:i + 11 + size]))
        i += 11 + size + 4
    return out


def session(app, stream, secs=3.0, flv=None, verbose=True, idle=4.0):
    log = []
    res = {"app": app, "stream": stream, "connect": None, "play": [], "v": 0,
           "vbytes": 0, "a": 0, "abytes": 0, "vcodec": None, "acodec": None,
           "meta": None, "end": None, "log": log}
    phase = "tcp"
    out = None
    base = None
    try:
        r = Rtmp(HOST, PORT, timeout=idle)
        phase = "handshake"
        r.handshake()
        r.set_chunk_size(4096)
        tc = "rtmp://%s:%d/%s" % (HOST, PORT, app)
        r.cmd(3, 0, "connect", 1.0, {"app": app, "flashVer": "LNX 9,0,124,2",
                                     "tcUrl": tc, "fpad": False, "capabilities": 15.0,
                                     "audioCodecs": 4071.0, "videoCodecs": 252.0,
                                     "videoFunction": 1.0})
        phase = "connect"
        play_t = None
        t_end = time.time() + 15 + secs
        if flv:
            out = sys.stdout.buffer if flv == "-" else open(flv, "wb")
            out.write(b"FLV\x01\x05\x00\x00\x00\x09\x00\x00\x00\x00")
        while time.time() < t_end:
            if play_t is not None and time.time() - play_t >= secs:
                res["end"] = "collected %.1fs" % secs
                break
            try:
                m = r.read_msg()
            except socket.timeout:
                res["end"] = "idle timeout in %s" % phase
                break
            if m is None:
                continue
            mtype, msid, ts, data = m
            c = r.handle_ctrl(mtype, data)
            if c:
                if verbose:
                    log.append(c)
                continue
            if mtype in (20, 17):
                vals = amf_all(data[1:] if mtype == 17 else data)
                name = vals[0] if vals else None
                info = next((v for v in vals[1:] if isinstance(v, dict) and "code" in v), None)
                log.append("CMD " + short(vals, 300))
                if phase == "connect" and name in ("_result", "_error"):
                    res["connect"] = "%s %s %s" % (name, info.get("code") if info else "",
                                                   info.get("description", "") if info else "")
                    if name == "_error":
                        res["end"] = "connect rejected"
                        break
                    phase = "createStream"
                    r.cmd(3, 0, "createStream", 2.0, None)
                elif phase == "createStream" and name in ("_result", "_error"):
                    if name == "_error":
                        res["end"] = "createStream error"
                        break
                    sid = int(vals[3]) if len(vals) > 3 and isinstance(vals[3], float) else 1
                    phase = "play"
                    r.send_msg(2, 4, 0, struct.pack(">HII", 3, sid, 3000))
                    r.cmd(8, sid, "play", 0.0, None, stream, -2000.0)
                    play_t = time.time()
                elif name == "onStatus" and info:
                    code = str(info.get("code"))
                    d = info.get("description")
                    res["play"].append(code + (" (%s)" % d if d else ""))
                    if any(x in code for x in BAD_CODES):
                        res["end"] = "play status " + code
                        break
            elif mtype in (8, 9, 18, 22):
                subs = split_aggregate(data, ts) if mtype == 22 else [(mtype, ts, data)]
                for (t, sts, body) in subs:
                    if t == 9:
                        res["v"] += 1
                        res["vbytes"] += len(body)
                        if res["vcodec"] is None:
                            res["vcodec"] = vdesc(body)
                    elif t == 8:
                        res["a"] += 1
                        res["abytes"] += len(body)
                        if res["acodec"] is None:
                            res["acodec"] = adesc(body)
                    elif t == 18:
                        res["meta"] = short(amf_all(body), 600)
                    if out and t in (8, 9, 18):
                        if base is None:
                            base = sts
                        rel = max(0, sts - base)
                        tag = (bytes([t]) + len(body).to_bytes(3, "big")
                               + (rel & 0xFFFFFF).to_bytes(3, "big")
                               + bytes([(rel >> 24) & 0xFF]) + b"\x00\x00\x00")
                        out.write(tag + body + struct.pack(">I", len(tag) + len(body)))
                        if flv == "-": out.flush()
            else:
                log.append("msg type %d len %d" % (mtype, len(data)))
    except EOFError:
        res["end"] = "closed by camera during %s" % phase
    except Exception as e:
        res["end"] = "error during %s: %r" % (phase, e)
    finally:
        if out and out is not sys.stdout.buffer:
            out.close()
        try:
            r.s.close()
        except Exception:
            pass
    return res


def fmt_res(r):
    return ("app=%r stream=%r connect=%s play=%s video=%d msgs/%d B %s audio=%d msgs/%d B %s end=%s" % (
        r["app"], r["stream"], r["connect"], r["play"], r["v"], r["vbytes"], r["vcodec"],
        r["a"], r["abytes"], r["acodec"], r["end"]))


def p80():
    h = HOST.encode()
    tests = (("banner", None),
             ("GET", b"GET / HTTP/1.1\r\nHost: " + h + b"\r\nUser-Agent: curl/8\r\nAccept: */*\r\n\r\n"),
             ("RTMPT", b"POST /fcs/ident2 HTTP/1.1\r\nHost: " + h
              + b"\r\nContent-Type: application/x-fcs\r\nContent-Length: 1\r\n\r\n\x00"),
             ("cms-ver", bytes([5, 17, 0, 0])))
    for label, payload in tests:
        t0 = time.time()
        try:
            s = socket.create_connection((HOST, 80), 4)
            s.settimeout(3)
            if payload:
                s.sendall(payload)
            d = b""
            try:
                while time.time() - t0 < 3:
                    c = s.recv(4096)
                    if not c:
                        d += b"<EOF>"
                        break
                    d += c
            except socket.timeout:
                d += b"<timeout>"
            s.close()
            print("p80", label, "->", repr(d[:600]), "(%.2fs)" % (time.time() - t0), flush=True)
        except Exception as e:
            print("p80", label, "EXC", repr(e), flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        apps = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else DEFAULT_APPS
        streams = sys.argv[3].split(",") if len(sys.argv) > 3 and sys.argv[3] else ["live"]
        if streams == ["ALL"]:
            streams = DEFAULT_STREAMS
        secs = float(sys.argv[4]) if len(sys.argv) > 4 else 2.5
        for app in apps:
            for st in streams:
                r = session(app, st, secs, None, verbose=False)
                print(fmt_res(r), flush=True)
                for line in r["log"][:4]:
                    print("     " + line, flush=True)
                if r["meta"]:
                    print("     meta " + r["meta"], flush=True)
    elif mode == "play":
        app, st = sys.argv[2], sys.argv[3]
        secs = float(sys.argv[4]) if len(sys.argv) > 4 else 8.0
        flv = sys.argv[5] if len(sys.argv) > 5 else None
        r = session(app, st, secs, flv, verbose=True)
        log_out = sys.stderr if flv == "-" else sys.stdout
        print(fmt_res(r), file=log_out, flush=True)
        for line in r["log"]:
            print("  " + line, file=log_out, flush=True)
        if r["meta"]:
            print("meta " + r["meta"], file=log_out, flush=True)
    elif mode == "p80":
        p80()


if __name__ == "__main__":
    main()
