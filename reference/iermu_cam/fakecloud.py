#!/usr/bin/env python3
import socket, struct, threading, time, os
LOG="/home/nmt3325/iermu_cam/fakecloud.log"
def log(msg):
    line="%s %s"%(time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG,"a") as f:
            f.write(line+"\n"); f.flush()
    except Exception:
        pass
SO_ORIGINAL_DST=80
def orig_dst(sock):
    try:
        data=sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
        port=struct.unpack("!H", data[2:4])[0]
        ip=socket.inet_ntoa(data[4:8])
        return "%s:%d"%(ip,port)
    except Exception:
        return "?"
def hx(b):
    return b[:400].hex()
def handle_tcp(conn, addr):
    od=orig_dst(conn)
    log("TCP OPEN from %s:%d orig=%s"%(addr[0],addr[1],od))
    conn.settimeout(180); total=0
    try:
        while True:
            d=conn.recv(4096)
            if not d:
                log("TCP CLOSE %s:%d orig=%s total=%d"%(addr[0],addr[1],od,total)); break
            total+=len(d)
            log("TCP DATA %s:%d orig=%s len=%d hex=%s"%(addr[0],addr[1],od,len(d),hx(d)))
    except socket.timeout:
        log("TCP IDLE %s:%d orig=%s total=%d"%(addr[0],addr[1],od,total))
    except Exception as e:
        log("TCP ERR %s:%d orig=%s err=%s"%(addr[0],addr[1],od,e))
    finally:
        try: conn.close()
        except Exception: pass
def tcp_server(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    s.bind(("0.0.0.0",port)); s.listen(32)
    log("TCP listening on %d"%port)
    while True:
        try:
            conn,addr=s.accept()
            threading.Thread(target=handle_tcp,args=(conn,addr),daemon=True).start()
        except Exception as e:
            log("TCP accept err %s"%e); time.sleep(0.5)
def udp_server(port):
    s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,1)
    s.bind(("0.0.0.0",port))
    log("UDP listening on %d"%port)
    while True:
        try:
            d,addr=s.recvfrom(4096)
            log("UDP %s:%d len=%d hex=%s"%(addr[0],addr[1],len(d),hx(d)))
        except Exception as e:
            log("UDP err %s"%e); time.sleep(0.5)
if __name__=="__main__":
    for p in (3399,):
        threading.Thread(target=tcp_server,args=(p,),daemon=True).start()
    for p in (3368,):
        threading.Thread(target=udp_server,args=(p,),daemon=True).start()
    log("fakecloud started pid=%d"%os.getpid())
    while True: time.sleep(3600)
