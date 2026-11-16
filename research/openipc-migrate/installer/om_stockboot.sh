#!/bin/sh
# om_stockboot.sh -- camera-side actions of the stock-U-Boot OpenIPC migration
# (iBlack, Hi3518EV100, 8 MiB NOR, Wi-Fi only: no UART, no Ethernet).
#
# Runs from RAM under the OpenIPC BusyBox staged by camera/stage2.sh ($BB);
# every external command goes through it. Geometry and the env models live in
# research/openipc-migrate/stock_layout.py (the host builds and validates every
# image this script writes). The vendor U-Boot (0x000000-0x03FFFF) is NEVER
# written; for every write action its SHA-256 must equal the value the host
# pinned from the read-only diagnose run, before and after the writes.
#
# Actions:
#   diagnose     read-only: device info (incl. the watchdog registers), SHA-256 of
#                all 128 erase blocks (3 passes over the MTD char devices), the weak
#                block 0x6c0000 x16, the live vendor env x2, the vendor U-Boot image
#                (for an SPI recovery image). Uploads diag-info, diag-blocks,
#                diag-env, diag-uboot.
#   wdtgate      flash untouched: quiesce all (every process stopped and the SP805
#                watchdog switched off by the hook), hold GATE_HOLD seconds, resume.
#                Proves the camera survives a quiesced final far longer than the
#                watchdog period before final depends on it. Uploads wdtgate-info
#                (uptimes and watchdog register snapshots).
#   collect      read-only: trial-log mailbox (0x70000) x2, live vendor env x2.
#                Uploads collect-tl, collect-env, collect-info.
#   probe-arm    vendor env <- env-probe.img: the next boot restores the stock
#                bootcmd, runs saveenv and boots stock (proves the vendor U-Boot
#                runs our env and saveenv works before anything depends on it).
#   trial-arm    bakfs <- trial.img (OpenIPC kernel + Wi-Fi trial payload), then
#                vendor env <- env-armed.img LAST: the next boot saves the stock
#                bootcmd first and then boots the OpenIPC kernel once.
#   restore-env  vendor env <- env-base.img (the env read by diagnose).
#   final        OpenIPC env 0x60000 <- env-openipc.img; firmware.bin -> 0x70000;
#                overlay 0x720000-0x800000 erased; vendor env <- env-final.img LAST.
#                Blocks 0x60000/0x70000 are unused by the stock layout (abort is
#                still clean there); from 0x80000 on (point of no return) it never
#                aborts: failed blocks are retried, recorded and reported, the
#                final env is always written, and a later run can retry.
# Writes need --allow-write AND OM_WRITE_CONFIRM set to the action's phrase; a
# reboot also needs --allow-reboot AND OM_REBOOT_CONFIRM. Writes are verified by
# majority reads (2 of up to 3) of the MTD *char* devices (block-device reads
# could be served from the page cache). Partitions mtd1-mtd5 are rewritten
# whole (flash_eraseall + char write); mtd0 is only written per 64 KiB block
# through mtdblock0 (each dd closes the device, which flushes the mtdblock
# cache) and only when no other process holds mtdblock0 open.
# --sim-dir DIR redirects all flash access to DIR/mtd0..mtd5 and MTD_PROC
# (offline tests only; never reboots; OM_SIM_FAULT seams exist only there).
# Exit: 0 done; 1 refused or failed without changing what the vendor U-Boot
# boots; 2 FLASH MODIFIED and not fully verified (keep power, retry final, or
# recover over SPI); 3 done and verified but the authorized reboot did not
# happen. $STAGE/flash.state records clean|armed|dirty for stage2.sh.
# Prints no flash contents, env values, backup hashes or secrets.
set -u

PROBE_PHRASE="yes-arm-saveenv-probe"
PROBE_REBOOT="yes-reboot-for-saveenv-probe"
TRIAL_PHRASE="yes-arm-one-shot-openipc-trial"
TRIAL_REBOOT="yes-reboot-into-openipc-trial"
RESTORE_PHRASE="yes-restore-stock-uboot-env"
FINAL_PHRASE="yes-migrate-this-camera-to-openipc"
FINAL_REBOOT="yes-reboot-into-openipc"
STOCK_BOOTCMD="sf probe 0;sf read 0x80007fc0 0x80000 0x180000;bootm 0x80007fc0"
FINAL_BOOTCMD="sf probe 0;sf read 0x82000000 0x70000 0x1d0000;bootm 0x82000000;reset"
TRIAL_MARK="omtrial=1"

BS=65536
STOCK="boot:0x200000 rootfs:0xc0000 appfs:0x280000 mdat1:0x20000 mdat2:0x20000 bakfs:0x280000"
UBOOT_LEN=$((0x40000)); SENV_BLK=4; SENV_LEN=$((0x20000)); OENV_BLK=6; TL_BLK=7
FW_BLK=7; FW_LEN=$((0x6b0000)); FW_KERNEL_LEN=$((0x1d0000)); OENV_LEN=$((0x10000))
TRIAL_LEN=$((0x280000)); TK_LEN=$((0x1d0000)); BAKFS_OFF=$((0x580000)); WEAK_OFF=$((0x6c0000))
GATE_HOLD=300   # wdtgate: seconds held with every process stopped (unit 1's watchdog fired after ~44 s)
APPLETS="awk base64 cat cmp crc32 dd devmem flash_eraseall grep head ls mkdir mknod od readlink reboot rm sha256sum sleep sync tail timeout tr wc wget"

