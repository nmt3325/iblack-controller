#!/bin/bash
# iermu AP-mode Wi-Fi setup v3 (run as root: sudo bash iermu_run.sh)
export PATH=/usr/sbin:/usr/bin:/sbin:/bin:$PATH
set +e
SSID="example-home-wifi"
WIFIPWD="REPLACE_AP_WIFI_PSK"
DEV_IP="192.168.58.1"
DEV_PORT="3357"
CAM_SSID="iermu-setup-EXAMPLE"
CAM_PWD="REPLACE_SETUP_PSK"
IFACE="wlp1s0"
CON="iermucam"
STATIC="192.168.58.251/24"
RESTORED=0
rm -f /tmp/iermu_mac

echo "=== iermu camera Wi-Fi setup v3 $(date -Is) ==="
ORIG=$(nmcli -t -f NAME,DEVICE connection show --active | awk -F: -v d="$IFACE" '$2==d{print $1; exit}')
echo "ORIG_WIFI_CONN=$ORIG"

restore() {
  [ "$RESTORED" = 1 ] && return
  RESTORED=1
  echo "--- restore: reverting $IFACE (orig=$ORIG) $(date +%T)"
  nmcli connection down "$CON" >/dev/null 2>&1
  nmcli connection delete "$CON" >/dev/null 2>&1
  if [ -n "$ORIG" ]; then
    nmcli -w 30 connection up "$ORIG" >/dev/null 2>&1 || nmcli device wifi connect "$SSID" ifname "$IFACE" >/dev/null 2>&1
  else
    nmcli device wifi connect "$SSID" ifname "$IFACE" >/dev/null 2>&1
  fi
  sleep 2
  echo "--- restore: $IFACE => $(nmcli -t -f DEVICE,STATE,CONNECTION device status | grep "^$IFACE:")"
  echo "--- restore: ip => $(ip -br addr show $IFACE)"
}
trap restore EXIT

echo "--- creating camera profile"
nmcli connection delete "$CON" >/dev/null 2>&1
nmcli connection add type wifi ifname "$IFACE" con-name "$CON" ssid "$CAM_SSID" \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$CAM_PWD" \
  ipv4.method manual ipv4.addresses "$STATIC" ipv4.never-default yes ipv4.ignore-auto-dns yes \
  ipv6.method ignore connection.autoconnect no >/dev/null 2>&1 \
  && echo "profile added" || echo "profile add FAILED"

echo "--- rescan + associate (up to 30s)"
nmcli device wifi rescan ifname "$IFACE" >/dev/null 2>&1
sleep 3
nmcli -w 30 connection up "$CON" 2>&1 | sed 's/^/up: /'
echo "--- $IFACE => $(nmcli -t -f DEVICE,STATE,CONNECTION device status | grep "^$IFACE:")"
echo "--- ip => $(ip -br addr show $IFACE)"
echo "--- default routes =>"; ip route show default
echo "--- ping $DEV_IP"
ping -c 3 -W 2 "$DEV_IP"

echo "--- handshake v3 (ver/login/param63/auth/wifi-pre/setwifi/wifi-post/save) $(date +%T)"
SSID="$SSID" WIFIPWD="$WIFIPWD" DEV_IP="$DEV_IP" DEV_PORT="$DEV_PORT" python3 - <<'PY'
import socket, os, time, binascii
HOST=os.environ.get("DEV_IP","192.168.58.1"); PORT=int(os.environ.get("DEV_PORT","3357"))
SSID=os.environ["SSID"].encode(); WIFIPWD=os.environ["WIFIPWD"].encode()
LOGIN_USER=b"REPLACE_USER"; LOGIN_PWD=b"REPLACE_PASSWORD"; STAR=b"******"
def hx(b): return binascii.hexlify(b).decode()
def be32(b,o): return (b[o]<<24)|(b[o+1]<<16)|(b[o+2]<<8)|b[o+3]
def asc(b):
    out=[]; cur=""
    for x in b:
        if 32<=x<127: cur+=chr(x)
        else:
            if len(cur)>=3: out.append(cur)
            cur=""
    if len(cur)>=3: out.append(cur)
    return out
def send_cmd(s,main,sub,body=b""):
    n=len(body); s.sendall(bytes([main&255,sub&255,n&255,(n>>8)&255])+body)
def recv_resp(s,settle=1.0,hard=6.0,cap=4096):
    s.settimeout(hard); data=b""
    try:
        while len(data)<4:
            c=s.recv(4-len(data))
            if not c: return data
            data+=c
    except Exception: return data
    s.settimeout(0.4); last=time.time()
    while len(data)<cap and (time.time()-last)<settle:
        try:
            c=s.recv(cap-len(data))
            if not c: break
            data+=c; last=time.time()
        except Exception: break
    return data
