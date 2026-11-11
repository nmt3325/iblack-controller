#!/bin/bash
# One-shot, bounded setup-mode capture for this camera only. Run as root.
set -u -o pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
D=/home/nmt3325/iermu_cam
IF=wlp1s0
MAC=02:00:00:00:00:01
CAM=192.168.10.34
AP=192.168.10.1
PROFILE=iermu-reset-capture
CONF=/etc/hostapd/iermu-ap.conf

make_protocol() {
  python3 - "$D/iermu_run.sh" "$1" "$MAC" <<'PYCODE'
import pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text()
marker = "python3 - <<'PY'\n"
assert source.count(marker) == 1, 'Original protocol boundary changed'
code = source.split(marker, 1)[1].split('\nPY\n', 1)[0]
anchor = 'send_cmd(s,74,38); show("wifi-pre",recv_resp(s))'
assert code.count(anchor) == 1, 'Original identity-check location changed'
guard = 'if locals().get("mac", "").lower() != ' + repr(sys.argv[3]) + ':\n    raise SystemExit("ABORT: unexpected or unreadable camera identity")\n'
code = code.replace(anchor, guard + anchor, 1)
anchor = 'send_cmd(s,72,8,bytes(w)); rw=show('
assert code.count(anchor) == 1, 'Original Wi-Fi command changed'
code = code.replace(anchor, 'send_cmd(s,72,8,bytes(w)); print("WIFI_COMMAND_SENT", flush=True); rw=show(', 1)
compile(code, sys.argv[2], 'exec')
pathlib.Path(sys.argv[2]).write_text(code + '\n')
print('PROTOCOL_CHECK_OK: original handshake retained; target MAC guard added', flush=True)
PYCODE
}

[ "$(id -u)" = 0 ] || { echo 'Root required'; exit 1; }
for cmd in nmcli iw ip systemctl systemd-run iptables python3 flock timeout; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }
done
TARGET=$(sed -n 's/^ssid=//p' "$CONF")
PSK=$(sed -n 's/^wpa_passphrase=//p' "$CONF")
HOTSPOT=$(sed -n 's/^CAM_SSID="\([^"]*\)"$/\1/p' "$D/iermu_run.sh")
HOTSPOT_PSK=$(sed -n 's/^CAM_PWD="\([^"]*\)"$/\1/p' "$D/iermu_run.sh")
[ "$TARGET" = iermu-cam ] && [ -n "$PSK" ] && [ "$HOTSPOT" = iermu-setup-EXAMPLE ] && [ -n "$HOTSPOT_PSK" ] || { echo 'Configuration preflight failed'; exit 1; }
if [ "${1:-}" = --check ]; then
  CHECK=$(mktemp)
  make_protocol "$CHECK"; RC=$?
  rm -f "$CHECK"
  [ "$RC" = 0 ] && echo 'CHECK_OK: no network changes made'
  exit "$RC"
fi
[ "$#" = 0 ] || { echo 'Usage: script [--check]'; exit 1; }
ip route get 1.1.1.1 | grep -q 'dev ens18' || { echo 'ABORT: independent wired uplink not verified'; exit 1; }
exec 9>/run/iermu-reset-capture.lock
flock -n 9 || { echo 'Another capture is already running'; exit 1; }
umask 077
RUN=/run/iermu-reset-capture
mkdir -p "$RUN"
LOG="$D/ap/reset_capture_$(date +%Y%m%d_%H%M%S).log"
exec >>"$LOG" 2>&1
log() { printf '%s %s\n' "$(date -Is)" "$*"; }
phase() { printf '%s\n' "$1" > "$RUN/status"; log "PHASE=$1"; }
AP_RESTORED=0
CHANGED=0