ACTION=${1:-}; [ $# -gt 0 ] && shift
BB=busybox; STAGE=""; HOOK=""; PAY=""; MANIFEST=""; SIM=""; FW_SHA=""; OENV_SHA=""
ALLOW_WRITE=0; ALLOW_REBOOT=0; MAX_REWRITES=2; HOOK_TIMEOUT=300
LOGF=""; TMP=""; STATE=clean; WRITE=0; REBOOT=0; EXPLICIT=0; QUIESCED=0; DEGRADED=""; FAULT=""

emit(){ echo "$1"; if [ -n "$LOGF" ]; then echo "$1" >> "$LOGF" 2>/dev/null; fi; }
log(){ emit "[stockboot:$ACTION] $*"; }
hook(){ [ -n "$HOOK" ] || return 0; "$BB" timeout "$HOOK_TIMEOUT" "$BB" sh "$HOOK" "$@" </dev/null; }
hook_best(){ hook "$@" >/dev/null 2>&1 || true; }
progress(){ hook_best progress "$1"; }
set_state(){ STATE=$1; if [ -n "$STAGE" ]; then echo "$1" > "$STAGE/flash.state"; fi; }
finish(){ rc=$1; EXPLICIT=1
  if [ "$QUIESCED" -eq 1 ] && [ "$STATE" != dirty ] && [ "$STATE" != final ]; then hook_best resume; QUIESCED=0; fi
  if [ -n "$LOGF" ] && [ -f "$LOGF" ]; then hook_best upload "log-$ACTION" "$LOGF"; fi
  if [ -n "$TMP" ]; then "$BB" rm -rf "$TMP"; fi
  exit "$rc"; }
die(){ log "STOP: $*"
  if [ "$STATE" = dirty ]; then log "flash is modified and not verified: keep power on; retry final or recover over SPI"
    progress "stopped-dirty"; finish 2; fi
  progress "stopped-clean"; finish 1; }
on_exit(){ rc=$?; [ "$EXPLICIT" -eq 1 ] && exit "$rc"
  log "unexpected exit (rc=$rc)"
  if [ "$STATE" = dirty ]; then finish 2; fi
  finish 1; }
need(){ [ "$1" -ge 2 ] || { echo "option $2 needs a value"; exit 1; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --busybox) need $# "$1"; BB=$2; shift 2;;
    --stage) need $# "$1"; STAGE=$2; shift 2;;
    --hook) need $# "$1"; HOOK=$2; shift 2;;
    --payload) need $# "$1"; PAY=$2; shift 2;;
    --manifest) need $# "$1"; MANIFEST=$2; shift 2;;
    --firmware-sha256) need $# "$1"; FW_SHA=$2; shift 2;;
    --oenv-sha256) need $# "$1"; OENV_SHA=$2; shift 2;;
    --allow-write) ALLOW_WRITE=1; shift;;
    --allow-reboot) ALLOW_REBOOT=1; shift;;
    --sim-dir) need $# "$1"; SIM=$2; shift 2;;
    *) echo "om_stockboot: unknown argument"; exit 1;;
  esac
done

case "$ACTION" in
  diagnose|collect|wdtgate) KIND=read;;
  probe-arm) KIND=write; PHRASE=$PROBE_PHRASE; RPHRASE=$PROBE_REBOOT;;
  trial-arm) KIND=write; PHRASE=$TRIAL_PHRASE; RPHRASE=$TRIAL_REBOOT;;
  restore-env) KIND=write; PHRASE=$RESTORE_PHRASE; RPHRASE="";;
  final) KIND=write; PHRASE=$FINAL_PHRASE; RPHRASE=$FINAL_REBOOT;;
  *) echo "usage: om_stockboot.sh diagnose|collect|wdtgate|probe-arm|trial-arm|restore-env|final --stage DIR ..."; exit 1;;
esac
[ -n "$STAGE" ] || { echo "om_stockboot: --stage DIR is required"; exit 1; }
"$BB" mkdir -p "$STAGE" || exit 1
LOGF="$STAGE/action.log"; : > "$LOGF"
TMP="$STAGE/.tmp-$ACTION"; "$BB" rm -rf "$TMP"; "$BB" mkdir -p "$TMP" || exit 1
trap on_exit EXIT
trap 'die "interrupted by a signal"' INT TERM
trap '' HUP
[ -f "$STAGE/flash.state" ] && STATE=$("$BB" cat "$STAGE/flash.state")
case "$STATE" in clean|armed|dirty|final) ;; *) STATE=dirty;; esac

if [ "$KIND" = write ] && [ "$ALLOW_WRITE" -eq 1 ] && [ "${OM_WRITE_CONFIRM:-}" = "$PHRASE" ]; then WRITE=1; fi
if [ "$WRITE" -eq 1 ] && [ -n "$RPHRASE" ] && [ "$ALLOW_REBOOT" -eq 1 ] && [ "${OM_REBOOT_CONFIRM:-}" = "$RPHRASE" ]; then
  REBOOT=1; fi
if [ "$KIND" = write ] && [ "$WRITE" -ne 1 ]; then die "write action without --allow-write and its confirm phrase"; fi
if [ "$STATE" = dirty ] && [ "$ACTION" != final ] && [ "$ACTION" != collect ]; then
  die "flash already modified by final; only final (retry) or collect may run"; fi
if [ "$STATE" = final ] && [ "$ACTION" != collect ]; then die "final already verified; power-cycle into OpenIPC"; fi
if [ -n "$SIM" ]; then MTD_PROC=${MTD_PROC:-$SIM/proc_mtd}; FAULT=${OM_SIM_FAULT:-}; log "SIMULATION on $SIM (never reboots)"
else MTD_PROC=/proc/mtd; fi
log "start (write=$WRITE reboot=$REBOOT state=$STATE)"

