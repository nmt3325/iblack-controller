#!/bin/sh
# Non-persistent trial: move wlan0 to the network described by $NEW (built with wlan_conf).
# Reverts to the network stored in the U-Boot env (wlanssid/wlanpass) unless $KEEP appears
# within $1 seconds (default 300). Nothing is written to flash. If the old gateway cannot be
# reached after the revert, the camera reboots; the env still holds the old network.
# The log never contains the SSID or the passphrase.
# Run detached:  ( trap "" HUP; exec sh wifi-switch-trial.sh 300 ) </dev/null >/dev/null 2>&1 &
# The WIFI_TRIAL_* overrides exist for the tests; on the camera the defaults are used.
LOG=${WIFI_TRIAL_LOG:-/tmp/wifi-trial.log}
NEW=${WIFI_TRIAL_NEW:-/tmp/wpa_trial.conf}
KEEP=${WIFI_TRIAL_KEEP:-/tmp/wifi-trial.keep}
OLD=${WIFI_TRIAL_OLD:-/tmp/wpa_supplicant.conf}
WAIT=${1:-300}
PIDF=${WIFI_TRIAL_PIDFILE:-/var/run/udhcpc.wlan0.pid}

log() { echo "$(cut -d' ' -f1 /proc/uptime) $*" >> "$LOG"; }

sleep 3
[ -s "$NEW" ] || { log "missing $NEW"; exit 1; }
rm -f "$KEEP"
ARGS=$(tr '\0' ' ' < "/proc/$(cat $PIDF 2>/dev/null)/cmdline" 2>/dev/null)
case "$ARGS" in udhcpc*) ;; *) ARGS="udhcpc -A 0 -T 1 -t 5 -R -b -O search -p $PIDF -i wlan0" ;; esac
ORIG_GW=$(ip route | awk '/^default/ && / dev wlan0/ {print $3; exit}')
log "start wait=$WAIT"

stop_wlan() {
	p=$(cat "$PIDF" 2>/dev/null)
	[ -n "$p" ] && kill "$p" 2>/dev/null
	sleep 1
	killall -q wpa_supplicant
	i=0
	while pidof wpa_supplicant >/dev/null && [ $i -lt 10 ]; do sleep 1; i=$((i + 1)); done
	ip -4 addr flush dev wlan0
}

start_wlan() {
	wpa_supplicant -B -i wlan0 -D nl80211,wext -c "$1" >> "$LOG" 2>&1
	sleep 3
	$ARGS >> "$LOG" 2>&1
	i=0
	while [ $i -lt 60 ]; do
		ip -4 addr show wlan0 | grep -q 'inet ' && return 0
		sleep 1; i=$((i + 1))
	done
	return 1
}

state() {
	log "addr: $(ip -4 -o addr show wlan0 | awk '{print $4}')"
	log "route: $(ip route | grep '^default' | tr '\n' ';')"
	log "link: $(iwconfig wlan0 2>/dev/null | grep -oE 'Frequency:[^ ]+|Bit Rate[=:][^ ]+|Signal level[=:][^ ]+' | tr '\n' ' ')"
}

stop_wlan
if start_wlan "$NEW"; then log "joined: lease ok"; else log "joined: no lease"; fi
state
gw=$(ip route | awk '/^default/ && / dev wlan0/ {print $3; exit}')
[ -n "$gw" ] && log "ping gw: $(ping -c 3 -W 2 "$gw" >/dev/null 2>&1 && echo ok || echo fail)"

t=0
while [ $t -lt "$WAIT" ]; do
	[ -e "$KEEP" ] && { log "keep flag seen at t=$t; staying"; exit 0; }
	sleep 5; t=$((t + 5))
done

log "no keep flag after ${WAIT}s; reverting"
stop_wlan
wlan_conf "$(fw_printenv -n wlanssid)" "$(fw_printenv -n wlanpass)" > "$OLD"
if start_wlan "$OLD"; then log "reverted: lease ok"; else log "reverted: no lease"; fi
state
i=0
while [ $i -lt 12 ]; do
	ping -c 1 -W 2 "$ORIG_GW" >/dev/null 2>&1 && { log "reverted: gw reachable"; exit 0; }
	sleep 5; i=$((i + 1))
done
log "revert failed; rebooting"
sync
reboot
