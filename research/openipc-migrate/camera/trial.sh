#!/bin/sh
# trial.sh -- one-shot trial of the OpenIPC kernel with the STOCK userland (iBlack, Wi-Fi only).
#
# Lives in the trial payload SquashFS (mtd6 "tp", inside the stock bakfs area)
# and is exec'd by the migration runapp.sh only when /proc/cmdline carries
# " omtrial=1", i.e. the vendor U-Boot booted the trial kernel from the armed
# env, which had ALREADY saved the stock bootcmd: the next boot is stock again
# whatever happens here. Runs under the stock BusyBox 1.18.4 sh: no $((...)),
# no printf/head/tr/sed/sha256sum, its dd takes only if/of/bs/count/skip/seek
# (conv= fails with rc 1) and its grep has no -x. Writable partitions in the trial
# layout are only tl (64 KiB log mailbox at 0x70000) and senv (vendor env); this script
# writes tl (log checkpoints) and, only if the vendor env still equals the
# armed image (saveenv failed), restores the stock env into senv.
# Steps: check kernel/partition table, load the pinned OpenIPC watchdog with its kernel
# feeder enabled (normal OpenIPC load_hisilicon behavior), stop SysUpdate, eth0 down, Wi-Fi from
# the OpenIPC cfg80211.ko + mt7601sta.ko and the MT7601USTA.dat built by the
# host, explicit WPA2 authentication, DHCP, report to the LAN host (status + log upload; the stock wget sends --post-data
# form-escaped and serve_migration.py decodes it), reboot -f.
# Prints/uploads no Wi-Fi credentials (the .dat, wpa.conf and auth.raw are never logged).
umask 077
PATH=${OMT_TEST_PATH:-/sbin:/usr/sbin:/bin:/usr/bin}; export PATH
P=${OMT_PAYLOAD:-/dev/omt/p}
W=${OMT_WORK:-/dev/omt}
CONF=${OMT_CONF:-/app/openipc-migrate/migrate.conf}
MTDP=${OMT_PROC_MTD:-/proc/mtd}
CMDL=${OMT_PROC_CMDLINE:-/proc/cmdline}
DEVS=${OMT_PROC_DEVICES:-/proc/devices}
NETD=${OMT_NET_DIR:-/sys/class/net}
LOG="$W/trial.log"
mkdir -p "$W" || reboot -f
: > "$LOG"
up(){ awk '{print $1}' /proc/uptime 2>/dev/null; }
log(){ echo "[$(up)] $*" >> "$LOG"; }
flag(){ echo "omt $1=$2" >> "$LOG"; }
sec(){ echo "== $1" >> "$LOG"; shift; "$@" >> "$LOG" 2>&1; }

# tl checkpoint: the host-built 64 KiB template tl-blank.img (zeros, "OMTLOG-END" at 61440) with the
# first 61440 B of the log laid over its start -- dd to stdout opened by "1<>" does not truncate, so no
# conv= is needed -- written to the whole tl partition through mtdblock7 (close flushes; O_TRUNC means
# nothing to a block device) and verified through the char device mtd7.
checkpoint(){
  cp "$P/tl-blank.img" "$W/tl.img" || return 1
  dd if="$LOG" bs=61440 count=1 2>/dev/null 1<>"$W/tl.img" || return 1
  want=$(md5sum < "$W/tl.img" | awk '{print $1}')
  dd if="$W/tl.img" of="$TLB" bs=65536 count=1 2>/dev/null
  sync
  got=$(dd if="$TLC" bs=65536 count=1 2>/dev/null | md5sum | awk '{print $1}')
  rm -f "$W/tl.img"
  [ -n "$want" ] && [ "$want" = "$got" ]; }