# ---- tools, geometry and device nodes ----------------------------------------------------
APPLIST=" $("$BB" --list 2>/dev/null | "$BB" tr '\n' ' ') "
for a in $APPLETS; do case "$APPLIST" in *" $a "*) ;; *) die "runtime BusyBox lacks $a";; esac; done
SHA(){ "$BB" sha256sum "$1" | "$BB" awk '{print $1}'; }
SIZE(){ "$BB" wc -c < "$1" | "$BB" tr -d ' '; }
mval(){ [ -n "$MANIFEST" ] || return 0; "$BB" awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$MANIFEST"; }
is_hex64(){ case "$1" in *[!0-9a-f]*|"") return 1;; esac; [ "${#1}" -eq 64 ]; }
NAMES=""; SIZES=""; OFFS=""; off=0
for e in $STOCK; do n=${e%%:*}; s=$((${e#*:})); NAMES="$NAMES $n"; SIZES="$SIZES $s"; OFFS="$OFFS $off"; off=$((off+s)); done
nth(){ n=$1; shift; while [ "$n" -gt 1 ]; do shift; n=$((n-1)); done; echo "$1"; }
psize(){ nth $(($1+1)) $SIZES; }
poff(){ nth $(($1+1)) $OFFS; }
[ -r "$MTD_PROC" ] || die "cannot read $MTD_PROC"
i=0
for n in $NAMES; do
  line=$("$BB" grep "^mtd$i: " "$MTD_PROC")
  set -- $line
  [ $# -eq 4 ] && [ "$4" = "\"$n\"" ] && [ $((0x$2)) -eq "$(psize $i)" ] && [ $((0x$3)) -eq $BS ] || \
    die "/proc/mtd is not the stock 6-partition table (mtd$i)"
  i=$((i+1))
done
[ -z "$("$BB" grep '^mtd6: ' "$MTD_PROC")" ] || die "/proc/mtd has more than the 6 stock partitions"
if [ -n "$SIM" ]; then
  chr(){ echo "$SIM/mtd$1"; }; blk(){ echo "$SIM/mtd$1"; }
  i=0; while [ $i -lt 6 ]; do [ "$(SIZE "$SIM/mtd$i")" -eq "$(psize $i)" ] || die "sim file mtd$i has the wrong size"; i=$((i+1)); done
else
  CMAJ=$("$BB" awk '$2 == "mtd" {print $1; exit}' /proc/devices); BMAJ=$("$BB" awk '$2 == "mtdblock" {print $1; exit}' /proc/devices)
  [ -n "$CMAJ" ] && [ -n "$BMAJ" ] || die "kernel offers no MTD char/block devices"
  DEVD="$STAGE/dev"; "$BB" rm -rf "$DEVD"; "$BB" mkdir -p "$DEVD" || die "cannot create $DEVD"
  i=0
  while [ $i -lt 6 ]; do
    "$BB" mknod "$DEVD/mtd$i" c "$CMAJ" $((i*2)) && "$BB" mknod "$DEVD/mtdblock$i" b "$BMAJ" "$i" || die "mknod failed"
    i=$((i+1))
  done
  chr(){ echo "$DEVD/mtd$1"; }; blk(){ echo "$DEVD/mtdblock$1"; }
fi
FFBLK="$TMP/ff.blk"
"$BB" dd if=/dev/zero bs=$BS count=1 2>/dev/null | "$BB" tr '\000' '\377' > "$FFBLK"
[ "$(SIZE "$FFBLK")" -eq $BS ] || die "cannot build a 0xFF block"
FF64=$(SHA "$FFBLK")

# ---- read / write / verify primitives (64 KiB units) -------------------------------------
rd(){ "$BB" dd if="$(chr "$1")" bs=$BS skip="$2" count="$3" 2>/dev/null; }
rd_hash(){ case "$FAULT" in transient) if [ ! -e "$TMP/fault.t" ]; then : > "$TMP/fault.t"; echo bogus; return 0; fi;; esac
  rd "$@" | "$BB" sha256sum | "$BB" awk '{print $1}'; }
f_hash(){ "$BB" dd if="$1" bs=$BS skip="$2" count="$3" 2>/dev/null | "$BB" sha256sum | "$BB" awk '{print $1}'; }
vblock(){ ok=0; bad=0; r=0
  while [ $r -lt 3 ]; do
    if [ "$(rd_hash "$2" "$3" 1)" = "$1" ]; then ok=$((ok+1)); else bad=$((bad+1)); fi
    r=$((r+1))
    if [ $ok -ge 2 ] || [ $bad -ge 2 ]; then break; fi
  done
  if [ $bad -gt 0 ]; then log "  note: $bad of $r reads mismatched on mtd$2 block $3"; fi
  [ $ok -ge 2 ]; }
# w0 SRC SBLK PBLK: one block into mtd0 through mtdblock0 (never below block 4 = 0x40000).
w0(){ [ "$3" -ge 4 ] && [ "$3" -lt 32 ] || die "internal: refusing an mtd0 write at block $3"
  "$BB" dd if="$1" of="$(blk 0)" bs=$BS skip="$2" seek="$3" count=1 conv=notrunc,fsync 2>/dev/null || return 1
  case "$FAULT" in
    bad-env) if [ "$3" -eq $SENV_BLK ]; then "$BB" dd if=/dev/zero of="$(blk 0)" bs=1 seek=$(($3*BS+64)) count=8 conv=notrunc 2>/dev/null; fi;;
    bad-env-once) if [ "$3" -eq $SENV_BLK ] && [ ! -e "$TMP/fault.e" ]; then : > "$TMP/fault.e"
        "$BB" dd if=/dev/zero of="$(blk 0)" bs=1 seek=$(($3*BS+64)) count=8 conv=notrunc 2>/dev/null; fi;;
    bad-oenv) if [ "$3" -eq $OENV_BLK ]; then "$BB" dd if=/dev/zero of="$(blk 0)" bs=1 seek=$(($3*BS+64)) count=8 conv=notrunc 2>/dev/null; fi;;
    bad-kernel) if [ "$3" -eq 20 ]; then "$BB" dd if=/dev/zero of="$(blk 0)" bs=1 seek=$(($3*BS+64)) count=8 conv=notrunc 2>/dev/null; fi;;
  esac
  return 0; }
