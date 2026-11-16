#!/bin/sh
# migrate_hook.sh -- hook verbs called by installer/om_stockboot.sh (--hook); RAM runtime BusyBox only.
#   quiesce [all]  SIGSTOP the stock writers (cmsMain.out, SysUpdate) or, with
#                  "all" (final, wdtgate), every user process except init and the
#                  RAM runtime itself, so nothing pages in code from flash that
#                  is about to be overwritten. cmsMain.out is also the only
#                  feeder of the SoC's SP805 watchdog (through /dev/mem: the
#                  stock kernel has no watchdog driver), so once everything is
#                  stopped the watchdog is switched off and its control word is
#                  saved (unit 1 was reset ~44 s into a quiesced final without
#                  this). If a process does not stop, or the watchdog cannot be
#                  read or does not stop, everything is undone (watchdog re-armed,
#                  processes continued) and the verb fails. Refuses if a watchdog
#                  device exists (a driver would own the watchdog); "all" also
#                  refuses unless the real /proc/mtd is the camera's (mdat1).
#   resume         re-arm the watchdog as saved, then SIGCONT what quiesce stopped.
#   wdt-rearm      re-arm the watchdog as saved (before a reboot: should the
#                  reboot hang, the watchdog still resets the SoC).
#   wdt-status     one line: wdt ctrl= value= load= lock= ris= id= cid= saved=
#                  (decimal register values; saved=none when nothing is saved).
#   upload NAME F  send F base64-encoded (BusyBox wget --post-file stops at the
#                  first NUL byte) to the host, which stores it privately and
#                  echoes the SHA-256 of the decoded bytes; must equal F's.
#   export DIR     upload DIR/mdat1.bin and DIR/mdat2.bin (final's backups).
#   progress EVENT best-effort status ping.
# Environment (exported by stage2.sh): OM_BB OM_HOST OM_PORT OM_RUN_ID OM_DIR
# [OM_QUIESCE_PROCS] [OM_WATCHDOG_DEV OM_DEVMEM: offline tests only]. Prints no
# file contents, hashes or secrets.
BB=${OM_BB:-busybox}
PIDS="$OM_DIR/quiesced.pids"
WDT=${OM_WATCHDOG_DEV:-/dev/watchdog}
WSAVE="$OM_DIR/wdt.saved"
# Hi3518EV100 watchdog (ARM SP805 at 0x20040000); every register is write-locked
# unless W_KEY was written to W_LOCK. CTRL bit 0 runs the counter, bit 1 the reset.
W_LOAD=0x20040000; W_VALUE=0x20040004; W_CTRL=0x20040008; W_INTCLR=0x2004000C; W_RIS=0x20040010
W_LOCK=0x20040C00; W_KEY=0x1ACCE551; W_PID=0x20040FE0; W_CID=0x20040FF0
url(){ echo "http://$OM_HOST:$OM_PORT/$1?run=$OM_RUN_ID"; }
stop_pid(){ kill -STOP "$1" 2>/dev/null && echo "$1" >> "$PIDS"; }
mem(){ if [ -n "${OM_DEVMEM:-}" ]; then "$OM_DEVMEM" "$@"; else "$BB" devmem "$@"; fi; }
# rd32 ADDR: the 32-bit register in decimal; fails unless devmem printed one hex word.
rd32(){ v=$(mem "$1" 32 2>/dev/null) || return 1
  case "$v" in 0x|0x*[!0-9A-Fa-f]*) return 1;; 0x*) ;; *) return 1;; esac
  [ "${#v}" -le 10 ] || return 1
  echo $((v)); }
wr32(){ mem "$1" 32 "$2" >/dev/null 2>&1; }
wdt_off(){ c=$(rd32 $W_CTRL) || { echo "hook: cannot read the watchdog"; return 1; }
  if [ ! -f "$WSAVE" ]; then echo "$c" > "$WSAVE" || { echo "hook: cannot save the watchdog state"; return 1; }; fi
  wr32 $W_LOCK $W_KEY; wr32 $W_CTRL 0; wr32 $W_LOCK 0
  c=$(rd32 $W_CTRL) || { echo "hook: cannot read the watchdog"; return 1; }
  [ $((c & 3)) -eq 0 ] || { echo "hook: the watchdog did not stop (ctrl $c)"; return 1; }
  a=$(rd32 $W_VALUE); "$BB" sleep 1; b=$(rd32 $W_VALUE)
  if [ -n "$a" ] && [ "$a" = "$b" ]; then f=frozen; else f=moving; fi
  echo "hook: watchdog off (ctrl was $("$BB" cat "$WSAVE"), counter $f)"
  return 0; }
wdt_restore(){ [ -f "$WSAVE" ] || return 0
  s=$("$BB" cat "$WSAVE" 2>/dev/null)
  case "$s" in ""|*[!0-9]*) echo "hook: the saved watchdog state is unreadable"; return 1;; esac
  if [ $((s & 3)) -ne 0 ]; then
    wr32 $W_LOCK $W_KEY; wr32 $W_INTCLR 1; wr32 $W_CTRL "$s"; wr32 $W_LOCK 0
    c=$(rd32 $W_CTRL) || { echo "hook: cannot read the watchdog"; return 1; }
    [ $((c & 3)) -eq $((s & 3)) ] || { echo "hook: the watchdog was not re-armed (ctrl $c)"; return 1; }
    echo "hook: watchdog re-armed"
  fi
  "$BB" rm -f "$WSAVE"
  return 0; }
resume_pids(){ [ -f "$PIDS" ] || return 0
  for pid in $("$BB" cat "$PIDS"); do kill -CONT "$pid" 2>/dev/null; done
  "$BB" rm -f "$PIDS"; }
undo(){ wdt_restore; resume_pids; echo "hook: quiesce undone (watchdog restored, processes continued)"; }
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
      case "$st" in T|t|Z|X|"") ;; *) echo "hook: pid $pid did not stop (state '$st')"; undo; exit 1;; esac
    done
    wdt_off || { undo; exit 1; }
    echo "hook: quiesced $("$BB" wc -l < "$PIDS" | "$BB" tr -d ' ') process(es)"
    "$BB" sync
    exit 0;;
  resume)
    r=0; wdt_restore || r=1
    resume_pids
    exit $r;;
  wdt-rearm)
    wdt_restore || exit 1
    exit 0;;
  wdt-status)
    line=wdt
    for r in ctrl:$W_CTRL value:$W_VALUE load:$W_LOAD lock:$W_LOCK ris:$W_RIS; do
      v=$(rd32 "${r#*:}") || { echo "wdt unreadable"; exit 1; }
      line="$line ${r%%:*}=$v"
    done
    for g in id:$W_PID cid:$W_CID; do base=$((${g#*:})); ids=""
      for k in 0 4 8 12; do v=$(rd32 $((base + k))) || { echo "wdt unreadable"; exit 1; }; ids="$ids,$((v & 255))"; done
      line="$line ${g%%:*}=${ids#,}"
    done
    s=none; [ -f "$WSAVE" ] && s=$("$BB" cat "$WSAVE" 2>/dev/null)
    echo "$line saved=$s"
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
