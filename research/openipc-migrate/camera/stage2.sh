#!/bin/sh
# stage2.sh -- camera-side driver for the stock-U-Boot OpenIPC migration (Wi-Fi only iBlack).
#
# Started in the background by the migration appfs runapp.sh. It is inert
# unless the LAN host (serve_migration.py) answers the manifest poll with
# run=<our run id>, mode=go and the exact GO phrase.
#   phase1 runs under the STOCK BusyBox 1.18.4 sh (no $((...)) arithmetic, no
#          chmod/wc/tr/head/sha256sum) and never writes flash. That sh parses
#          every function of this file before the final case, so NO line here
#          may use $((...)), not even in phase2 (it is a syntax error there:
#          "you disabled math support"). It downloads the OpenIPC BusyBox
#          runtime, om_stockboot.sh and the hook into RAM, verifies each with
#          stock sha1sum AND md5sum against pins baked into migrate.conf, then
#          re-executes itself under that runtime.
#   phase2 runs entirely from RAM, re-verifies everything by SHA-256 pin and
#          then polls the manifest. Each new action seq (16 hex) runs
#          om_stockboot.sh once: diagnose and collect are read-only; probe-arm,
#          trial-arm, restore-env and final write only when the manifest also
#          carries that action's write phrase (the installer checks it), and
#          reboot only with its reboot phrase. Action files are fetched by the
#          SHA-256 the manifest names; firmware.bin and env-openipc.img only by
#          the pins baked into migrate.conf.
#          $OM_DIR/stage/flash.state: dirty (final passed its point of no
#          return) or final (verified, waiting for a power cycle) -> phase2
#          never exits (the stock userland on flash is gone) and only accepts
#          final retries / collect.
# No address, credential, SSID or key is hard-coded here (see migrate.conf).

GO_PHRASE="yes-stage-openipc-migration"
CONF=${OM_CONF:-/app/openipc-migrate/migrate.conf}
PHASE=${1:-}