# write0 SRC SBLK PBLK N: per-block write + verify + rewrite; failed blocks go to FAILED (never stops early).
write0(){ k=0; FAILED=""
  while [ $k -lt "$4" ]; do
    sb=$(($2+k)); pb=$(($3+k)); exp=$(f_hash "$1" $sb 1); att=0
    while :; do
      w0 "$1" $sb $pb || log "  write command failed on mtd0 block $pb"
      if vblock "$exp" 0 $pb; then break; fi
      att=$((att+1))
      if [ $att -gt $MAX_REWRITES ]; then FAILED="$FAILED $pb"; log "  mtd0 block $pb NOT verified"; break; fi
      log "  mtd0 block $pb failed verification; rewrite $att/$MAX_REWRITES"
    done
    k=$((k+1))
  done
  [ -z "$FAILED" ]; }
erase_part(){ [ "$1" -ge 1 ] && [ "$1" -le 5 ] || die "internal: refusing to erase mtd$1"
  if [ -n "$SIM" ]; then e=0; nb=$(($(psize "$1")/BS))
    while [ $e -lt $nb ]; do "$BB" dd if="$FFBLK" of="$(chr "$1")" bs=$BS seek=$e count=1 conv=notrunc 2>/dev/null || return 1; e=$((e+1)); done
  else "$BB" flash_eraseall -q "$(chr "$1")" >/dev/null 2>&1; fi; }
# rewrite_part IDX SRC SBLK N: erase mtdIDX whole, write N blocks of SRC from block SBLK at offset 0, verify the
# whole partition (the rest must read 0xFF); up to MAX_REWRITES full rewrites.
rewrite_part(){ ri=$1; att=0
  while :; do
    erase_part "$ri" || log "  erase command failed on mtd$ri"
    if [ "$4" -gt 0 ]; then
      "$BB" dd if="$2" of="$(chr "$ri")" bs=$BS skip="$3" count="$4" conv=notrunc 2>/dev/null || log "  write command failed on mtd$ri"
      case "$FAULT" in bad-part-$ri|bad-part-$ri-once)
        if [ "$FAULT" = "bad-part-$ri" ] || [ ! -e "$TMP/fault.p" ]; then : > "$TMP/fault.p"
          "$BB" dd if=/dev/zero of="$(chr "$ri")" bs=1 seek=100 count=8 conv=notrunc 2>/dev/null; fi;;
      esac
    fi
    if verify_part "$@"; then log "  mtd$ri: whole partition verified"; return 0; fi
    att=$((att+1))
    if [ $att -gt $MAX_REWRITES ]; then log "  mtd$ri: block(s)$BADBLKS NOT verified"; return 1; fi
    log "  mtd$ri: block(s)$BADBLKS failed verification; full rewrite $att/$MAX_REWRITES"
  done; }
# verify_part IDX SRC SBLK N: whole-partition check (blocks past N must read 0xFF); bad indices in BADBLKS.
verify_part(){ BADBLKS=""; nb=$(($(psize "$1")/BS)); k=0
  while [ $k -lt $nb ]; do
    if [ $k -lt "$4" ]; then exp=$(f_hash "$2" $(($3+k)) 1); else exp=$FF64; fi
    vblock "$exp" "$1" $k || BADBLKS="$BADBLKS $k"
    k=$((k+1)); done
  [ -z "$BADBLKS" ]; }
# stable_hash IDX SBLK N READS: hash of the region when READS reads agree, else empty.
stable_hash(){ h1=$(rd_hash "$1" "$2" "$3"); r=1
  while [ $r -lt "$4" ]; do [ "$(rd_hash "$1" "$2" "$3")" = "$h1" ] || { echo ""; return 0; }; r=$((r+1)); done
  echo "$h1"; }
# stable_hash3: up to 3 stable_hash attempts, so one glitched read neither aborts nor degrades.
stable_hash3(){ sh3_t=0
  while [ $sh3_t -lt 3 ]; do sh3_v=$(stable_hash "$@"); [ -n "$sh3_v" ] && { echo "$sh3_v"; return 0; }
    sh3_t=$((sh3_t+1)); done
  echo ""; }
read_live_env(){ rd 0 $SENV_BLK 2 > "$TMP/live1.env"; rd 0 $SENV_BLK 2 > "$TMP/live2.env"
  [ "$(SIZE "$TMP/live1.env")" -eq $SENV_LEN ] && "$BB" cmp -s "$TMP/live1.env" "$TMP/live2.env" || die "live vendor env does not read back stably"
  LIVE_SHA=$(SHA "$TMP/live1.env"); }
# U-Boot env image = 4-byte CRC32 (LE) + NUL-separated entries; the body starts at byte 5.
# env_lines IMG: the live definitions only. U-Boot's setenv leaves remnants of old definitions (e.g. the
# armed bootcmd with the trial marker) behind the end marker (double NUL = first empty line); never parse them.
env_lines(){ "$BB" tail -c +5 "$1" | "$BB" tr '\000' '\n' | "$BB" awk '$0 == "" {exit} {print}'; }
env_crc_ok(){ want=$("$BB" od -An -tx4 -N4 "$1" | "$BB" tr -d ' \n')
  got=$("$BB" tail -c +5 "$1" | "$BB" crc32 | "$BB" awk '{print $1}')
  [ "${#want}" -eq 8 ] && [ "$want" = "$got" ]; }
