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
# Steps: check kernel/partition table, stop SysUpdate, eth0 down, Wi-Fi from
# the OpenIPC cfg80211.ko + mt7601sta.ko and the MT7601USTA.dat built by the
# host, DHCP, report to the LAN host (status + log upload; the stock wget sends --post-data
# form-escaped and serve_migration.py decodes it), reboot -f.
# Prints/uploads no Wi-Fi credentials (the .dat is never logged).
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
finish(){ log "trial end: $1"; flag result "$1"; cp_note end
  if [ -n "${OM_HOST:-}" ]; then
    wget -q -T 30 -O /dev/null --post-data "$(cat "$LOG")" \
      "http://$OM_HOST:$OM_PORT/upload/trial-log?run=$OM_RUN_ID" >/dev/null 2>&1
    wget -q -T 10 -O /dev/null "http://$OM_HOST:$OM_PORT/status?run=$OM_RUN_ID&phase=trial&event=trial-$1" >/dev/null 2>&1
  fi
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
sec usb cat /sys/kernel/debug/usb/devices
cp_note wifi

# ---- DHCP and the LAN host ---------------------------------------------------------------------
# (sourcing a missing file would end this sh before reboot -f: check first)
if [ -r "$CONF" ]; then . "$CONF" 2>/dev/null; fi
ok=0; n=0
while [ "$ok" -eq 0 ] && [ "$n" -lt 8 ]; do
  if udhcpc -i "$IF" -s "$P/trial_dhcp.sh" -t 5 -T 3 -n -q >> "$LOG" 2>&1; then ok=1; else sleep 5; fi
  n=$(expr $n + 1)
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
sec dmesg-tail sh -c 'dmesg | tail -n 60'
finish completed