cp_note(){ if checkpoint; then log "checkpoint $1 written to tl"; else log "checkpoint $1 NOT verified"; fi; }
# Only the foreground process started below is ours to terminate. Match its original
# /proc start time as well as its PID; never killall a supplicant or touch another service.
APID=""; ASTART=""
auth_alive(){
  case "$APID" in ""|0|1|*[!0-9]*) return 1;; esac
  [ -n "$ASTART" ] && [ "$ASTART" = "$(awk '{print $22}' "/proc/$APID/stat" 2>/dev/null)" ] && \
    kill -0 "$APID" 2>/dev/null
}
auth_connected(){
  awk '/^CTRL-EVENT-CONNECTED$/ {s=1} /^CTRL-EVENT-DISCONNECTED$/ {s=0} END {exit (s ? 0 : 1)}' \
    "$W/auth.raw" 2>/dev/null
}
auth_cleanup(){
  if auth_alive; then kill "$APID" 2>/dev/null; fi
  APID=""; ASTART=""
}
finish(){ log "trial end: $1"; flag result "$1"; cp_note end
  if [ -n "${OM_HOST:-}" ]; then
    wget -q -T 30 -O /dev/null --post-data "$(cat "$LOG")" \
      "http://$OM_HOST:$OM_PORT/upload/trial-log?run=$OM_RUN_ID" >/dev/null 2>&1
    wget -q -T 10 -O /dev/null "http://$OM_HOST:$OM_PORT/status?run=$OM_RUN_ID&phase=trial&event=trial-$1" >/dev/null 2>&1
  fi
  auth_cleanup
  sync
  if [ -n "${OMT_NO_REBOOT:-}" ]; then exit 0; fi
  reboot -f; sleep 30; exit 0; }

log "trial start"
flag nonce "$(cat "$P/nonce" 2>/dev/null)"
flag kernel "$(uname -r)"
case " $(cat "$CMDL") " in *" omtrial=1 "*) flag cmdline_mark 1;; *) flag cmdline_mark 0;; esac
sec version cat /proc/version
sec cmdline cat "$CMDL"
sec mtd cat "$MTDP"

# ---- device nodes and the partition table --------------------------------------------------
CMAJ=$(awk '$2 == "mtd" {print $1}' "$DEVS"); BMAJ=$(awk '$2 == "mtdblock" {print $1}' "$DEVS")
if grep -q '^mtd5: 001d0000 00010000 "tk"$' "$MTDP" && grep -q '^mtd6: 000b0000 00010000 "tp"$' "$MTDP" && \
   grep -q '^mtd7: 00010000 00010000 "tl"$' "$MTDP" && grep -q '^mtd8: 00020000 00010000 "senv"$' "$MTDP" && \
   [ -n "$CMAJ" ] && [ -n "$BMAJ" ]; then flag mtd_table ok; else flag mtd_table bad; finish mtd-table-bad; fi
if [ -n "${OMT_DEV_DIR:-}" ]; then TLB=$OMT_DEV_DIR/b7; TLC=$OMT_DEV_DIR/c7; SEB=$OMT_DEV_DIR/b8; SEC=$OMT_DEV_DIR/c8
else
  TLB=$W/b7; TLC=$W/c7; SEB=$W/b8; SEC=$W/c8
  rm -f "$TLB" "$TLC" "$SEB" "$SEC"
  mknod "$TLB" b "$BMAJ" 7 && mknod "$TLC" c "$CMAJ" 14 && mknod "$SEB" b "$BMAJ" 8 && mknod "$SEC" c "$CMAJ" 16 || \
    finish mknod-failed
fi
cp_note boot

# ---- vendor env: the armed env must already have been replaced by U-Boot's saveenv ----------
senv=$(dd if="$SEC" bs=65536 count=2 2>/dev/null | md5sum | awk '{print $1}')
if [ "$senv" = "$(cat "$P/env-armed.md5")" ]; then
  log "vendor env still armed (U-Boot saveenv did not happen): restoring the stock env"
  dd if="$P/env-base.img" of="$SEB" bs=65536 count=2 2>/dev/null; sync   # the whole 128 KiB partition
  senv=$(dd if="$SEC" bs=65536 count=2 2>/dev/null | md5sum | awk '{print $1}')
  if [ "$senv" = "$(cat "$P/env-base.md5")" ]; then flag env armed-restored; else flag env armed-restore-failed; fi