env_has(){ env_lines "$1" | "$BB" grep -qxF "$2"; }
env_mentions(){ env_lines "$1" | "$BB" grep -qF "$2"; }
check_env_img(){ f="$PAY/$1"; [ -f "$f" ] || die "payload $1 missing"
  [ "$(SIZE "$f")" -eq "$2" ] || die "$1 has the wrong size"
  env_crc_ok "$f" || die "$1 CRC mismatch"; }
pinned(){ want=$(mval "f.$1"); is_hex64 "$want" || die "manifest has no SHA-256 for $1"
  [ -f "$PAY/$1" ] && [ "$(SHA "$PAY/$1")" = "$want" ] || die "$1 differs from its manifest SHA-256"; }
check_uboot(){ want=$(mval uboot); is_hex64 "$want" || die "manifest has no U-Boot SHA-256"
  got=$(stable_hash3 0 0 4 2); [ -n "$got" ] && [ "$got" = "$want" ] || die "vendor U-Boot region differs from the diagnose pin"
  UBOOT_SHA=$got; }
check_mtd0_free(){ [ -n "$SIM" ] && return 0
  for f in /proc/[0-9]*/fd/*; do
    l=$("$BB" readlink "$f" 2>/dev/null) || continue
    case "$l" in *mtdblock0|*mtd/block0) die "another process holds mtdblock0 open (${f%/fd/*})";; esac
  done
  "$BB" grep -q 'mtdblock0' /proc/mounts && die "mtdblock0 is mounted"
  return 0; }
reboot_now(){ log "reboot authorized: sync + reboot -f"; progress "rebooting-$ACTION"
  # Watchdog back on as quiesce found it: should the reboot hang, the SoC is still reset.
  if hook wdt-rearm >/dev/null 2>&1; then log "watchdog re-armed for the reboot"; else log "watchdog re-arm failed"; fi
  if [ -n "$SIM" ]; then log "SIMULATION: reboot skipped"; return 0; fi
  hook_best upload "log-$ACTION" "$LOGF"
  "$BB" sync; "$BB" sleep 1; "$BB" reboot -f; "$BB" sleep 5
  if [ -w /proc/sysrq-trigger ]; then echo b > /proc/sysrq-trigger; fi
  "$BB" sleep 5; log "the authorized reboot did not happen; power-cycle the camera"; finish 3; }
quiesce(){ qo=$(hook quiesce "$@" 2>&1); qr=$?
  echo "$qo" | while IFS= read -r l; do if [ -n "$l" ]; then log "$l"; fi; done
  [ "$qr" -eq 0 ] || die "hook quiesce failed; nothing written"
  QUIESCED=1; log "stock processes stopped"; }
fail_env(){ log "STOP: $*"; log "vendor env is neither the old nor the new image: the vendor default env (stock layout) will boot stock"
  progress "stopped-env-unverified"; finish 2; }
env_key(){ env_lines "$1" | "$BB" grep -q "^$2="; }
magic_ok(){ [ "$("$BB" dd if="$1" bs=4 count=1 2>/dev/null | "$BB" od -An -tx1 | "$BB" tr -d ' \n')" = 27051956 ] && \
  [ "$("$BB" dd if="$1" bs=$BS skip=$(($2/BS)) count=1 2>/dev/null | "$BB" head -c 4)" = hsqs ]; }
upload(){ hook upload "$1" "$2" >/dev/null 2>&1 || die "host did not confirm upload $1"; log "uploaded $1"; }
check_live_base(){ read_live_env; want=$(mval envlive)
  is_hex64 "$want" && [ "$LIVE_SHA" = "$want" ] || die "live vendor env differs from the env the host planned from (run collect)"
  env_mentions "$TMP/live1.env" "$TRIAL_MARK" && die "live vendor env carries the trial marker"
  return 0; }
# write_env IMG: vendor env <- IMG (2 blocks); on failure put the env read before the write back.
write_env(){ progress "writing-$ACTION"; log "writing $1 to the vendor env (0x40000, 2 x 64 KiB)"
  if write0 "$PAY/$1" 0 $SENV_BLK 2; then return 0; fi
  log "$1 not verified; restoring the env read before the write"
  if write0 "$TMP/live1.env" 0 $SENV_BLK 2; then die "$1 not verified; previous vendor env restored and verified"; fi
  set_state armed; fail_env "$1 not verified and the previous env could not be restored"; }
after_arm(){ set_state armed; log "vendor env armed for $ACTION"; progress "armed-$ACTION"
  if [ "$REBOOT" -eq 1 ]; then reboot_now; finish 0; fi
  log "reboot not authorized: the next boot (any power cycle) runs the armed env"; finish 0; }
# read2 IDX SBLK N OUT: OUT <- region when 2 of up to 3 reads agree (returns 1 otherwise; OUT keeps read 1).
read2(){ rd "$1" "$2" "$3" > "$4"; rd "$1" "$2" "$3" > "$4.b"
  if "$BB" cmp -s "$4" "$4.b"; then "$BB" rm -f "$4.b"; return 0; fi
  rd "$1" "$2" "$3" > "$4.c"
  if "$BB" cmp -s "$4.b" "$4.c"; then "$BB" mv "$4.b" "$4"; "$BB" rm -f "$4.c"; return 0; fi
  "$BB" cmp -s "$4" "$4.c"; r=$?; "$BB" rm -f "$4.b" "$4.c"; return $r; }

act_diagnose(){
  # Read-only diagnosis must not stop the stock network/watchdog-feeding services.
  # Repeated reads below still fail the host gate if live data changes.
  log "read-only diagnosis: stock services remain running"
  I="$TMP/diag-info"; : > "$I"
  sec(){ t=$1; shift; echo "== $t" >> "$I"; "$@" >> "$I" 2>&1; echo >> "$I"; }
  sec uname "$BB" uname -a
  sec uptime "$BB" cat /proc/uptime
  for f in cmdline version cpuinfo meminfo partitions devices modules mounts misc; do sec "$f" "$BB" cat "/proc/$f"; done
  sec mtd "$BB" cat "$MTD_PROC"
  sec df "$BB" df
  sec ps "$BB" ps
  sec watchdog "$BB" ls -l /dev/watchdog /dev/watchdog0 /sys/class/watchdog
  sec devmem "$BB" ls -l /dev/mem
  sec wdt hook wdt-status
  echo "== mtd-holders" >> "$I"
  for f in /proc/[0-9]*/fd/*; do l=$("$BB" readlink "$f" 2>/dev/null) || continue
    case "$l" in *mtd*) p=${f#/proc/}; p=${p%%/*}; echo "$p $("$BB" cat "/proc/$p/comm" 2>/dev/null) $l" >> "$I";; esac; done
  echo "== usb" >> "$I"
  for d in /sys/bus/usb/devices/*; do [ -f "$d/idVendor" ] || continue
    echo "${d##*/} $("$BB" cat "$d/idVendor"):$("$BB" cat "$d/idProduct") $("$BB" cat "$d/product" 2>/dev/null)" >> "$I"; done
  echo "== net" >> "$I"
  for d in /sys/class/net/*; do echo "${d##*/} $("$BB" cat "$d/operstate" 2>/dev/null) $("$BB" readlink "$d/device/driver" 2>/dev/null)" >> "$I"; done
  sec wireless "$BB" cat /proc/net/wireless
  echo "== dmesg" >> "$I"; "$BB" dmesg 2>&1 | "$BB" tail -n 400 >> "$I"
  # Confirm basic evidence before a slow read or later failure can lose it.
  upload diag-info "$I"; progress "diagnose-info-ready"
  B="$TMP/diag-blocks"; : > "$B"; pass=1
  log "hashing all 128 erase blocks (3 passes)"
  while [ $pass -le 3 ]; do abs=0; i=0
    while [ $i -lt 6 ]; do nb=$(($(psize $i)/BS)); k=0
      progress "diag-pass-$pass-part-$i"
      while [ $k -lt $nb ]; do echo "b $pass $abs $(rd_hash $i $k 1)" >> "$B"; k=$((k+1)); abs=$((abs+1)); done
      i=$((i+1)); done
    pass=$((pass+1)); done
  wb=$(((WEAK_OFF-BAKFS_OFF)/BS)); r=1
  while [ $r -le 16 ]; do echo "w $r $(rd_hash 5 $wb 1)" >> "$B"; r=$((r+1)); done
  r=1; while [ $r -le 3 ]; do echo "u $r $(rd_hash 0 0 4)" >> "$B"; r=$((r+1)); done
  read_live_env; echo "e 1 $LIVE_SHA" >> "$B"; echo "e 2 $(SHA "$TMP/live2.env")" >> "$B"
  upload diag-blocks "$B"; upload diag-env "$TMP/live1.env"
  # The vendor U-Boot itself (2 of up to 3 reads agree): the host's SPI recovery image needs every byte.
  if read2 0 0 4 "$TMP/diag-uboot" && [ "$(SIZE "$TMP/diag-uboot")" -eq $UBOOT_LEN ]; then
    if hook upload diag-uboot "$TMP/diag-uboot" >/dev/null 2>&1; then log "uploaded diag-uboot"
    else log "diag-uboot not confirmed by the host (optional)"; fi
  else log "vendor U-Boot did not read back stably; diag-uboot not uploaded"; fi
  progress "diagnose-done"; log "diagnose finished (read-only)"; finish 0; }

