#!/bin/sh
# OpenIPC migration installer for the stock iBlack (Hi3518EV100, 8 MiB NOR).
#
# SAFETY MODEL -- read before running on hardware:
#   * Default is a DRY RUN: reads only, no flash writes, no reboot.
#   * A real flash write needs BOTH --allow-flash-write AND the environment
#     variable OPENIPC_MIGRATE_CONFIRM set to the exact CONFIRM_PHRASE below.
#     In real-write mode /proc/mtd and /dev/mtdblockN are always used (the
#     MTD_PROC / MTD_DEV_DIR test overrides are ignored).
#   * A reboot additionally needs --allow-reboot AND OPENIPC_MIGRATE_REBOOT_CONFIRM
#     set to REBOOT_PHRASE, and only happens after every region AND the final
#     sweep verified. It uses `reboot -f` (the stock inittab shutdown actions
#     would run binaries from the overwritten stock rootfs).
#   * --dry-run-dir DIR redirects every device access to file-backed fake
#     mtdblock files; it forces real-write mode OFF and can never reboot. The
#     OPENIPC_MIGRATE_SELFTEST_FAULT seams exist only in that mode.
#   * Must run from RAM under the OpenIPC BusyBox staged by stage2.sh: the stock
#     BusyBox 1.18.4 has no sha256sum/tr/wc/head and no shell arithmetic.
#     Every external command goes through that BusyBox ($BB).
#   * Order: verify payloads -> verify /proc/mtd -> hook quiesce -> read-
#     stability check (N full reads of every stock partition) -> private
#     mdat1/mdat2 backup -> hook export -> bootloader (256 KiB, 0xFF padded)
#     -> env -> firmware -> overlay -> final sweep. The four regions tile the
#     whole 8 MiB, so the result does not depend on the old flash contents.
#     Every 64 KiB-aligned segment is verified by majority readback (2 of up
#     to 3 reads) and rewritten at most --max-rewrites times.
#   * Exit codes: 0 done; 1 clean abort (no flash byte written, hook `resume`
#     called); 2 PARTIAL STATE (flash modified, never reboots); 3 migration
#     verified but the authorized reboot did not happen.
#   * Never prints partition contents, backup hashes, or secrets.
# This file hard-codes no camera address, credential, SSID, or key.
set -eu

CONFIRM_PHRASE="yes-migrate-this-camera-to-openipc"
REBOOT_PHRASE="yes-reboot-into-openipc"

# ---- Fixed geometry (mirrors research/openipc-migrate/migrate_plan.py) ------
FLASH_BYTES=$((0x800000)); BS=$((0x10000))
BOOT_LEN=$((0x40000)); ENV_BASE=$((0x40000)); ENV_LEN=$((0x10000))
FW_BASE=$((0x50000)); OVERLAY_BASE=$((0x700000))
# Stock partitions in flash order -- name:size(hex); offsets are derived.
STOCK="boot:0x200000 rootfs:0xc0000 appfs:0x280000 mdat1:0x20000 mdat2:0x20000 bakfs:0x280000"
ORDER="bootloader env firmware overlay"
REQUIRED_APPLETS="awk cat chmod cmp dd grep head mkdir rm rmdir sh sha256sum sleep sync tail timeout tr wc"

FW=""; FW_SHA=""; UBOOT=""; UBOOT_SHA=""; ENV_IMG=""; ENV_SHA=""; ENV_ERASE=0; ENV_MODE=""
STAGE=""; BB="busybox"; HOOK=""; HOOK_TIMEOUT=120
ALLOW_WRITE=0; ALLOW_REBOOT=0; DRYRUN_DIR=""; STAB_READS=3; MAX_REWRITES=2
MTD_PROC="${MTD_PROC:-/proc/mtd}"
LOGF=""; LOCKD=""; TMP=""; WRITES_STARTED=0; CUR_REGION="none"; EFFECT=0; WRITE_MODE=0
SELFTEST_FAULT=""; EXPLICIT_EXIT=0; BOOT_IMG=""; ENV_SRC="FF"