[ -r "$CONF" ] || exit 0
. "$CONF"
: ${OM_DIR:=/dev/om}
: ${OM_LOCK:=/dev/openipc-migrate.lock}
: ${OM_APP_DIR:=/app/openipc-migrate}
: ${OM_POLL_INTERVAL:=20}
: ${OM_POLL_MAX:=90}
: ${OM_IDLE_MAX:=180}
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
# RAMFS (also CONFIG_SHMEM=n's "tmpfs") has zero filesystem block counts.
# The actual stock stat -f mislabels RAMFS as "romfs". Accept that alias only
# with a unique longest matching, writable ramfs/tmpfs mount in /proc/mounts.
# Real ROMFS, read-only mounts, bounded/full tmpfs and unknown output stay shut.
# stat -c is not built into the stock BusyBox; its default -f output is.
p1_ramfs_unaccounted(){
  fsstat=$(stat -f "$OM_DIR" 2>/dev/null) || return 1
  echo "$fsstat" | awk '
    $1 == "ID:" { for (i=1; i<NF; i++) if ($i == "Type:") { t=$(i+1); nt++ } }
    $1 == "Blocks:" && $2 == "Total:" && $4 == "Free:" && $6 == "Available:" {
      nb++; z=(NF == 7 && $3 == "0" && $5 == "0" && $7 == "0")
    }
    END { exit !(nt == 1 && (t == "ramfs" || t == "romfs") && nb == 1 && z) }' || return 1
  # Do not guess at proc-mounts escaping or awk -v backslash interpretation.
  # The stock shell does not support POSIX character classes in case patterns.
  case "$OM_DIR" in *\\*) return 1;; esac
  echo "$OM_DIR" | awk '
    NR != 1 || $0 !~ /^[-a-zA-Z0-9_.\/]+$/ { bad=1 }
    END { exit (NR != 1 || bad) }' || return 1
  fsdir=$(cd "$OM_DIR" 2>/dev/null && pwd -P) || return 1
  case "$fsdir" in *\\*) return 1;; esac
  awk -v p="$fsdir" '
    BEGIN { if (p !~ /^\/[-a-zA-Z0-9_.\/]*$/) bad=1 }
    NF != 6 || $2 !~ /^\// { bad=1; next }
    {
      m=$2; n=length(m)
      if (m == "/" || p == m || index(p, m "/") == 1) {
        if (n > best) { best=n; count=0 }
        if (n == best) { count++; kind=$3; opts="," $4 "," }
      }
    }
    END { exit !(!bad && count == 1 && (kind == "ramfs" || kind == "tmpfs") &&
                 index(opts, ",rw,") && !index(opts, ",ro,")) }' /proc/mounts
}
# Read meminfo from stdin so the exact stock-awk calculation is fixture-tested.
# RAMFS pages are Cached but Unevictable; do not count them as reclaimable.
# Without the accounting fields use only free RAM + reclaimable buffers.
p1_mem_avail(){ awk '
    $1 ~ /^(MemFree:|Buffers:|Cached:|Shmem:|Unevictable:)$/ {
      if (NF != 3 || $2 !~ /^[0-9]+$/ || $3 != "kB" || seen[$1]++) bad=1
      n[$1]=$2
    }
    END {
      if (bad || !seen["MemFree:"] || !seen["Buffers:"] || !seen["Cached:"]) { print 0; exit }
      v=n["MemFree:"]+n["Buffers:"]
      if (seen["Shmem:"] && seen["Unevictable:"]) v+=n["Cached:"]-n["Shmem:"]-n["Unevictable:"]
      if (v < 0) v=0
      print v
    }'; }
p1_capacity(){
  avail=$(p1_mem_avail); dirfree=0
  space=$(df "$OM_DIR" 2>/dev/null) || return 1
  dirfree=$(echo "$space" | awk '
    NR == 2 && NF >= 6 && $(NF-4) ~ /^[0-9]+$/ && $(NF-3) ~ /^[0-9]+$/ && $(NF-2) ~ /^[0-9]+$/ {
      print $(NF-2); ok=1
    }
    END { if (!ok) print -1 }')
  if [ "$dirfree" = 0 ] && p1_ramfs_unaccounted; then
    # The 10 MiB work budget and a further 2 MiB of RAM headroom must both fit.
    dirfree=$(expr "${avail:-0}" - 2048)
    p1_ge "$dirfree" 0 || dirfree=0
  fi
  p1_ge "$avail" "$OM_MIN_AVAIL_KB" && p1_ge "$dirfree" "$OM_MIN_DIRFREE_KB"
}
p1_pins(){ case "$1" in
    busybox) echo "$OM_RT_BB_SHA1 $OM_RT_BB_MD5";;
    ld-musl.so) echo "$OM_RT_LD_SHA1 $OM_RT_LD_MD5";;
    om_stockboot.sh) echo "$OM_INSTALLER_SHA1 $OM_INSTALLER_MD5";;
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
  if ! p1_capacity < /proc/meminfo; then
    p1_report "lowmem-$avail-$dirfree"; log "insufficient RAM: avail=$avail KiB, free in $OM_DIR=$dirfree KiB"; exit 0
  fi
  p1_report go-received
  for f in busybox ld-musl.so om_stockboot.sh migrate_hook.sh; do
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
  for pair in "busybox:$OM_RT_BB_SHA256" "ld-musl.so:$OM_RT_LD_SHA256" "om_stockboot.sh:$OM_INSTALLER_SHA256" \
              "migrate_hook.sh:$OM_HOOK_SHA256" "stage2.sh:$OM_STAGE2_SHA256"; do
    f=${pair%%:*}; want=${pair#*:}
    if [ "$(p2_sha "$OM_DIR/$f")" != "$want" ]; then p2_report "bundle-sha256-mismatch-$f"; log "bundle mismatch: $f"; exit 0; fi
  done
  for a in awk base64 cat cmp cp head mkdir pidof readlink rm sha256sum sleep timeout tr wc wget; do
    "$BB" --list 2>/dev/null | "$BB" grep -qx "$a" || { p2_report "runtime-missing-$a"; exit 0; }
  done
  "$BB" mkdir -p "$OM_DIR/stage" || exit 0
  p2_report phase2-start
  last=""; idle=""; held=""
  while :; do
    st=$(p2_state)
    if p2_get manifest "$OM_DIR/manifest" && [ "$(p2_mval run)" = "$OM_RUN_ID" ]; then
      mode=$(p2_mval mode); seq=$(p2_mval seq)
      if [ "$mode" = abort ] && [ "$st" != dirty ] && [ "$st" != final ]; then p2_report aborted; p2_exit; fi
      if [ "$mode" = go ] && [ "$(p2_mval go)" = "$GO_PHRASE" ] && p2_is_seq "$seq" && [ "$seq" != "$last" ]; then
        last=$seq; idle=""
        p2_run "$(p2_mval action)"
        continue
      fi
    fi
    case "$st" in
      dirty) p2_report hold-dirty-retry-final; held="$held.";;
      final) p2_report hold-final-verified-power-cycle; held="$held.";;
      *) idle="$idle."
         if [ "${#idle}" -ge "$OM_IDLE_MAX" ]; then p2_report idle-exit; p2_exit; fi;;
    esac
    if [ "$OM_HOLD_MAX" -gt 0 ] && [ "${#held}" -ge "$OM_HOLD_MAX" ]; then exit 0; fi   # offline tests only
    "$BB" sleep "$OM_POLL_INTERVAL"
  done
}
p2_state(){ s=$("$BB" cat "$OM_DIR/stage/flash.state" 2>/dev/null)
  case "$s" in clean|armed|dirty|final) echo "$s";; "") echo clean;; *) echo dirty;; esac; }