act_collect(){
  C="$TMP/collect-info"; : > "$C"
  echo "state $STATE" >> "$C"; echo "uptime $("$BB" cat /proc/uptime 2>/dev/null)" >> "$C"
  echo "cmdline $("$BB" cat /proc/cmdline 2>/dev/null)" >> "$C"
  k=0; while [ $k -lt 8 ]; do echo "blk $k $(stable_hash3 0 $k 1 2)" >> "$C"; k=$((k+1)); done
  if read2 0 $TL_BLK 1 "$TMP/collect-tl"; then echo "tl stable" >> "$C"; else echo "tl unstable" >> "$C"; fi
  if read2 0 $SENV_BLK 2 "$TMP/collect-env"; then echo "env stable" >> "$C"; else echo "env unstable" >> "$C"; fi
  upload collect-info "$C"; upload collect-tl "$TMP/collect-tl"; upload collect-env "$TMP/collect-env"
  progress "collect-done"; log "collect finished (read-only)"; finish 0; }

act_wdtgate(){
  # Flash untouched. Unit 1 was reset by the SP805 watchdog ~44 s into a quiesced final (its only
  # feeder, cmsMain.out, was stopped). Here the hook stops everything and switches the watchdog off,
  # the camera is held far longer than that, then the watchdog is re-armed and every process continued.
  hold=$GATE_HOLD
  if [ -n "$SIM" ] && [ -n "${OM_SIM_GATE_HOLD:-}" ]; then hold=$OM_SIM_GATE_HOLD; fi
  case "$hold" in ""|*[!0-9]*) die "bad watchdog gate hold";; esac
  step=10; if [ "$hold" -lt 10 ]; then step=1; fi
  G="$TMP/wdtgate-info"; : > "$G"
  gi(){ echo "$1" >> "$G"; }
  up(){ "$BB" awk '{print $1}' /proc/uptime 2>/dev/null; }
  ws(){ hook wdt-status 2>/dev/null | "$BB" tail -n 1; }
  gi "hold $hold"; gi "uptime-start $(up)"; gi "before $(ws)"
  log "watchdog gate: stop every process, watchdog off, hold ${hold}s, resume (flash untouched)"
  quiesce all
  gi "uptime-quiesced $(up)"; gi "quiesced $(ws)"
  progress "wdtgate-quiesced"
  t=0; while [ $t -lt "$hold" ]; do "$BB" sleep $step; t=$((t+step)); progress "wdtgate-held-$t"; done
  gi "uptime-held $(up)"; gi "held $(ws)"
  # final needs the network while quiesced (mdat backups are exported after quiesce all)
  if hook upload wdtgate-info "$G" >/dev/null 2>&1; then gi "quiesced-upload ok"; log "wdtgate-info confirmed while quiesced"
  else gi "quiesced-upload failed"; log "wdtgate-info not confirmed while quiesced"; fi
  if hook resume >/dev/null 2>&1; then gi "resume ok"; log "resume ok"; else gi "resume failed"; log "resume failed"; fi
  QUIESCED=0
  gi "uptime-resumed $(up)"; gi "after $(ws)"
  upload wdtgate-info "$G"
  progress "wdtgate-done"; log "watchdog gate held ${hold}s and resumed (flash untouched)"; finish 0; }

