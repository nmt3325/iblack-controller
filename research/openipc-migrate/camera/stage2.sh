#!/bin/sh
# stage2.sh -- camera-side driver for the stock iBlack -> OpenIPC online migration.
#
# Started in the background by the migration appfs runapp.sh. It is inert
# unless the LAN host (serve_migration.py) answers the manifest poll with
# run=<our run id>, mode=go and the exact GO phrase.
#   phase1 runs under the STOCK BusyBox 1.18.4 sh (no $((...)) arithmetic, no
#          chmod/wc/tr/head/sha256sum) and never writes flash. That sh parses
#          every function of this file before the final case, so NO line here
#          may use $((...)), not even in phase2 (it is a syntax error there:
#          "you disabled math support"). It downloads
#          the OpenIPC BusyBox runtime, the installer and the hook into RAM,
#          verifies each with stock sha1sum AND md5sum against pins baked into
#          migrate.conf, then re-executes itself under that runtime.
#   phase2 runs entirely from RAM, re-verifies everything by SHA-256 pin,
#          downloads and verifies the payloads, re-reads the manifest and runs
#          openipc_migrate.sh. Flash is written only when the manifest ALSO
#          carries the installer's write phrase (else: read-only rehearsal);
#          a reboot additionally needs the reboot phrase.
# No address, credential, SSID or key is hard-coded here (see migrate.conf).

GO_PHRASE="yes-stage-openipc-migration"
WRITE_PHRASE="yes-migrate-this-camera-to-openipc"
REBOOT_PHRASE="yes-reboot-into-openipc"
CONF=${OM_CONF:-/app/openipc-migrate/migrate.conf}
PHASE=${1:-}

[ -r "$CONF" ] || exit 0
. "$CONF"
: ${OM_DIR:=/dev/om}
: ${OM_LOCK:=/dev/openipc-migrate.lock}
: ${OM_APP_DIR:=/app/openipc-migrate}
: ${OM_POLL_INTERVAL:=20}
: ${OM_POLL_MAX:=90}
: ${OM_MIN_AVAIL_KB:=12288}
: ${OM_MIN_DIRFREE_KB:=10240}
: ${OM_RETRY_SLEEP:=5}
: ${OM_HOLD_MAX:=0}
: ${OM_STOCK_BB:=/bin/busybox}
[ -n "${OM_HOST:-}" ] && [ -n "${OM_PORT:-}" ] && [ -n "${OM_RUN_ID:-}" ] || exit 0
BASE="http://$OM_HOST:$OM_PORT"
log(){ echo "[om:$PHASE] $*" >> "$OM_DIR/stage2.log" 2>/dev/null; }

# --- phase1 begin (stock BusyBox 1.18.4 sh: no arithmetic expansion) ---
p1_get(){ rm -f "$2"; wget -q -T 30 -O "$2" "$BASE/$1?run=$OM_RUN_ID" >/dev/null 2>&1 && [ -s "$2" ]; }
p1_report(){ wget -q -T 10 -O /dev/null "$BASE/status?run=$OM_RUN_ID&phase=1&event=$1" >/dev/null 2>&1; }
p1_mval(){ awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$OM_DIR/manifest" 2>/dev/null; }
p1_sum(){ "$1" "$2" 2>/dev/null | awk '{print $1}'; }
p1_ge(){ expr "${1:-0}" \>= "${2:-0}" >/dev/null 2>&1; }
p1_pins(){ case "$1" in
    busybox) echo "$OM_RT_BB_SHA1 $OM_RT_BB_MD5";;
    ld-musl.so) echo "$OM_RT_LD_SHA1 $OM_RT_LD_MD5";;
    openipc_migrate.sh) echo "$OM_INSTALLER_SHA1 $OM_INSTALLER_MD5";;
    migrate_hook.sh) echo "$OM_HOOK_SHA1 $OM_HOOK_MD5";;
    *) echo "none none";;
  esac; }
# p1_install SRC DEST SHA1 MD5: stock BusyBox has no chmod, so copy an
# executable first (cp keeps its mode) and overwrite the content in place.
p1_install(){ cp "$OM_STOCK_BB" "$2" 2>/dev/null || return 1
  cat "$1" > "$2" || return 1
  rm -f "$1"
  [ "$(p1_sum sha1sum "$2")" = "$3" ] && [ "$(p1_sum md5sum "$2")" = "$4" ]; }