p2_is_seq(){ case "$1" in *[!0-9a-f]*|"") return 1;; esac; [ "${#1}" -eq 16 ]; }
p2_is_sha(){ case "$1" in *[!0-9a-f]*|"") return 1;; esac; [ "${#1}" -eq 64 ]; }
p2_sha(){ "$BB" sha256sum "$1" 2>/dev/null | "$BB" awk '{print $1}'; }
p2_report(){ "$BB" wget -q -T 10 -O /dev/null "$BASE/status?run=$OM_RUN_ID&phase=2&event=$1" >/dev/null 2>&1; }
p2_get(){ "$BB" rm -f "$2"; "$BB" wget -q -T 60 -O "$2" "$BASE/$1?run=$OM_RUN_ID" >/dev/null 2>&1; }
p2_mval(){ "$BB" awk -F= -v k="$1" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$OM_DIR/manifest" 2>/dev/null; }
p2_fetch(){ for try in 1 2 3; do
    if p2_get "$1" "$OM_DIR/payload/$2" && [ "$(p2_sha "$OM_DIR/payload/$2")" = "$3" ]; then return 0; fi
    [ "$try" = 3 ] || "$BB" sleep "$OM_RETRY_SLEEP"
  done
  return 1; }
p2_exit(){ "$BB" rm -rf "$OM_DIR/payload"; log "phase2 exit; stock firmware continues"; exit 0; }
# p2_run ACTION: fetch and verify the action's files, then run om_stockboot.sh once.
p2_run(){ act=$1; st=$(p2_state)
  case "$st:$act" in
    *:collect|dirty:final) ;;
    dirty:*|final:*) p2_report "refused-$act-in-state-$st"; return 0;;
  esac
  case "$act" in
    diagnose|collect) files="";;
    probe-arm) files="env-probe.img";;
    trial-arm) files="trial.img env-armed.img";;
    restore-env) files="env-base.img";;
    final) files="env-final.img";;
    *) p2_report unknown-action; return 0;;
  esac
  "$BB" rm -rf "$OM_DIR/payload"; "$BB" mkdir -p "$OM_DIR/payload" || return 0
  "$BB" cp "$OM_DIR/manifest" "$OM_DIR/manifest.run" || return 0
  for f in $files; do
    want=$(p2_mval "f.$f")
    p2_is_sha "$want" || { p2_report "no-pin-$f"; return 0; }
    p2_fetch "action/$f" "$f" "$want" || { p2_report "fetch-failed-$f"; "$BB" rm -rf "$OM_DIR/payload"; return 0; }
  done
  set -- "$act" --busybox "$BB" --stage "$OM_DIR/stage" --hook "$OM_DIR/migrate_hook.sh" \
    --payload "$OM_DIR/payload" --manifest "$OM_DIR/manifest.run"
  if [ "$act" = final ]; then
    p2_fetch payload/firmware.bin firmware.bin "$OM_FW_SHA256" || { p2_report payload-mismatch-firmware; return 0; }
    p2_fetch payload/env-openipc.img env-openipc.img "$OM_OENV_SHA256" || { p2_report payload-mismatch-oenv; return 0; }
    set -- "$@" --firmware-sha256 "$OM_FW_SHA256" --oenv-sha256 "$OM_OENV_SHA256"
  fi
  wp=$(p2_mval write); rp=$(p2_mval reboot)
  if [ -n "$wp" ]; then set -- "$@" --allow-write; OM_WRITE_CONFIRM=$wp; export OM_WRITE_CONFIRM; fi
  if [ -n "$wp" ] && [ -n "$rp" ]; then set -- "$@" --allow-reboot; OM_REBOOT_CONFIRM=$rp; export OM_REBOOT_CONFIRM; fi
  if [ -n "${OM_SIM_DIR:-}" ]; then
    # Offline test harness only (never written by build_migration_appfs.py).
    set -- "$@" --sim-dir "$OM_SIM_DIR"; MTD_PROC=$OM_SIM_MTD_PROC; export MTD_PROC
    if [ -n "${OM_SIM_FAULT:-}" ]; then export OM_SIM_FAULT; fi
  fi
  OM_BB=$BB; export OM_BB OM_HOST OM_PORT OM_RUN_ID OM_DIR
  if [ -n "${OM_QUIESCE_PROCS:-}" ]; then export OM_QUIESCE_PROCS; fi
  if [ -n "${OM_WATCHDOG_DEV:-}" ]; then export OM_WATCHDOG_DEV; fi
  p2_report "action-start-$act"; log "action $act start"
  "$BB" sh "$OM_DIR/om_stockboot.sh" "$@" > "$OM_DIR/action.out" 2>&1
  rc=$?
  unset OM_WRITE_CONFIRM OM_REBOOT_CONFIRM
  log "action $act rc=$rc"; p2_report "action-rc-$act-$rc"
  "$BB" rm -rf "$OM_DIR/payload"
  return 0; }

case "$PHASE" in
  phase1) phase1;;
  phase2) phase2;;
  *) echo "usage: stage2.sh phase1|phase2" >&2; exit 2;;
esac