act_probe(){
  pinned env-probe.img; check_env_img env-probe.img $SENV_LEN
  env_has "$PAY/env-probe.img" "bootcmd=setenv bootcmd '$STOCK_BOOTCMD';saveenv;$STOCK_BOOTCMD;reset" || \
    die "env-probe.img does not carry the saveenv probe bootcmd"
  env_mentions "$PAY/env-probe.img" "$TRIAL_MARK" && die "env-probe.img carries the trial marker"
  check_live_base; check_uboot; check_mtd0_free; quiesce
  write_env env-probe.img
  after_arm; }

act_trial(){
  pinned trial.img; pinned env-armed.img
  [ "$(SIZE "$PAY/trial.img")" -eq $TRIAL_LEN ] || die "trial.img must be exactly the bakfs size"
  magic_ok "$PAY/trial.img" $TK_LEN || die "trial.img lacks the uImage at 0 or the SquashFS at 0x1d0000"
  check_env_img env-armed.img $SENV_LEN
  env_mentions "$PAY/env-armed.img" "bootcmd=setenv bootcmd '$STOCK_BOOTCMD';saveenv;" || \
    die "env-armed.img does not save the stock bootcmd before the trial boot"
  env_mentions "$PAY/env-armed.img" "$TRIAL_MARK" || die "env-armed.img lacks the trial marker"
  check_live_base; check_uboot; check_mtd0_free; quiesce
  progress "writing-trial-image"; log "writing trial.img to bakfs (mtd5, 40 blocks)"
  rewrite_part 5 "$PAY/trial.img" 0 40 || die "trial image not verified in bakfs; vendor env untouched (stock boots)"
  write_env env-armed.img
  after_arm; }

act_restore(){
  pinned env-base.img; check_env_img env-base.img $SENV_LEN
  env_has "$PAY/env-base.img" "bootcmd=$STOCK_BOOTCMD" || die "env-base.img does not carry the stock bootcmd"
  env_mentions "$PAY/env-base.img" "$TRIAL_MARK" && die "env-base.img carries the trial marker"
  if read2 0 $SENV_BLK 2 "$TMP/live1.env" && [ "$(SHA "$TMP/live1.env")" = "$(SHA "$PAY/env-base.img")" ]; then
    set_state clean; log "vendor env already equals env-base.img; nothing written"; progress "restore-not-needed"; finish 0; fi
  check_uboot; check_mtd0_free; quiesce
  set_state armed; progress "writing-$ACTION"; log "writing env-base.img to the vendor env"
  write0 "$PAY/env-base.img" 0 $SENV_BLK 2 || fail_env "env-base.img not verified"
  set_state clean; progress "env-restored"; log "vendor env restored and verified"; finish 0; }

# ---- final ---------------------------------------------------------------------------------
# deg ABSBLK...: record the region class of every unverified absolute erase block.
deg(){ for b in "$@"; do a=$((b*BS))
    if [ $a -lt $UBOOT_LEN ]; then c=uboot; elif [ $a -lt $((0x60000)) ]; then c=venv; elif [ $a -lt $((0x70000)) ]; then c=oenv
    elif [ $a -lt $((0x240000)) ]; then c=kernel; elif [ $a -lt $((0x720000)) ]; then c=rootfs; else c=overlay; fi
    case " $DEGRADED " in *" $c "*) ;; *) DEGRADED="$DEGRADED $c";; esac
  done; }
# mtd0 blocks of the plan: SRC SBLK PBLK N
fin0(){ if [ "$CONT" -eq 1 ]; then k=0; todo=0
    while [ $k -lt "$4" ]; do vblock "$(f_hash "$1" $(($2+k)) 1)" 0 $(($3+k)) || todo=1; k=$((k+1)); done
    if [ $todo -eq 0 ]; then log "  mtd0 blocks $3..$(($3+$4-1)) already verified"; return 0; fi; fi
  write0 "$@" && return 0
  deg $FAILED; return 1; }
finp(){ off=$(poff "$1")
  if [ "$CONT" -eq 1 ] && verify_part "$@"; then log "  mtd$1 already verified"; return 0; fi
  rewrite_part "$@" && return 0
  for b in $BADBLKS; do deg $((off/BS+b)); done; return 1; }
fin_plan(){ progress final-w-m0; fin0 "$F" 1 8 24; progress final-w-m5; finp 5 "$F" 81 26
  progress final-w-m4; finp 4 "$F" 79 2; progress final-w-m3; finp 3 "$F" 77 2
  progress final-w-m2; finp 2 "$F" 37 40; progress final-w-m1; finp 1 "$F" 25 12; }