elif [ "$senv" = "$(cat "$P/env-base.md5")" ]; then flag env base
else flag env saved-by-uboot
fi

# ---- watchdog ownership before the long Wi-Fi/DHCP wait ------------------------------------
# Normal OpenIPC load_hisilicon loads this same module. Its documented default kernel daemon feeds
# the watchdog; the earlier stock-userland trial omitted it and reset before DHCP finished. That
# timing alone is not proof of the reset cause. Do not poke registers, open an unowned watchdog, or
# disable it: use the matching pinned driver with its feeder explicitly enabled, and fail closed.
insmod "$P/wdt.ko" nodeamon=0 default_margin=60 nowayout=0 >> "$LOG" 2>&1
wdt_rc=$?; flag insmod_wdt "$wdt_rc"
[ "$wdt_rc" -eq 0 ] || finish watchdog-init-failed
sec watchdog_modules lsmod
sec watchdog_misc cat /proc/misc
cp_note watchdog

# ---- stock services off, Wi-Fi from the OpenIPC modules --------------------------------------
killall SysUpdate >/dev/null 2>&1
ifconfig eth0 down >/dev/null 2>&1
if [ -z "${OMT_NO_ETC:-}" ]; then
  rm -rf "$W/etc"; cp -a /etc "$W/etc" && mkdir -p "$W/etc/mediatek" && \
    cp "$P/MT7601USTA.dat" "$W/etc/mediatek/MT7601USTA.dat" && mount -o bind "$W/etc" /etc || finish etc-overlay-failed
fi
insmod "$P/cfg80211.ko" >> "$LOG" 2>&1; flag insmod_cfg80211 $?
insmod "$P/mt7601sta.ko" >> "$LOG" 2>&1; flag insmod_mt7601sta $?
sec lsmod lsmod
IF=""; n=0
while [ -z "$IF" ] && [ "$n" -lt 30 ]; do
  for d in "$NETD"/*; do x=${d##*/}; case "$x" in lo|eth0|"*") ;; *) IF=$x;; esac; done
  [ -n "$IF" ] || sleep 1; n=$(expr $n + 1)
done
flag iface "${IF:-none}"
[ -n "$IF" ] || finish no-wifi-interface
ifconfig "$IF" up >> "$LOG" 2>&1
ifup_rc=$?; flag ifup "$ifup_rc"
[ "$ifup_rc" -eq 0 ] || finish wifi-ifup-failed
sec usb cat /sys/kernel/debug/usb/devices
cp_note wifi

# ---- DHCP and the LAN host ---------------------------------------------------------------------
# (sourcing a missing file would end this sh before reboot -f: check first)
if [ -r "$CONF" ]; then . "$CONF" 2>/dev/null; fi

# ---- authenticated Wi-Fi, before DHCP -----------------------------------------------------------
# The standalone static ARMv5 runtime is pinned by the host and verified again here with a stock
# applet. Its trial-only logging patch emits fixed event names; raw output is RAM-only regardless.
# A carrier bit or a configured .dat is not evidence of WPA authentication.
auth_want=$(cat "$P/auth.sha1" 2>/dev/null)
auth_got=$(sha1sum < "$P/wpa_supplicant" 2>/dev/null | awk '{print $1}')
if [ -n "$auth_want" ] && [ "$auth_want" = "$auth_got" ]; then flag auth_binary ok
else flag auth_binary bad; finish auth-runtime-invalid; fi
[ -r "$P/wpa.conf" ] || finish auth-config-missing
AUTH=${OMT_SUPPLICANT:-$P/wpa_supplicant}
: > "$W/auth.raw"
"$AUTH" -i "$IF" -D nl80211,wext -c "$P/wpa.conf" > "$W/auth.raw" 2>&1 &
APID=$!
ASTART=$(awk '{print $22}' "/proc/$APID/stat" 2>/dev/null)
[ -n "$ASTART" ] || { flag auth process-exit; finish wifi-auth-process-exit; }
log "Wi-Fi authentication started"; cp_note auth-start
authed=0; n=0
while [ "$n" -lt 90 ]; do
  if ! auth_alive; then flag auth process-exit; cp_note auth; finish wifi-auth-process-exit; fi
  if auth_connected; then authed=1; break; fi
  sleep 1; n=$(expr $n + 1)