p1_wait_for_go(){ n=0
  while :; do
    if p1_get manifest "$OM_DIR/manifest"; then
      if [ "$(p1_mval run)" != "$OM_RUN_ID" ]; then
        p1_report run-mismatch
      else
        case "$(p1_mval mode)" in
          go) if [ "$(p1_mval go)" = "$GO_PHRASE" ]; then return 0; fi
              p1_report go-phrase-missing;;
          abort) p1_report aborted; return 1;;
          *) p1_report standby;;
        esac
      fi
    fi
    n=$(expr $n + 1)
    if p1_ge "$n" "$OM_POLL_MAX"; then p1_report standby-timeout; return 1; fi
    sleep "$OM_POLL_INTERVAL"
  done; }
phase1(){
  mkdir "$OM_LOCK" 2>/dev/null || exit 0
  mkdir -p "$OM_DIR" || exit 0
  log "phase1 start (stock sh); polling $BASE"
  if ! p1_wait_for_go; then log "no go; stock firmware continues"; exit 0; fi
  avail=$(awk '/^MemFree:/{f=$2} /^Buffers:/{b=$2} /^Cached:/{c=$2} /^Shmem:/{s=$2} END{print f+b+c-s}' /proc/meminfo)
  dirfree=$(df "$OM_DIR" 2>/dev/null | awk 'NR==2 {print $(NF-2)}')
  if ! p1_ge "$avail" "$OM_MIN_AVAIL_KB" || ! p1_ge "$dirfree" "$OM_MIN_DIRFREE_KB"; then
    p1_report "lowmem-$avail-$dirfree"; log "insufficient RAM: avail=$avail KiB, free in $OM_DIR=$dirfree KiB"; exit 0
  fi
  p1_report go-received
  for f in busybox ld-musl.so openipc_migrate.sh migrate_hook.sh; do
    if ! p1_get "runtime/$f" "$OM_DIR/dl.$f"; then p1_report "fetch-failed-$f"; exit 0; fi
    pins=$(p1_pins "$f")
    if ! p1_install "$OM_DIR/dl.$f" "$OM_DIR/$f" "${pins% *}" "${pins#* }"; then
      rm -f "$OM_DIR/$f"; p1_report "hash-mismatch-$f"; log "pin mismatch for $f"; exit 0
    fi
  done
  if ! "$OM_DIR/busybox" true 2>/dev/null; then p1_report runtime-exec-failed; exit 0; fi
  cp "$OM_APP_DIR/stage2.sh" "$OM_DIR/stage2.sh" && cp "$CONF" "$OM_DIR/migrate.conf" || exit 0
  p1_report runtime-ready
  OM_CONF="$OM_DIR/migrate.conf"; export OM_CONF
  exec "$OM_DIR/busybox" sh "$OM_DIR/stage2.sh" phase2
}
# --- phase1 end ---