def show(tag,r):
    if len(r)>=4: print("%-9s cmd=%d/%d b2=%d b3=%d len=%d hex=%s"%(tag,r[0],r[1],r[2],r[3],len(r),hx(r)))
    else: print("%-9s short len=%d hex=%s"%(tag,len(r),hx(r)))
    if len(r)>4:
        a=asc(r[4:])
        if a: print("  ascii:",a)
    return r
s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(10)
print("connecting %s:%d"%(HOST,PORT))
try:
    s.connect((HOST,PORT))
except Exception as e:
    print("connect FAILED:",e); raise SystemExit(1)
send_cmd(s,5,17); rv=show("ver",recv_resp(s))
if len(rv)>=8 and rv[0]==5 and rv[1]==17 and rv[2]!=255:
    v=(rv[4]<<24)|(rv[5]<<16)|(rv[6]<<8)|rv[7]; print("  m_DvrVer=%d"%((v>>12)&0xFFFF))
b=bytearray(40); b[0:len(LOGIN_USER)]=LOGIN_USER; b[20:20+len(LOGIN_PWD)]=LOGIN_PWD
send_cmd(s,69,6,bytes(b)); rl=show("login",recv_resp(s))
print("  login_ok=%s"%(len(rl)>=8 and rl[0]==69))
send_cmd(s,74,63); rp=show("param63",recv_resp(s))
imac=None; params=rp[4:] if len(rp)>4 else b""
if len(rp)>=4 and rp[0]==74 and rp[1]==63 and rp[2]!=255 and len(params)>=16:
    imac=be32(params,12); mac=":".join("%02x"%x for x in params[0:6])
    print("  MAC=%s iMacType=%d paramlen=%d"%(mac,imac,len(params)))
    try: open("/tmp/iermu_mac","w").write(mac)
    except Exception: pass
else:
    send_cmd(s,74,3); show("param3",recv_resp(s))
if imac is not None and imac!=0:
    ab=bytearray(8); ab[0:4]=bytes([1,2,3,4]); ab[4:8]=bytes([(imac>>24)&255,(imac>>16)&255,(imac>>8)&255,imac&255])
    send_cmd(s,75,63,bytes(ab)); show("auth",recv_resp(s))
else:
    print("  auth skipped (iMacType=%s)"%imac)
send_cmd(s,74,38); show("wifi-pre",recv_resp(s))
w=bytearray(97); w[0]=0; w[1:1+min(len(SSID),32)]=SSID[:32]; w[33:39]=STAR
pl=min(len(WIFIPWD),32); w[65:65+pl]=WIFIPWD[:pl]
send_cmd(s,72,8,bytes(w)); rw=show("setwifi",recv_resp(s,settle=2.0,hard=10.0))
if len(rw)>=4:
    print("RESULT: setwifi reply b2=%d b3=%d"%(rw[2],rw[3]))
else:
    print("RESULT: setwifi NO/short RESPONSE (camera may be switching)")
try:
    send_cmd(s,74,38); show("wifi-post",recv_resp(s))
    send_cmd(s,65,3); show("save",recv_resp(s))
except Exception as e:
    print("post-cmds err:",e)
try: s.close()
except Exception: pass
print("done-python")
PY
PRC=$?
echo "--- python rc=$PRC $(date +%T)"

if [ "$PRC" = 0 ]; then
  echo "--- monitor camera AP (up to 60s)"
  miss=0
  for i in $(seq 1 20); do
    sleep 3
    L=$(iw dev "$IFACE" link 2>/dev/null | awk '/Connected to/{printf "%s ",$3} /SSID:/{printf "%s",$2}')
    [ -z "$L" ] && L="not-associated"
    if ping -c1 -W1 "$DEV_IP" >/dev/null 2>&1; then P=up; miss=0; else P=down; miss=$((miss+1)); fi
    echo "t+$((i*3))s link=[$L] ping=$P"
    if [ "$miss" -ge 3 ]; then echo ">>> camera AP gone - likely switching to home Wi-Fi"; break; fi
  done
fi

restore
trap - EXIT

echo "--- wait 25s for camera to join home LAN"
sleep 25
echo "--- wifi scan for camera AP"
nmcli -t -f SSID,BSSID,CHAN,SIGNAL device wifi list --rescan yes ifname "$IFACE" 2>/dev/null | grep -i iermu || echo "camera AP not visible"
echo "--- LAN sweep 192.168.10.0/24"
for i in $(seq 1 254); do ping -c1 -W1 192.168.10.$i >/dev/null 2>&1 & done; wait
ip neigh show dev "$IFACE" | grep -vE 'FAILED|INCOMPLETE' | sort -t. -k4 -n
CM=$(cat /tmp/iermu_mac 2>/dev/null)
echo "camera MAC (param63) = ${CM:-unknown}"
if ip neigh show dev "$IFACE" | grep -qiE "00:20:1b${CM:+|$CM}"; then echo ">>> CAMERA FOUND ON LAN:"; ip neigh show dev "$IFACE" | grep -iE "00:20:1b${CM:+|$CM}"; else echo "camera not on LAN yet"; fi
echo "=== END $(date -Is) ==="