done
if [ "$authed" -eq 1 ]; then
  flag auth connected
else
  auth_reason=wait-expired
  grep -q '^CTRL-EVENT-SCAN-FAILED$' "$W/auth.raw" && auth_reason=scan-failed
  grep -q '^CTRL-EVENT-ASSOC-REJECT$' "$W/auth.raw" && auth_reason=association-rejected
  grep -q '^CTRL-EVENT-SSID-TEMP-DISABLED$' "$W/auth.raw" && auth_reason=temporarily-disabled
  flag auth "$auth_reason"
fi
cp_note auth
[ "$authed" -eq 1 ] || finish wifi-auth-failed
# No SSID/PSK, iwconfig output, or raw driver trace is copied into diagnostics. The fixed-label
# health filter records reset/OOM/watchdog symptoms without the driver's authentication chatter.
net_note(){
  sec wireless cat /proc/net/wireless
  sec carrier cat "$NETD/$IF/carrier"
  sec meminfo cat /proc/meminfo
  echo "== kernel-health" >> "$LOG"
  dmesg | awk '/[Ww]atchdog|Kernel panic|Oops:|BUG:|Call [Tt]race|Out of memory|Killed process/ && !/[Ss][Ss][Ii][Dd]|[Pp][Ss][Kk]|[Pp]ass|[Kk]ey|PMK|PTK|GTK/' | tail -n 20 >> "$LOG" 2>&1
}
ok=0; n=0
while [ "$ok" -eq 0 ] && [ "$n" -lt 8 ]; do
  n=$(expr $n + 1); log "DHCP attempt $n started"; flag dhcp_attempt "$n"
  net_note; cp_note "dhcp-$n"
  if udhcpc -i "$IF" -s "$P/trial_dhcp.sh" -t 5 -T 3 -n -q >> "$LOG" 2>&1; then ok=1
  else log "DHCP attempt $n failed"; sleep 5; fi
done
if [ "$ok" -eq 1 ]; then flag dhcp ok; else flag dhcp fail; fi
sec route cat /proc/net/route
cp_note dhcp
[ "$ok" -eq 1 ] || finish no-dhcp-lease
[ -n "${OM_HOST:-}" ] && [ -n "${OM_PORT:-}" ] && [ -n "${OM_RUN_ID:-}" ] || finish no-migrate-conf
wget -q -T 10 -O /dev/null "http://$OM_HOST:$OM_PORT/status?run=$OM_RUN_ID&phase=trial&event=trial-network-up" >/dev/null 2>&1
rm -f "$W/manifest"
wget -q -T 20 -O "$W/manifest" "http://$OM_HOST:$OM_PORT/manifest?run=$OM_RUN_ID" >/dev/null 2>&1
# (anchored pattern: the stock grep has no -x)
if grep -q "^run=$OM_RUN_ID\$" "$W/manifest" 2>/dev/null; then flag host ok; else flag host fail; fi
[ -r "$W/manifest" ] && grep -q "^run=$OM_RUN_ID\$" "$W/manifest" || finish host-unreachable
# A quick association is insufficient evidence for watchdog ownership. Keep the same kernel alive
# beyond the 60-second watchdog interval before declaring the trial complete. The launcher retains
# its independent 900-second stock-return deadline, and no extra flash region becomes writable.
log "host reached; starting 70-second stability window"; cp_note host-reached
sleep 70 || finish stability-wait-failed
auth_alive || finish wifi-auth-process-exit
if ! auth_connected; then flag auth lost; finish wifi-auth-lost; fi
flag stable_window ok; sec uptime_stable cat /proc/uptime
net_note
finish completed