emit(){ printf '%s\n' "$1"; if [ -n "$LOGF" ]; then printf '%s\n' "$1" >> "$LOGF" 2>/dev/null || true; fi; }
log(){ emit "[migrate] $*"; }
hook_call(){ "$BB" timeout "$HOOK_TIMEOUT" "$BB" sh "$HOOK" "$@" </dev/null; }
hook_best(){ if [ -n "$HOOK" ]; then hook_call "$@" >/dev/null 2>&1 || true; fi; }
die(){ emit "[migrate] ABORT: $*" >&2
  EXPLICIT_EXIT=1
  if [ "$WRITES_STARTED" -eq 1 ]; then
    emit "[migrate] PARTIAL STATE: flash already modified (region: $CUR_REGION). Do not power-cycle blindly; recover via U-Boot fwupd (TFTP) or UART/SPI." >&2
    hook_best progress abort-partial
    exit 2
  fi
  hook_best progress abort-clean
  hook_best resume
  exit 1; }
on_exit(){ rc=$?
  if [ "$EXPLICIT_EXIT" -ne 1 ]; then
    if [ "$WRITES_STARTED" -eq 1 ]; then
      emit "[migrate] PARTIAL STATE: unexpected exit (rc=$rc) after flash was modified (region: $CUR_REGION)." >&2
      hook_best progress abort-partial; rc=2
    else
      emit "[migrate] ABORT: unexpected exit (rc=$rc) before any flash write." >&2
      hook_best progress abort-clean; hook_best resume; rc=1
    fi
  fi
  if [ -n "$TMP" ]; then "$BB" rm -rf "$TMP" 2>/dev/null || true; fi
  if [ -n "$LOCKD" ]; then "$BB" rmdir "$LOCKD" 2>/dev/null || true; fi
  exit "$rc"; }
usage(){ printf '%s\n' \
  'Usage: openipc_migrate.sh --firmware F --firmware-sha256 S --uboot U --uboot-sha256 S' \
  '         (--env-image F --env-sha256 S | --env-erase) --stage DIR' \
  '         [--busybox PATH] [--hook SCRIPT] [--stability-reads 2-9] [--max-rewrites 0-5]' \
  '         [--allow-flash-write] [--allow-reboot] [--dry-run-dir DIR]' \
  'Default is a dry run (reads only). See the safety header in this file.'; }
need(){ if [ "$1" -lt 2 ]; then die "option $2 needs a value"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --firmware) need $# "$1"; FW=$2; shift 2;;
    --firmware-sha256) need $# "$1"; FW_SHA=$2; shift 2;;
    --uboot) need $# "$1"; UBOOT=$2; shift 2;;
    --uboot-sha256) need $# "$1"; UBOOT_SHA=$2; shift 2;;
    --env-image) need $# "$1"; ENV_IMG=$2; shift 2;;
    --env-sha256) need $# "$1"; ENV_SHA=$2; shift 2;;
    --env-erase) ENV_ERASE=1; shift;;
    --stage) need $# "$1"; STAGE=$2; shift 2;;
    --busybox) need $# "$1"; BB=$2; shift 2;;
    --hook) need $# "$1"; HOOK=$2; shift 2;;
    --stability-reads) need $# "$1"; STAB_READS=$2; shift 2;;
    --max-rewrites) need $# "$1"; MAX_REWRITES=$2; shift 2;;
    --allow-flash-write) ALLOW_WRITE=1; shift;;
    --allow-reboot) ALLOW_REBOOT=1; shift;;
    --dry-run-dir) need $# "$1"; DRYRUN_DIR=$2; shift 2;;
    -h|--help) usage; EXPLICIT_EXIT=1; exit 0;;
    *) die "unknown argument: $1";;
  esac
done

