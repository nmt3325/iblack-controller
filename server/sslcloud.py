#!/usr/bin/env python3
# SSLv3-capable logging HTTPS endpoint for the iermu camera (api.iermu.com emulation)
import socket, threading, time, sys, os, json, traceback
sys.path.insert(0, '/home/nmt3325/iermu_cam/pylib')
from tlslite.api import TLSConnection, HandshakeSettings, X509CertChain, parsePEMKey
D = '/home/nmt3325/iermu_cam/stab'
LOG = D + '/sslcloud.log'
RESP = D + '/responses.json'
PORT = int(os.environ.get('PORT', '8443'))
lock = threading.Lock()
def log(msg):
    line = '%s %s' % (time.strftime('%m-%d %H:%M:%S'), msg)
    with lock:
        with open(LOG, 'a') as f:
            f.write(line + '\n')
chain = X509CertChain(); chain.parsePemList(open(D + '/cert.pem').read())
priv = parsePEMKey(open(D + '/key.pem').read(), private=True)
def settings():
    s = HandshakeSettings()
    s.minVersion = (3, 0); s.maxVersion = (3, 3)
    s.cipherNames = ['aes256', 'aes128', '3des', 'rc4']
    s.keyExchangeNames = ['rsa', 'dhe_rsa', 'ecdhe_rsa']
    s.macNames = ['sha', 'sha256', 'md5']
    return s
def pick_response(method, path, body):
    try:
        rules = json.load(open(RESP))
    except Exception:
        rules = []
    for r in rules:
        if r.get('match', '') in path:
            return r.get('status', 200), r.get('body', '{}')
    return 200, '{}'
def handle(sock, addr):
    tag = '%s:%d' % addr
    conn = TLSConnection(sock)
    try:
        conn.handshakeServer(certChain=chain, privateKey=priv, settings=settings())
    except Exception as e:
        log('HS-FAIL %s %s: %s' % (tag, type(e).__name__, e)); sock.close(); return
    log('HS-OK %s version=%s cipher=%s' % (tag, conn.version, conn.getCipherName()))
    try:
        conn.sock.settimeout(30)
        while True:
            data = b''
            while b'\r\n\r\n' not in data and b'\n\n' not in data:
                c = conn.recv(65536)
                if not c:
                    if data: log('REQ-PARTIAL %s %r' % (tag, data[:2000]))
                    log('CLOSE %s' % tag); return
                data += c
            delimiter = b'\r\n\r\n' if b'\r\n\r\n' in data else b'\n\n'
            head, _, rest = data.partition(delimiter)
            lines = head.decode('latin1').splitlines()
            clen = 0
            for l in lines[1:]:
                if l.lower().startswith('content-length:'):
                    clen = int(l.split(':', 1)[1].strip() or 0)
            while len(rest) < clen:
                c = conn.recv(65536)
                if not c: break
                rest += c
            log('REQ %s %s | headers=%r | body=%r' % (tag, lines[0], lines[1:], rest[:4000]))
            parts = lines[0].split(' ')
            method, path = (parts + ['', ''])[:2]
            status, body = pick_response(method, path, rest)
            b = body.encode() if isinstance(body, str) else body
            resp = ('HTTP/1.1 %d OK\r\nServer: nginx\r\nContent-Type: application/json;charset=UTF-8\r\nContent-Length: %d\r\nConnection: keep-alive\r\n\r\n' % (status, len(b))).encode() + b
            conn.send(resp)
            log('RESP %s %d %r' % (tag, status, b[:300]))
    except Exception as e:
        log('ERR %s %s: %s' % (tag, type(e).__name__, e))
    finally:
        try: conn.close()
        except Exception: pass
def main():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', PORT)); s.listen(16)
    log('sslcloud listening on %d pid=%d' % (PORT, os.getpid()))
    while True:
        c, a = s.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()
if __name__ == '__main__':
    main()
