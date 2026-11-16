#!/usr/bin/env python3
"""Minimal RTSP probe: OPTIONS, DESCRIBE, SETUP (RTP over the RTSP TCP connection), PLAY, then count RTP/H.264.

Prints aggregate numbers only (status codes, codec, SPS profile/level/size, frames per second, bit rate, NAL type
counts), never the address, credentials, session ids or picture data. The camera address and the root password
are read from private files outside the repository (first line of each).

    rtspprobe.py --address-file PRIVATE/camera-address --password-file PRIVATE/root-password [/stream=0] [seconds]
"""
import argparse, base64, hashlib, os, re, socket, time

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--address-file", required=True)
ap.add_argument("--password-file", required=True)
ap.add_argument("path", nargs="?", default="/stream=0")
ap.add_argument("seconds", nargs="?", type=float, default=6.0)
args = ap.parse_args()
with open(args.address_file) as f:
    cam = f.readline().strip()
with open(args.password_file) as f:
    pw = f.readline().rstrip("\n")
path = args.path if args.path.startswith("/") else "/" + args.path
dur = args.seconds
base = f'rtsp://{cam}:554{path}'
s = socket.create_connection((cam, 554), timeout=10)
st = {'cseq': 0, 'www': None, 'session': None, 'buf': b'', 'nc': 0}

def recv_resp():
    while b'\r\n\r\n' not in st['buf']:
        d = s.recv(65536)
        if not d: raise EOFError('closed')
        st['buf'] += d
    head, _, rest = st['buf'].partition(b'\r\n\r\n')
    lines = head.decode('latin1').split('\r\n')
    code = int(lines[0].split()[1]); hd = {}
    for l in lines[1:]:
        k, _, v = l.partition(':'); hd[k.strip().lower()] = v.strip()
    n = int(hd.get('content-length', '0'))
    while len(rest) < n:
        rest += s.recv(65536)
    st['buf'] = rest[n:]
    return code, hd, rest[:n]

