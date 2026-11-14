#!/bin/sh
# migrate_hook.sh -- hook verbs called by openipc_migrate.sh (via --hook).
#   quiesce        SIGSTOP the stock writers (cmsMain.out, SysUpdate) so nothing
#                  touches flash during the stability check, backup and writes.
#                  Refuses if a watchdog device exists (a stopped app could no
#                  longer feed it). Must succeed or the installer aborts.
#   export DIR     upload DIR/mdat1.bin and DIR/mdat2.bin to the LAN host and
#                  require the host to echo each file's SHA-256. Must succeed.
#                  BusyBox wget --post-file sends the file as a C string (it
#                  stops at the first NUL byte; seen with the OpenIPC 1.36.1
#                  build under qemu), so each backup travels base64-encoded and
#                  the host echoes the SHA-256 of the DECODED bytes, which must
#                  equal the SHA-256 of the local raw file.
#   progress EVENT best-effort status ping.
#   resume         SIGCONT what quiesce stopped (only used before any write).
# Environment (exported by stage2.sh): OM_BB OM_HOST OM_PORT OM_RUN_ID OM_DIR
# [OM_QUIESCE_PROCS] [OM_WATCHDOG_DEV: offline tests only]. Prints no file
# contents, hashes or secrets.
BB=${OM_BB:-busybox}
PIDS="$OM_DIR/quiesced.pids"
WDT=${OM_WATCHDOG_DEV:-/dev/watchdog}
url(){ printf 'http://%s:%s/%s?run=%s' "$OM_HOST" "$OM_PORT" "$1" "$OM_RUN_ID"; }
case "${1:-}" in
  quiesce)
    if [ -e "$WDT" ]; then echo "hook: $WDT exists; refusing to stop the stock app"; exit 1; fi
    : > "$PIDS" || exit 1
    for name in ${OM_QUIESCE_PROCS:-cmsMain.out SysUpdate}; do
      for pid in $("$BB" pidof "$name" 2>/dev/null); do
        kill -STOP "$pid" 2>/dev/null && echo "$pid" >> "$PIDS"
      done
    done
    "$BB" sleep 1
    for pid in $("$BB" cat "$PIDS"); do
      st=$("$BB" awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)
      case "$st" in T|t) ;; *) echo "hook: pid $pid did not stop (state '$st')"; exit 1;; esac
    done
    echo "hook: quiesced $("$BB" wc -l < "$PIDS" | "$BB" tr -d ' ') process(es)"
    "$BB" sync
    exit 0;;
  export)
    dir=${2:-}
    for n in mdat1 mdat2; do
      f="$dir/$n.bin"; b="$dir/$n.b64"
      [ -f "$f" ] || { echo "hook: missing backup $n"; exit 1; }
      want=$("$BB" sha256sum "$f" | "$BB" awk '{print $1}')
      "$BB" base64 "$f" > "$b" || { "$BB" rm -f "$b"; echo "hook: cannot encode backup $n"; exit 1; }
      got=$("$BB" wget -q -T 30 -O - --post-file "$b" "$(url "upload/$n")" 2>/dev/null | "$BB" head -c 64)
      "$BB" rm -f "$b"
      [ -n "$want" ] && [ "$got" = "$want" ] || { echo "hook: host did not confirm backup $n"; exit 1; }
    done
    echo "hook: backups exported and confirmed by the host"
    exit 0;;
  progress)
    "$BB" wget -q -T 5 -O /dev/null "$(url status)&phase=installer&event=${2:-none}" >/dev/null 2>&1
    exit 0;;
  resume)
    [ -f "$PIDS" ] || exit 0
    for pid in $("$BB" cat "$PIDS"); do kill -CONT "$pid" 2>/dev/null; done
    "$BB" rm -f "$PIDS"
    exit 0;;
  *) echo "hook: unknown verb '${1:-}'"; exit 2;;
esac