# ---- argument validation (no device access yet) ----------------------------
[ -n "$FW" ] && [ -n "$UBOOT" ] || { usage >&2; die "missing --firmware/--uboot"; }
[ -n "$FW_SHA" ] && [ -n "$UBOOT_SHA" ] || die "missing --firmware-sha256/--uboot-sha256"
[ -n "$STAGE" ] || die "--stage DIR is required (temp files, log and private backup live there)"
if [ -n "$ENV_IMG" ] && [ "$ENV_ERASE" -eq 1 ]; then die "use either --env-image or --env-erase, not both"; fi
if [ -n "$ENV_IMG" ]; then
  ENV_MODE=image
  [ -n "$ENV_SHA" ] || die "--env-image needs --env-sha256"
elif [ "$ENV_ERASE" -eq 1 ]; then
  ENV_MODE=erase
else
  die "choose an env strategy: --env-image F --env-sha256 S (keeps Wi-Fi settings) or --env-erase (U-Boot defaults; a Wi-Fi-only unit becomes unreachable)"
fi
case "$STAB_READS" in [2-9]) ;; *) die "--stability-reads must be 2..9";; esac
case "$MAX_REWRITES" in [0-5]) ;; *) die "--max-rewrites must be 0..5";; esac
for p in "$FW" "$UBOOT" "$ENV_IMG" "$STAGE" "$DRYRUN_DIR" "$HOOK"; do
  case "$p" in *" "*|*"	"*|*:*) die "paths must not contain whitespace or ':' ($p)";; esac
done
[ -f "$FW" ] || die "firmware not found: $FW"
[ -f "$UBOOT" ] || die "uboot not found: $UBOOT"
if [ "$ENV_MODE" = image ]; then [ -f "$ENV_IMG" ] || die "env image not found: $ENV_IMG"; fi
if [ -n "$HOOK" ]; then [ -f "$HOOK" ] || die "hook script not found: $HOOK"; fi
command -v "$BB" >/dev/null 2>&1 || die "busybox not found: $BB"
APPLETS=" $("$BB" --list 2>/dev/null | "$BB" tr '\n' ' ') " || die "cannot list busybox applets"
for a in $REQUIRED_APPLETS; do
  case "$APPLETS" in *" $a "*) ;; *) die "busybox lacks required applet: $a";; esac
done

# ---- mode -------------------------------------------------------------------
if [ -n "$DRYRUN_DIR" ]; then
  WRITE_MODE=0; EFFECT=1
  SELFTEST_FAULT="${OPENIPC_MIGRATE_SELFTEST_FAULT:-}"
elif [ "$ALLOW_WRITE" -eq 1 ]; then
  [ "${OPENIPC_MIGRATE_CONFIRM:-}" = "$CONFIRM_PHRASE" ] || die "--allow-flash-write given but OPENIPC_MIGRATE_CONFIRM phrase missing"
  WRITE_MODE=1; EFFECT=1; MTD_PROC=/proc/mtd
fi

"$BB" mkdir -p "$STAGE" || die "cannot create stage dir"
LOCKD="$STAGE/.lock"
if ! "$BB" mkdir "$LOCKD" 2>/dev/null; then LOCKD=""; die "another installer run holds $STAGE/.lock"; fi
trap on_exit EXIT
trap 'die "interrupted by signal"' INT TERM
trap '' HUP
TMP="$STAGE/.tmp"; "$BB" rm -rf "$TMP"; "$BB" mkdir -p "$TMP" || die "cannot create tmp dir"
LOGF="$STAGE/migrate.log"; : > "$LOGF"
FFBLK="$TMP/ff.blk"

if [ "$WRITE_MODE" -eq 1 ]; then log "MODE: REAL FLASH WRITE (authorized by flag + confirm phrase)"
elif [ -n "$DRYRUN_DIR" ]; then log "MODE: file-backed simulation ($DRYRUN_DIR); can never reboot"
else log "MODE: dry run (reads only; no writes, no reboot)"; fi