fin_sweep(){ SWEEP=""
  vblock "$(f_hash "$O" 0 1)" 0 $OENV_BLK || SWEEP="$SWEEP oenv"
  k=0; while [ $k -lt 25 ]; do vblock "$(f_hash "$F" $k 1)" 0 $((FW_BLK+k)) || SWEEP="$SWEEP m0"; k=$((k+1)); done
  for spec in "1 25 12" "2 37 40" "3 77 2" "4 79 2" "5 81 26"; do set -- $spec
    verify_part "$1" "$F" "$2" "$3" || SWEEP="$SWEEP m$1"; done
  [ -z "$SWEEP" ]; }

act_final(){
  F="$PAY/firmware.bin"; O="$PAY/env-openipc.img"; E="$PAY/env-final.img"
  is_hex64 "$FW_SHA" && is_hex64 "$OENV_SHA" || die "--firmware-sha256 and --oenv-sha256 are required"
  [ -f "$F" ] && [ "$(SHA "$F")" = "$FW_SHA" ] || die "firmware.bin differs from its pin"
  [ "$(SIZE "$F")" -eq $FW_LEN ] && magic_ok "$F" $FW_KERNEL_LEN || die "firmware.bin is not the 0x6b0000-byte nor-lite-split image"
  [ -f "$O" ] && [ "$(SHA "$O")" = "$OENV_SHA" ] || die "env-openipc.img differs from its pin"
  [ "$(SIZE "$O")" -eq $OENV_LEN ] && env_crc_ok "$O" && env_key "$O" bootcmd || die "env-openipc.img is not a valid 64 KiB env"
  pinned env-final.img; check_env_img env-final.img $SENV_LEN
  env_has "$E" "bootcmd=$FINAL_BOOTCMD" && env_has "$E" "verify=y" || die "env-final.img lacks the final bootcmd or verify=y"
  env_mentions "$E" "$TRIAL_MARK" && die "env-final.img carries the trial marker"
  CONT=0
  if [ "$STATE" = dirty ]; then CONT=1; log "continuing an interrupted final (state dirty)"
  else check_live_base; fi
  check_uboot; check_mtd0_free
  quiesce all
  if [ $CONT -eq 0 ]; then
    log "stability check (U-Boot, vendor env, mdat1/mdat2 x3)"
    for spec in "0 0 4" "0 4 2" "3 0 2" "4 0 2"; do set -- $spec
      [ -n "$(stable_hash3 "$1" "$2" "$3" 3)" ] || die "flash reads are not stable (mtd$1); nothing written"; done
    P="$STAGE/private"; "$BB" mkdir -p "$P" || die "cannot create $P"
    for m in 3 4; do n=mdat$((m-2)); read2 $m 0 2 "$P/$n.bin" || die "$n does not read back stably"; done
    hook export "$P" >/dev/null 2>&1 || die "host did not confirm the mdat1/mdat2 backups; nothing written"
    log "mdat backups exported and confirmed"
    progress "final-pre-ponr"
    write0 "$O" 0 $OENV_BLK 1 || die "OpenIPC env block (0x60000) not verified; stock still intact"
    write0 "$F" 0 $FW_BLK 1 || die "first firmware block (0x70000) not verified; stock still intact"
  else
    fin0 "$O" 0 $OENV_BLK 1; fin0 "$F" 0 $FW_BLK 1
  fi
  set_state dirty; progress "ponr"; log "POINT OF NO RETURN: overwriting the stock firmware (never aborts from here)"
  fin_plan
  if ! fin_sweep; then log "sweep found unverified regions:$SWEEP; one repair pass"
    CONT=1; fin0 "$O" 0 $OENV_BLK 1; fin0 "$F" 0 $FW_BLK 1; fin_plan
    fin_sweep || log "still unverified after repair:$SWEEP"; fi
  DEGRADED=""; if [ -n "$SWEEP" ]; then fin_sweep_deg; fi
  log "writing env-final.img to the vendor env (last)"; progress final-w-env
  MAX_REWRITES=4; write0 "$E" 0 $SENV_BLK 2 || deg $FAILED; MAX_REWRITES=2
  got=$(stable_hash3 0 0 4 2); [ -n "$got" ] && [ "$got" = "$UBOOT_SHA" ] || { log "vendor U-Boot region changed or unstable!"; deg 0; }
  if [ -n "$DEGRADED" ]; then
    log "FINAL INCOMPLETE: unverified regions:$DEGRADED (keep power on; retry final, else recover over SPI)"
    progress "final-degraded$(echo "$DEGRADED" | "$BB" tr ' ' '-')"; finish 2; fi
  set_state final; progress "final-verified"; log "all regions written and verified; vendor env now boots OpenIPC"
  if [ "$REBOOT" -eq 1 ]; then reboot_now; fi
  log "reboot not authorized: power-cycle the camera to boot OpenIPC"; finish 0; }
fin_sweep_deg(){ for s in $SWEEP; do case "$s" in oenv) deg 6;;
    m0) k=0; while [ $k -lt 25 ]; do vblock "$(f_hash "$F" $k 1)" 0 $((FW_BLK+k)) || deg $((FW_BLK+k)); k=$((k+1)); done;;
    m*) i=${s#m}; for spec in "1 25 12" "2 37 40" "3 77 2" "4 79 2" "5 81 26"; do set -- $spec
          [ "$1" = "$i" ] || continue; verify_part "$1" "$F" "$2" "$3" || for b in $BADBLKS; do deg $(($(poff "$1")/BS+b)); done
        done;;
  esac; done; }

case "$ACTION" in
  diagnose) act_diagnose;;
  collect) act_collect;;
  wdtgate) act_wdtgate;;
  probe-arm) act_probe;;
  trial-arm) act_trial;;
  restore-env) act_restore;;
  final) act_final;;
esac
finish 1