restore_ap() {
  nmcli -w 5 connection down "$PROFILE" >/dev/null 2>&1 || true
  nmcli device set "$IF" managed no || return 1
  if ! systemctl is-active --quiet iermu-fakecloud.service; then
    systemctl reset-failed iermu-fakecloud.service 2>/dev/null || true
    systemd-run --unit=iermu-fakecloud --collect /usr/bin/python3 "$D/fakecloud.py" || return 1
  fi
  iptables -t nat -N IERMU_PRE 2>/dev/null || true
  iptables -t nat -F IERMU_PRE || return 1
  iptables -t nat -A IERMU_PRE -p udp --dport 53 -j DNAT --to-destination "$AP:53" || return 1
  iptables -t nat -A IERMU_PRE -p tcp --dport 53 -j DNAT --to-destination "$AP:53" || return 1
  iptables -t nat -A IERMU_PRE -p udp --dport 3368 -j DNAT --to-destination "$AP:3368" || return 1
  iptables -t nat -A IERMU_PRE -p tcp --dport 3399 -j DNAT --to-destination "$AP:3399" || return 1
  iptables -t nat -A IERMU_PRE -p tcp -d "$AP" -m multiport ! --dports 22,53 -j REDIRECT --to-ports 3399 || return 1
  iptables -t nat -C PREROUTING -i "$IF" -s "$CAM" -j IERMU_PRE 2>/dev/null || iptables -t nat -I PREROUTING 1 -i "$IF" -s "$CAM" -j IERMU_PRE || return 1
  systemctl start iermu-ap.service || return 1
  systemctl start iermu-dhcp.service || return 1
  for unit in iermu-ap.service iermu-dhcp.service iermu-fakecloud.service; do
    systemctl is-active --quiet "$unit" || return 1
  done
  AP_RESTORED=1
  log 'AP, DHCP, fake cloud and camera-only interception restored; no autorevert scheduled'
}
cleanup() {
  RC=$?
  trap - EXIT INT TERM
  if [ "$CHANGED" = 1 ] && [ "$AP_RESTORED" != 1 ]; then
    log 'Restoring AP on exit'
    restore_ap || { phase RESTORE_FAILED; RC=1; }
  fi
  nmcli -w 5 connection delete "$PROFILE" >/dev/null 2>&1 || true
  rm -f "$RUN/protocol.py"
  log "EXIT=$RC"
  exit "$RC"
}
trap cleanup EXIT
trap 'phase INTERRUPTED; exit 130' INT
trap 'phase INTERRUPTED; exit 143' TERM
phase PREPARING
make_protocol "$RUN/protocol.py" || exit 1
systemctl stop iermu-ap-autorevert.timer iermu-ap-autorevert.service 2>/dev/null || true
CHANGED=1
systemctl stop iermu-dhcp.service || exit 1
systemctl stop iermu-ap.service || exit 1
ip link set "$IF" down || exit 1
iw dev "$IF" set type managed || exit 1
ip link set "$IF" up || exit 1
nmcli device set "$IF" managed yes || exit 1
nmcli -w 5 device disconnect "$IF" >/dev/null 2>&1 || true
nmcli -w 5 connection delete "$PROFILE" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$IF" con-name "$PROFILE" ssid "$HOTSPOT" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PSK" ipv4.method manual ipv4.addresses 192.168.58.251/24 ipv4.never-default yes ipv4.ignore-auto-dns yes ipv6.method ignore connection.autoconnect no >/dev/null || exit 1
DEADLINE=$(( $(date +%s) + 1800 ))
printf '%s\n' "$DEADLINE" > "$RUN/deadline_epoch"
printf '%s\n' "$LOG" > "$RUN/log_path"
phase WAITING_FOR_CAMERA_HOTSPOT
log "Waiting for $HOTSPOT until $(date -d "@$DEADLINE" -Is)"
FOUND=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if timeout 15s nmcli -t -f SSID device wifi list --rescan yes ifname "$IF" 2>/dev/null | grep -Fxq "$HOTSPOT"; then
    log 'Expected camera hotspot detected; associating'
    if nmcli -w 15 connection up "$PROFILE" ifname "$IF"; then
      if iw dev "$IF" link | grep -Fq "SSID: $HOTSPOT"; then FOUND=1; break; fi
    fi
    nmcli -w 5 device disconnect "$IF" >/dev/null 2>&1 || true
  fi
  sleep 5
done
if [ "$FOUND" != 1 ]; then phase TIMED_OUT_NO_HOTSPOT; exit 2; fi
phase CONFIGURING_CAMERA
SSID="$TARGET" WIFIPWD="$PSK" DEV_IP=192.168.58.1 DEV_PORT=3357 python3 -u "$RUN/protocol.py" > "$RUN/handshake.log" 2>&1
PRC=$?
cat "$RUN/handshake.log"
if ! grep -Fxq WIFI_COMMAND_SENT "$RUN/handshake.log"; then phase CONFIGURATION_NOT_SENT; exit 3; fi
log "Wi-Fi command sent to verified camera; protocol exit=$PRC. Flash persistence is NOT yet verified."
phase RESTORING_ACCESS_POINT
restore_ap || { phase RESTORE_FAILED; exit 4; }
phase WAITING_FOR_CAMERA_JOIN
for ((i=0; i<45; i++)); do
  if hostapd_cli -p /run/hostapd -i "$IF" sta "$MAC" 2>/dev/null | grep -q AUTHORIZED; then
    phase JOINED_PERSISTENCE_UNTESTED
    hostapd_cli -p /run/hostapd -i "$IF" sta "$MAC" 2>/dev/null | grep -E 'flags|connected_time|signal'
    ping -I "$IF" -c 3 -W 1 "$CAM" || true
    log 'Camera joined iermu-cam. A separate camera power-cycle test is required to verify persistence.'
    exit 0
  fi
  sleep 4
done
phase AP_RESTORED_CAMERA_NOT_JOINED
exit 5