# ---- helpers ---------------------------------------------------------------
SHA(){ "$BB" sha256sum "$1" | "$BB" awk '{print $1}'; }
FILESIZE(){ "$BB" wc -c < "$1" | "$BB" tr -d ' '; }
hex(){ printf '0x%x' "$1"; }
nth(){ n=$1; shift; while [ "$n" -gt 1 ]; do shift; n=$((n-1)); done; printf '%s' "$1"; }

EXP_NAMES=""; EXP_SIZES=""; EXP_OFFS=""; off=0
for e in $STOCK; do
  n=${e%%:*}; s=${e#*:}; s=$((s))
  EXP_NAMES="$EXP_NAMES $n"; EXP_SIZES="$EXP_SIZES $s"; EXP_OFFS="$EXP_OFFS $off"
  off=$((off+s))
done
[ "$off" -eq "$FLASH_BYTES" ] || die "internal: stock table != 8 MiB"
# The four target regions must tile the whole chip with no gap or overlap.
[ "$BOOT_LEN" -eq "$ENV_BASE" ] && [ $((ENV_BASE+ENV_LEN)) -eq "$FW_BASE" ] || die "internal: region tiling broken"

name2idx(){ i2=0; for n2 in $EXP_NAMES; do if [ "$n2" = "$1" ]; then printf '%s' "$i2"; return 0; fi; i2=$((i2+1)); done; return 0; }
sizeof(){ nth $(( $(name2idx "$1") + 1 )) $EXP_SIZES; }
offbyidx(){ nth $(($1+1)) $EXP_OFFS; }
# Device path policy: simulation -> DRYRUN_DIR files; REAL write -> /dev always;
# dry run -> MTD_DEV_DIR (read-only test override), default /dev.
devpath(){ if [ -n "$DRYRUN_DIR" ]; then printf '%s/mtdblock%s' "$DRYRUN_DIR" "$1"
  elif [ "$WRITE_MODE" -eq 1 ]; then printf '/dev/mtdblock%s' "$1"
  else printf '%s/mtdblock%s' "${MTD_DEV_DIR:-/dev}" "$1"; fi; }
# map_abs START LEN OUTFILE -> "idx offset_in_part length" per touched stock part
map_abs(){ ms=$1; me=$(($1+$2)); : > "$3"; mi=0
  for mn in $EXP_NAMES; do
    mpo=$(nth $((mi+1)) $EXP_OFFS); mps=$(nth $((mi+1)) $EXP_SIZES); mpe=$((mpo+mps))
    mlo=$ms; if [ "$mpo" -gt "$mlo" ]; then mlo=$mpo; fi
    mhi=$me; if [ "$mpe" -lt "$mhi" ]; then mhi=$mpe; fi
    if [ "$mlo" -lt "$mhi" ]; then printf '%s %s %s\n' "$mi" $((mlo-mpo)) $((mhi-mlo)) >> "$3"; fi
    mi=$((mi+1))
  done; }

make_ff(){ if [ ! -f "$FFBLK" ]; then "$BB" dd if=/dev/zero bs=$BS count=1 2>/dev/null | "$BB" tr '\000' '\377' > "$FFBLK"; fi
  [ "$(FILESIZE "$FFBLK")" -eq "$BS" ] || die "failed to build 0xFF block"; }
hash_dev_region(){ "$BB" dd if="$(devpath "$1")" bs=$BS skip=$(($2/BS)) count=$(($3/BS)) 2>/dev/null | "$BB" sha256sum | "$BB" awk '{print $1}'; }
hash_file_region(){ "$BB" dd if="$1" bs=$BS skip=$(($2/BS)) count=$(($3/BS)) 2>/dev/null | "$BB" sha256sum | "$BB" awk '{print $1}'; }
ff_hash(){ "$BB" dd if=/dev/zero bs=$BS count=$(($1/BS)) 2>/dev/null | "$BB" tr '\000' '\377' | "$BB" sha256sum | "$BB" awk '{print $1}'; }
ff_bytes_hash(){ "$BB" dd if=/dev/zero bs=$BS count=$((($1+BS-1)/BS)) 2>/dev/null | "$BB" tr '\000' '\377' | "$BB" head -c "$1" | "$BB" sha256sum | "$BB" awk '{print $1}'; }
# read_hash: verification read (simulation-only transient fault seam).
read_hash(){ if [ "$SELFTEST_FAULT" = transient_read ] && [ ! -e "$TMP/fault.transient" ]; then
    : > "$TMP/fault.transient"; printf 'bogus'; return 0; fi
  hash_dev_region "$1" "$2" "$3"; }

# ---- verify payload fingerprints BEFORE touching any device ----------------
[ "$(SHA "$FW")" = "$FW_SHA" ] || die "firmware sha256 mismatch"
[ "$(SHA "$UBOOT")" = "$UBOOT_SHA" ] || die "uboot sha256 mismatch"
FW_SZ=$(FILESIZE "$FW"); UB_SZ=$(FILESIZE "$UBOOT")
[ "$UB_SZ" -gt 0 ] && [ "$UB_SZ" -le "$BOOT_LEN" ] || die "uboot size out of range (must fit 256 KiB)"
[ $((FW_BASE+FW_SZ)) -eq "$OVERLAY_BASE" ] || die "firmware must end exactly at overlay base 0x700000"
[ $((FW_SZ % BS)) -eq 0 ] || die "firmware length not a 64 KiB multiple"
printf '\047\005\031\126' > "$TMP/uimage.magic"
"$BB" head -c 4 "$FW" | "$BB" cmp -s - "$TMP/uimage.magic" || die "firmware does not start with a uImage header"
if [ "$ENV_MODE" = image ]; then
  [ "$(SHA "$ENV_IMG")" = "$ENV_SHA" ] || die "env image sha256 mismatch"
  [ "$(FILESIZE "$ENV_IMG")" -eq "$ENV_LEN" ] || die "env image must be exactly 64 KiB"
  "$BB" grep -q 'bootcmd=' "$ENV_IMG" || die "env image lacks bootcmd= (not a full U-Boot environment)"
  ENV_SRC=$ENV_IMG
fi
log "payloads verified: fw=${FW_SZ}B uboot=${UB_SZ}B env=$ENV_MODE"

# ---- parse + validate stock MTD table and device nodes ----------------------
[ -r "$MTD_PROC" ] || die "cannot read MTD table: $MTD_PROC"
i=0
for n in $EXP_NAMES; do
  esz=$(nth $((i+1)) $EXP_SIZES)
  line=$("$BB" grep "\"$n\"" "$MTD_PROC" || true)
  [ -n "$line" ] || die "stock partition '$n' not present in $MTD_PROC"
  dev=${line%%:*}; idx=${dev#mtd}
  [ "$idx" = "$i" ] || die "partition '$n' is $dev, expected mtd$i (table order changed)"
  set -- $line
  [ $# -ge 4 ] || die "malformed MTD line for $n"
  psz=$((0x$2)); per=$((0x$3))
  [ "$psz" -eq "$esz" ] || die "partition '$n' size $psz != expected $esz"
  [ "$per" -eq "$BS" ] || die "partition '$n' erasesize $per != 64 KiB (geometry assumption failed)"
  dp=$(devpath "$i")
  if [ -n "$DRYRUN_DIR" ]; then
    [ -f "$dp" ] || die "simulation device missing: $dp"
  elif [ "$WRITE_MODE" -eq 0 ] && [ -n "${MTD_DEV_DIR:-}" ]; then
    [ -e "$dp" ] || die "dry-run device missing: $dp"
  else
    [ -b "$dp" ] || die "not a block device: $dp"
  fi
  [ -r "$dp" ] || die "cannot read $dp"
  if [ "$EFFECT" -eq 1 ]; then [ -w "$dp" ] || die "cannot write $dp"; fi
  i=$((i+1))
done
log "stock MTD table matches the expected 6-partition layout; erase=64KiB"

# ---- write/verify primitives -------------------------------------------------
seg_write(){ local src=$1 so=$2 di=$3 po=$4 ln=$5 att=$6 cnt k dev
  cnt=$((ln/BS)); dev=$(devpath "$di")
  if [ "$src" = FF ]; then
    make_ff; k=0
    while [ "$k" -lt "$cnt" ]; do
      "$BB" dd if="$FFBLK" of="$dev" bs=$BS seek=$((po/BS+k)) count=1 conv=notrunc,fsync 2>/dev/null || return 1
      k=$((k+1))
    done
  else
    if [ "$CUR_REGION" = firmware ] && [ "$so" -eq 0 ] && [ "$cnt" -gt 1 ]; then
      if [ "$SELFTEST_FAULT" = short_write_firmware ] && [ "$att" -eq 0 ]; then cnt=$((cnt-1)); fi
      if [ "$SELFTEST_FAULT" = short_write_firmware_always ]; then cnt=$((cnt-1)); fi
    fi
    "$BB" dd if="$src" of="$dev" bs=$BS skip=$((so/BS)) seek=$((po/BS)) count=$cnt conv=notrunc,fsync 2>/dev/null || return 1
  fi
  if [ "$CUR_REGION" = firmware ] && [ "$so" -eq 0 ] && [ "$SELFTEST_FAULT" = corrupt_after_write_firmware ]; then
    "$BB" dd if=/dev/zero of="$dev" bs=1 seek="$po" count=64 conv=notrunc 2>/dev/null || true
  fi
  return 0; }
# seg_verify EXPECTED IDX OFF LEN: pass when 2 of up to 3 reads match.
seg_verify(){ local exp=$1 di=$2 po=$3 ln=$4 ok=0 bad=0 r=0 got
  while [ "$r" -lt 3 ]; do
    got=$(read_hash "$di" "$po" "$ln"); r=$((r+1))
    if [ "$got" = "$exp" ]; then ok=$((ok+1)); else bad=$((bad+1)); fi
    if [ "$ok" -ge 2 ] || [ "$bad" -ge 2 ]; then break; fi
  done
  if [ "$bad" -gt 0 ]; then log "  note: $bad of $r readback(s) mismatched on mtd$di@$(hex "$po")"; fi
  [ "$ok" -ge 2 ]; }
seg_expect(){ if [ "$1" = FF ]; then ff_hash "$3"; else hash_file_region "$1" "$2" "$3"; fi; }

# do_region NAME SRC ABS LEN (SRC = file whose byte 0 lands at ABS, or FF)
do_region(){ local rn=$1 src=$2 a0=$3 len=$4 segs di po ln abs so exp att kind=image
  CUR_REGION=$rn
  if [ "$src" = FF ]; then kind=erase-to-0xFF; fi
  map_abs "$a0" "$len" "$TMP/m.$rn"
  segs=$("$BB" cat "$TMP/m.$rn")
  [ -n "$segs" ] || die "internal: empty map for $rn"
  log "region $rn: abs $(hex "$a0") len $(hex "$len") ($kind)"
  if [ "$EFFECT" -eq 1 ]; then hook_best progress "region-begin-$rn"; fi
  set -- $segs
  while [ $# -ge 3 ]; do
    di=$1; po=$2; ln=$3; shift 3
    abs=$(( $(offbyidx "$di") + po )); so=$((abs - a0))
    if [ "$EFFECT" -ne 1 ]; then log "  would write ${ln}B -> mtdblock$di @$(hex "$po")"; continue; fi
    exp=$(seg_expect "$src" "$so" "$ln")
    att=0
    while :; do
      WRITES_STARTED=1
      if ! seg_write "$src" "$so" "$di" "$po" "$ln" "$att"; then log "  write command failed on mtd$di@$(hex "$po") (attempt $att)"; fi
      if seg_verify "$exp" "$di" "$po" "$ln"; then break; fi
      att=$((att+1))
      if [ "$att" -gt "$MAX_REWRITES" ]; then die "region $rn: readback mismatch persists on mtd$di@$(hex "$po") after $MAX_REWRITES rewrite(s)"; fi
      log "  readback mismatch on mtd$di@$(hex "$po"); rewrite $att/$MAX_REWRITES"
    done
    log "  segment mtd$di@$(hex "$po") len $(hex "$ln") verified"
  done
  if [ "$EFFECT" -eq 1 ]; then hook_best progress "region-done-$rn"; fi; }
sweep_region(){ local rn=$1 src=$2 a0=$3 len=$4 segs di po ln abs so exp
  CUR_REGION="final-sweep:$rn"
  map_abs "$a0" "$len" "$TMP/s.$rn"; segs=$("$BB" cat "$TMP/s.$rn")
  set -- $segs
  while [ $# -ge 3 ]; do
    di=$1; po=$2; ln=$3; shift 3
    abs=$(( $(offbyidx "$di") + po )); so=$((abs - a0))
    exp=$(seg_expect "$src" "$so" "$ln")
    seg_verify "$exp" "$di" "$po" "$ln" || die "final sweep: $rn mismatch on mtd$di@$(hex "$po")"
  done; }

# ---- read-stability pre-check ----------------------------------------------
# Motivated by the bakfs read-instability finding: refuse unless every stock
# partition reads back identically STAB_READS times in a row.
stability_check(){ local i=0 n ps dp got a b r
  log "read-stability pre-check (each stock partition x$STAB_READS)"
  for n in $EXP_NAMES; do
    ps=$(nth $((i+1)) $EXP_SIZES); dp=$(devpath "$i")
    got=$("$BB" dd if="$dp" bs=$BS count=$((ps/BS)) 2>/dev/null | "$BB" wc -c | "$BB" tr -d ' ')
    [ "$got" -eq "$ps" ] || die "short read on mtd$i ($n): $got of $ps bytes"
    a=$(hash_dev_region "$i" 0 "$ps"); r=1
    while [ "$r" -lt "$STAB_READS" ]; do
      if [ "$SELFTEST_FAULT" = unstable_read ] && [ "$n" = bakfs ]; then
        "$BB" dd if=/dev/zero of="$dp" bs=1 seek=$r count=1 conv=notrunc 2>/dev/null || true
      fi
      b=$(hash_dev_region "$i" 0 "$ps")
      [ "$a" = "$b" ] || die "unstable read on mtd$i ($n); refusing to write"
      r=$((r+1))
    done
    i=$((i+1))
  done
  log "read-stability pre-check passed"; }

# ---- private backup of per-unit config areas -------------------------------
backup_private(){ local n idx sz f
  "$BB" mkdir -p "$STAGE/private"; "$BB" chmod 700 "$STAGE/private" 2>/dev/null || true
  : > "$STAGE/private/backup-index.txt"
  for n in mdat1 mdat2; do
    idx=$(name2idx "$n"); sz=$(sizeof "$n"); f="$STAGE/private/$n.bin"
    "$BB" rm -f "$f"
    "$BB" dd if="$(devpath "$idx")" of="$f" bs=$BS count=$((sz/BS)) 2>/dev/null || die "backup read failed for $n"
    "$BB" chmod 600 "$f" 2>/dev/null || true
    [ "$(FILESIZE "$f")" -eq "$sz" ] || die "backup of $n has the wrong size"
    [ "$(SHA "$f")" = "$(hash_dev_region "$idx" 0 "$sz")" ] || die "backup of $n does not match a fresh read"
    printf '%s size=%s saved=yes\n' "$n" "$sz" >> "$STAGE/private/backup-index.txt"
  done
  log "private backup of mdat1/mdat2 saved under stage/private (content not logged)"; }

build_boot_image(){ local bi="$TMP/boot.img" k=0 pad
  make_ff; : > "$bi"
  while [ "$k" -lt $((BOOT_LEN/BS)) ]; do "$BB" cat "$FFBLK" >> "$bi"; k=$((k+1)); done
  "$BB" dd if="$UBOOT" of="$bi" conv=notrunc 2>/dev/null || die "cannot build the padded boot image"
  [ "$(FILESIZE "$bi")" -eq "$BOOT_LEN" ] || die "padded boot image has the wrong size"
  [ "$("$BB" head -c "$UB_SZ" "$bi" | "$BB" sha256sum | "$BB" awk '{print $1}')" = "$UBOOT_SHA" ] || die "padded boot image prefix != pinned U-Boot"
  pad=$((BOOT_LEN-UB_SZ))
  [ "$("$BB" tail -c "$pad" "$bi" | "$BB" sha256sum | "$BB" awk '{print $1}')" = "$(ff_bytes_hash "$pad")" ] || die "padded boot image tail is not 0xFF"
  BOOT_IMG=$bi
  log "boot image: U-Boot ${UB_SZ}B + ${pad}B 0xFF padding = 256 KiB"; }
spec_of(){ case "$1" in
    bootloader) printf '%s %s %s' "$BOOT_IMG" 0 "$BOOT_LEN";;
    env) printf '%s %s %s' "$ENV_SRC" "$ENV_BASE" "$ENV_LEN";;
    firmware) printf '%s %s %s' "$FW" "$FW_BASE" "$FW_SZ";;
    overlay) printf '%s %s %s' FF "$OVERLAY_BASE" $((FLASH_BYTES-OVERLAY_BASE));;
  esac; }

# ---- orchestrate -----------------------------------------------------------
if [ -n "$HOOK" ]; then hook_call quiesce || die "hook 'quiesce' failed; nothing written"; log "hook quiesce: ok"; fi
stability_check
backup_private
if [ -n "$HOOK" ]; then hook_call export "$STAGE/private" || die "hook 'export' failed; backup not safely off-device, nothing written"; log "hook export: ok"; fi
build_boot_image
if [ "$EFFECT" -eq 1 ]; then hook_best progress writing-start; fi
for r in $ORDER; do
  set -- $(spec_of "$r")
  do_region "$r" "$1" "$2" "$3"
done
if [ "$EFFECT" -eq 1 ]; then
  log "final sweep: re-verifying all four regions"
  for r in $ORDER; do set -- $(spec_of "$r"); sweep_region "$r" "$1" "$2" "$3"; done
  CUR_REGION="complete"
  printf 'ok\n' > "$STAGE/migrate.result"
  log "all regions written and verified in order: $ORDER"
  hook_best progress verified
else
  printf 'dry-run-ok\n' > "$STAGE/migrate.result"
  log "DRY RUN complete: nothing was written"
  hook_best resume
fi

if [ "$WRITE_MODE" -eq 1 ] && [ "$ALLOW_REBOOT" -eq 1 ] && [ "${OPENIPC_MIGRATE_REBOOT_CONFIRM:-}" = "$REBOOT_PHRASE" ]; then
  log "reboot authorized: sync + reboot -f into OpenIPC"
  hook_best progress rebooting
  "$BB" sync || true; "$BB" sleep 1 || true
  "$BB" reboot -f || true
  "$BB" sleep 5 || true
  if [ -w /proc/sysrq-trigger ]; then echo b > /proc/sysrq-trigger || true; fi
  log "migration verified but the reboot did not happen; power-cycle manually"
  EXPLICIT_EXIT=1; exit 3
fi
log "DONE. Reboot withheld (needs real-write mode, --allow-reboot and the reboot confirm phrase)."
if [ "$WRITE_MODE" -eq 1 ]; then log "stock processes stay stopped; do not resume the stock app on migrated flash"; fi
EXPLICIT_EXIT=1
exit 0