phase2(){
  BB="$OM_DIR/busybox"
  log "phase2 start (OpenIPC BusyBox from RAM)"
  for pair in "busybox:$OM_RT_BB_SHA256" "ld-musl.so:$OM_RT_LD_SHA256" "openipc_migrate.sh:$OM_INSTALLER_SHA256" \
              "migrate_hook.sh:$OM_HOOK_SHA256" "stage2.sh:$OM_STAGE2_SHA256"; do
    f=${pair%%:*}; want=${pair#*:}
    if [ "$(p2_sha "$OM_DIR/$f")" != "$want" ]; then p2_report "bundle-sha256-mismatch-$f"; log "bundle mismatch: $f"; exit 0; fi
  done
  # Applets the hook and this phase need beyond the installer's own check.
  for a in awk base64 head mkdir pidof rm sha256sum sleep timeout wget; do
    "$BB" --list 2>/dev/null | "$BB" grep -qx "$a" || p2_stop "runtime-missing-$a"
  done
  p2_report phase2-start
  "$BB" mkdir -p "$OM_DIR/payload"
  p2_fetch firmware.bin "$OM_FW_SHA256" || p2_stop payload-mismatch-firmware
  p2_fetch uboot.bin "$OM_UBOOT_SHA256" || p2_stop payload-mismatch-uboot
  if [ "$OM_ENV_MODE" = image ]; then p2_fetch env.img "$OM_ENV_SHA256" || p2_stop payload-mismatch-env; fi
  p2_report payloads-verified
  p2_get manifest "$OM_DIR/manifest" || p2_stop manifest-unreachable
  if [ "$(p2_mval run)" != "$OM_RUN_ID" ] || [ "$(p2_mval mode)" != go ] || [ "$(p2_mval go)" != "$GO_PHRASE" ]; then
    p2_stop go-withdrawn
  fi
  WRITE=0; REBOOT=0
  if [ "$(p2_mval write)" = "$WRITE_PHRASE" ]; then WRITE=1; fi
  if [ "$WRITE" -eq 1 ] && [ "$(p2_mval reboot)" = "$REBOOT_PHRASE" ]; then REBOOT=1; fi
  set -- --busybox "$BB" --stage "$OM_DIR/stage" --hook "$OM_DIR/migrate_hook.sh" \
    --firmware "$OM_DIR/payload/firmware.bin" --firmware-sha256 "$OM_FW_SHA256" \
    --uboot "$OM_DIR/payload/uboot.bin" --uboot-sha256 "$OM_UBOOT_SHA256"
  if [ "$OM_ENV_MODE" = image ]; then set -- "$@" --env-image "$OM_DIR/payload/env.img" --env-sha256 "$OM_ENV_SHA256"
  else set -- "$@" --env-erase; fi
  if [ -n "${OM_SIM_DIR:-}" ]; then
    # Offline test harness only (never written by build_migration_appfs.py).
    MTD_PROC=$OM_SIM_MTD_PROC; export MTD_PROC
    if [ "$WRITE" -eq 1 ]; then set -- "$@" --dry-run-dir "$OM_SIM_DIR"; else MTD_DEV_DIR=$OM_SIM_DIR; export MTD_DEV_DIR; fi
  elif [ "$WRITE" -eq 1 ]; then
    set -- "$@" --allow-flash-write
    OPENIPC_MIGRATE_CONFIRM=$WRITE_PHRASE; export OPENIPC_MIGRATE_CONFIRM
    if [ "$REBOOT" -eq 1 ]; then
      set -- "$@" --allow-reboot
      OPENIPC_MIGRATE_REBOOT_CONFIRM=$REBOOT_PHRASE; export OPENIPC_MIGRATE_REBOOT_CONFIRM
    fi
  fi
  OM_BB=$BB; export OM_BB OM_HOST OM_PORT OM_RUN_ID OM_DIR
  if [ -n "${OM_QUIESCE_PROCS:-}" ]; then export OM_QUIESCE_PROCS; fi
  if [ -n "${OM_WATCHDOG_DEV:-}" ]; then export OM_WATCHDOG_DEV; fi
  mode=rehearsal; if [ "$WRITE" -eq 1 ]; then mode=write; fi
  p2_report "installer-start-$mode"; log "installer start ($mode)"
  "$BB" sh "$OM_DIR/openipc_migrate.sh" "$@" > "$OM_DIR/installer.out" 2>&1
  rc=$?
  log "installer rc=$rc"
  p2_report "installer-rc-$rc"
  if [ -f "$OM_DIR/stage/migrate.log" ]; then
    "$BB" wget -q -T 30 -O /dev/null --post-file "$OM_DIR/stage/migrate.log" "$BASE/report?run=$OM_RUN_ID" >/dev/null 2>&1
  fi
  if [ -z "${OM_SIM_DIR:-}" ]; then
    # Never exit after flash was modified: the parent would be re-parented to
    # the stock init, whose code now lives on overwritten flash.
    case "$rc:$WRITE" in
      0:1) p2_hold migrated-reboot-withheld;;
      2:*) p2_hold partial-state;;
      3:*) p2_hold migrated-reboot-failed;;
    esac
  fi
  case "$rc:$WRITE" in
    0:1) p2_report migrated;;
    0:0) p2_report rehearsal-ok;;
    *) p2_report "stopped-rc-$rc";;
  esac
  "$BB" rm -rf "$OM_DIR/payload"
  exit 0
}
p2_sha(){ "$BB" sha256sum "$1" 2>/dev/null | "$BB" awk '{print $1}'; }
p2_report(){ "$BB" wget -q -T 10 -O /dev/null "$BASE/status?run=$OM_RUN_ID&phase=2&event=$1" >/dev/null 2>&1; }
p2_get(){ "$BB" rm -f "$2"; "$BB" wget -q -T 60 -O "$2" "$BASE/$1?run=$OM_RUN_ID" >/dev/null 2>&1; }
p2_mval(){ "$BB" awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$OM_DIR/manifest" 2>/dev/null; }
p2_fetch(){
  for try in 1 2 3; do
    if p2_get "payload/$1" "$OM_DIR/payload/$1" && [ "$(p2_sha "$OM_DIR/payload/$1")" = "$2" ]; then return 0; fi
    [ "$try" = 3 ] || "$BB" sleep "$OM_RETRY_SLEEP"
  done
  return 1; }
p2_stop(){ p2_report "$1"; log "stopped before the installer: $1"; "$BB" rm -rf "$OM_DIR/payload"; exit 0; }
p2_hold(){ k=
  while :; do
    p2_report "$1"
    if [ "$OM_HOLD_MAX" -gt 0 ]; then k="$k."; if [ "${#k}" -ge "$OM_HOLD_MAX" ]; then return 0; fi; fi
    "$BB" sleep 60
  done; }

case "$PHASE" in
  phase1) phase1;;
  phase2) phase2;;
  *) echo "usage: stage2.sh phase1|phase2" >&2; exit 2;;
esac