def auth(method, uri):
    w = st['www']
    if w.lower().startswith('digest'):
        p = dict(re.findall(r'(\w+)="?([^",]*)"?', w))
        realm, nonce = p.get('realm', ''), p.get('nonce', '')
        ha1 = hashlib.md5(f'root:{realm}:{pw}'.encode()).hexdigest()
        ha2 = hashlib.md5(f'{method}:{uri}'.encode()).hexdigest()
        if 'qop' in p:
            st['nc'] += 1; nc = f"{st['nc']:08x}"; cn = os.urandom(8).hex()
            r = hashlib.md5(f'{ha1}:{nonce}:{nc}:{cn}:auth:{ha2}'.encode()).hexdigest()
            return (f'Digest username="root", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                    f'response="{r}", qop=auth, nc={nc}, cnonce="{cn}"')
        r = hashlib.md5(f'{ha1}:{nonce}:{ha2}'.encode()).hexdigest()
        return f'Digest username="root", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{r}"'
    return 'Basic ' + base64.b64encode(f'root:{pw}'.encode()).decode()

def req(method, uri, extra=''):
    for attempt in range(2):
        st['cseq'] += 1
        h = f"{method} {uri} RTSP/1.0\r\nCSeq: {st['cseq']}\r\nUser-Agent: probe\r\n"
        if st['www']: h += 'Authorization: ' + auth(method, uri) + '\r\n'
        if st['session']: h += f"Session: {st['session']}\r\n"
        s.sendall((h + extra + '\r\n').encode())
        code, hd, body = recv_resp()
        if code == 401 and attempt == 0 and 'www-authenticate' in hd:
            st['www'] = hd['www-authenticate']; continue
        return code, hd, body

def rbsp(b):
    out = bytearray(); z = 0
    for x in b:
        if z >= 2 and x == 3: z = 0; continue
        out.append(x); z = z + 1 if x == 0 else 0
    return bytes(out)

class Bits:
    def __init__(s2, b): s2.b, s2.i = b, 0
    def u(s2, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((s2.b[s2.i >> 3] >> (7 - (s2.i & 7))) & 1); s2.i += 1
        return v
    def ue(s2):
        z = 0
        while s2.u(1) == 0: z += 1
        return (1 << z) - 1 + s2.u(z)
    def se(s2):
        v = s2.ue(); return (v + 1) // 2 if v & 1 else -(v // 2)

def sps_dim(sps):
    b = Bits(rbsp(sps[1:])); prof = b.u(8); b.u(8); lvl = b.u(8); b.ue()
    if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128):
        if b.ue() == 3: b.u(1)
        b.ue(); b.ue(); b.u(1)
        if b.u(1): return prof, lvl, None
    b.ue(); t = b.ue()
    if t == 0: b.ue()
    elif t == 1:
        b.u(1); b.se(); b.se()
        for _ in range(b.ue()): b.se()
    b.ue(); b.u(1); w = b.ue(); h = b.ue(); fmo = b.u(1)
    if not fmo: b.u(1)
    b.u(1); cl = cr = ct = cb = 0
    if b.u(1): cl, cr, ct, cb = b.ue(), b.ue(), b.ue(), b.ue()
    return prof, lvl, ((w + 1) * 16 - 2 * (cl + cr), (2 - fmo) * (h + 1) * 16 - 2 * (ct + cb))

code, hd, _ = req('OPTIONS', base); print('OPTIONS', code)
code, hd, sdp = req('DESCRIBE', base, 'Accept: application/sdp\r\n'); print('DESCRIBE', code, 'auth', (st['www'] or 'none').split()[0])
sdp = sdp.decode('latin1'); cbase = hd.get('content-base', base + '/')
vid = sdp.split('m=video', 1)[1].split('\nm=', 1)[0] if 'm=video' in sdp else ''
m = re.search(r'a=rtpmap:\d+ ([^\r\n]+)', vid); print('codec', m.group(1) if m else '?')
m = re.search(r'sprop-parameter-sets=([^;,\r\n]+)', vid)
if m:
    prof, lvl, dim = sps_dim(base64.b64decode(m.group(1) + '=='))
    print(f'sps profile={prof} level={lvl} size={dim[0]}x{dim[1]}' if dim else f'sps profile={prof}')
m = re.search(r'a=control:([^\r\n]+)', vid); ctl = m.group(1).strip() if m else ''
url = ctl if ctl.startswith('rtsp://') else (cbase.rstrip('/') + '/' + ctl if ctl and ctl != '*' else base)
code, hd, _ = req('SETUP', url, 'Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n'); print('SETUP', code)
st['session'] = hd.get('session', '').split(';')[0]
code, hd, _ = req('PLAY', base, 'Range: npt=0.000-\r\n'); print('PLAY', code)
t0 = time.time(); pk = by = 0; ts = set(); nal = {}; buf = st['buf']; first = None
s.settimeout(3)
while time.time() - t0 < dur:
    while len(buf) < 4 or (buf[0] == 0x24 and len(buf) < 4 + int.from_bytes(buf[2:4], 'big')):
        d = s.recv(65536)
        if not d: break
        buf += d
    if buf[0] != 0x24:  # RTSP message in between; skip to next '$'
        j = buf.find(b'$', 1); buf = buf[j:] if j > 0 else b''; continue
    ch, n = buf[1], int.from_bytes(buf[2:4], 'big'); p = buf[4:4 + n]; buf = buf[4 + n:]
    if ch != 0 or len(p) < 13: continue
    if first is None: first = time.time()
    pk += 1; by += len(p); ts.add(int.from_bytes(p[4:8], 'big'))
    cc = p[0] & 0x0f; off = 12 + 4 * cc; t = p[off] & 0x1f
    if t == 28 and (p[off + 1] & 0x80): t = p[off + 1] & 0x1f
    elif t == 28: continue
    nal[t] = nal.get(t, 0) + 1
el = time.time() - (first or t0)
try: req('TEARDOWN', base)
except Exception: pass
print(f'rtp packets={pk} frames={len(ts)} seconds={el:.1f} fps={len(ts)/el:.1f} kbps={by*8/el/1000:.0f}')
print('nal types', dict(sorted(nal.items())))
