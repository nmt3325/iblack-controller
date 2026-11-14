#!/bin/sh
# migrate_hook.sh -- hook verbs called by installer/om_stockboot.sh (--hook); RAM runtime BusyBox only.
#   quiesce [all]  SIGSTOP the stock writers (cmsMain.out, SysUpdate) or, with
#                  "all" (final only), every user process except init and the
#                  RAM runtime itself, so nothing pages in code from flash that
#                  is about to be overwritten. Refuses if a watchdog device
#                  exists (a stopped app could no longer feed it); "all" also
#                  refuses unless the real /proc/mtd is the camera's (mdat1).
#   resume         SIGCONT what quiesce stopped.
#   upload NAME F  send F base64-encoded (BusyBox wget --post-file stops at the
#                  first NUL byte) to the host, which stores it privately and
#                  echoes the SHA-256 of the decoded bytes; must equal F's.
#   export DIR     upload DIR/mdat1.bin and DIR/mdat2.bin (final's backups).
#   progress EVENT best-effort status ping.
# Environment (exported by stage2.sh): OM_BB OM_HOST OM_PORT OM_RUN_ID OM_DIR
# [OM_QUIESCE_PROCS] [OM_WATCHDOG_DEV: offline tests only]. Prints no file
# contents, hashes or secrets.
BB=${OM_BB:-busybox}
PIDS="$OM_DIR/quiesced.pids"
WDT=${OM_WATCHDOG_DEV:-/dev/watchdog}
url(){ echo "http://$OM_HOST:$OM_PORT/$1?run=$OM_RUN_ID"; }
stop_pid(){ kill -STOP "$1" 2>/dev/null && echo "$1" >> "$PIDS"; }
upload(){ n=$1; f=$2
  case "$n" in ""|*[!A-Za-z0-9._-]*) echo "hook: bad upload name"; return 1;; esac
  [ -f "$f" ] || { echo "hook: missing $n"; return 1; }
  want=$("$BB" sha256sum "$f" | "$BB" awk '{print $1}')
  b="$OM_DIR/upload.$n.b64"
  "$BB" base64 "$f" > "$b" || { "$BB" rm -f "$b"; echo "hook: cannot encode $n"; return 1; }
  got=$("$BB" wget -q -T 60 -O - --post-file "$b" "$(url "upload/$n")" 2>/dev/null | "$BB" head -c 64)
  "$BB" rm -f "$b"
  [ -n "$want" ] && [ "$got" = "$want" ] || { echo "hook: host did not confirm $n"; return 1; }
  return 0; }
case "${1:-}" in
  quiesce)
    if [ -e "$WDT" ]; then echo "hook: $WDT exists; refusing to stop the stock app"; exit 1; fi
    [ -f "$PIDS" ] || : > "$PIDS" || exit 1
    if [ "${2:-}" = all ] && [ -z "${OM_QUIESCE_PROCS:-}" ]; then
      "$BB" grep -q '"mdat1"$' /proc/mtd 2>/dev/null || { echo "hook: /proc/mtd is not the camera's; refusing"; exit 1; }
      me=$("$BB" readlink -f "$BB")
      for d in /proc/[0-9]*; do pid=${d#/proc/}
        [ "$pid" = 1 ] && continue
        [ -n "$("$BB" head -c 1 "$d/cmdline" 2>/dev/null)" ] || continue   # kernel thread or zombie
        [ "$("$BB" readlink "$d/exe" 2>/dev/null)" = "$me" ] && continue      # the RAM runtime (us)
        stop_pid "$pid"
      done
    else
      for name in ${OM_QUIESCE_PROCS:-cmsMain.out SysUpdate}; do
        for pid in $("$BB" pidof "$name" 2>/dev/null); do stop_pid "$pid"; done
      done
    fi
    "$BB" sleep 1
    for pid in $("$BB" cat "$PIDS"); do
      [ -e "/proc/$pid" ] || continue
      st=$("$BB" awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)
      case "$st" in T|t|Z|X|"") ;; *) echo "hook: pid $pid did not stop (state '$st')"; exit 1;; esac
    done
    echo "hook: quiesced $("$BB" wc -l < "$PIDS" | "$BB" tr -d ' ') process(es)"
    "$BB" sync
    exit 0;;
  resume)
    [ -f "$PIDS" ] || exit 0
    for pid in $("$BB" cat "$PIDS"); do kill -CONT "$pid" 2>/dev/null; done
    "$BB" rm -f "$PIDS"
    exit 0;;
  upload)
    upload "${2:-}" "${3:-}" || exit 1
    exit 0;;
  export)
    for n in mdat1 mdat2; do upload "$n" "${2:-}/$n.bin" || exit 1; done
    echo "hook: backups exported and confirmed by the host"
    exit 0;;
  progress)
    "$BB" wget -q -T 5 -O /dev/null "$(url status)&phase=installer&event=${2:-none}" >/dev/null 2>&1
    exit 0;;
  *) echo "hook: unknown verb '${1:-}'"; exit 2;;
esac
