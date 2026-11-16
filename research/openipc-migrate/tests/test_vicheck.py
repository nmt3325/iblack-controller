#!/usr/bin/env python3
"""video/S96vicheck (cold-start check and VI stall monitor) against a simulated majestic and VI.

The script runs under host BusyBox ash in its foreground `run` mode. sleep, pidof and logger are shell functions
that advance a virtual clock, so a scenario of many minutes takes well under a second. The model follows what
unit 2 showed on hardware: the VIU latches its enable bits only if the sensor already streams when majestic
enables the VI (so a start while the AR0141 is in standby never gets a frame, and the next start does), majestic's
sensor init leaves the sensor streaming, the channel row of /proc/umap/vi is missing while majestic is not running,
and frames arrive at 27 per second (the VI shows up SIM_CFGDELAY s after a start).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "video" / "S96vicheck"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")

# The simulated hardware lives in SIM_* shell variables of the harness (the script has its own STALL etc.).
# VICHECK_MAJESTIC names a shell function, so a restart runs in the same shell; only /proc/umap/vi is a real file,
# rewritten after every sleep.
HARNESS = r"""
SIM_T=0 SIM_PID= SIM_NEXTPID=100 SIM_AGE=0 SIM_CNT=0 SIM_LATCHED=0 SIM_STALL=0 SIM_DEAD=0 SIM_RESTARTS=0
majestic_start() {
	SIM_PID=$SIM_NEXTPID SIM_NEXTPID=$((SIM_NEXTPID + 1)) SIM_AGE=0 SIM_CNT=0 SIM_STALL=0
	SIM_LATCHED=$SIM_STREAM  # the VI is enabled before the sensor init
	SIM_STREAM=1             # the sensor init leaves the sensor streaming
}
majestic_stop() { SIM_PID=; }
sim_majestic() {
	[ "$1" = restart ] || return 1
	majestic_stop
	majestic_start
	SIM_RESTARTS=$((SIM_RESTARTS + 1))
}
write_vi() {
	{
		echo "-----VI PHYCHN ATTR-------------------------------------------------------------"
		echo " PhyChn CapX CapY  CapW  CapH  DstW  DstH CapSel Mirror Flip IntEn PixFom SrcRat DstRat"
		echo "      0    0    0  1280   720  1280   720   both      N    N     Y  sp420     -1     -1"
		echo
		echo "-----VI PHYCHN STATUS 1----------------------------------------------------------"
		echo " PhyChn  Dev      IntCnt  VbFail  LosInt  TopLos  BotLos BufCnt  IntT  SendT  Field  Stride"
		if [ -n "$SIM_PID" ] && [ "$SIM_CONF" = 1 ] && [ "$SIM_AGE" -ge "$SIM_CFGDELAY" ]; then
			printf '      0    0  %10s       0       2       0       2      2   476    329    frm    1280\n' "$SIM_CNT"
		fi
		echo
		echo "-----VI PHYCHN STATUS 2----------------------------------------------------------"
	} > "$D/vi"
}
apply_events() {
	for sim_ev in $SIM_EVENTS; do
		[ "${sim_ev%%:*}" = "$SIM_T" ] || continue
		case ${sim_ev#*:} in
		sensor-stop) SIM_STREAM=0 ;;
		vi-stall) SIM_STALL=1 ;;  # cleared by the next start
		vi-dead) SIM_DEAD=1 ;;    # never cleared
		user-restart) majestic_stop; majestic_start ;;
		user-stop) majestic_stop ;;
		user-start) majestic_start ;;
		esac
	done
}
sleep() {
	sim_s=0
	while [ "$sim_s" -lt "$1" ]; do
		sim_s=$((sim_s + 1)) SIM_T=$((SIM_T + 1))
		apply_events
		if [ -n "$SIM_PID" ]; then
			SIM_AGE=$((SIM_AGE + 1))
			if [ "$SIM_LATCHED$SIM_STREAM$SIM_STALL$SIM_DEAD$SIM_CONF" = 11001 ] &&
				[ "$SIM_AGE" -ge "$SIM_CFGDELAY" ]; then
				SIM_CNT=$((SIM_CNT + 27))
			fi
		fi
		if [ "$SIM_T" -ge "$SIM_END" ]; then
			echo "$SIM_RESTARTS" > "$D/restarts"
			exit 0
		fi
	done
	write_vi
}
pidof() { [ -n "$SIM_PID" ] || return 1; echo "$SIM_PID"; }
logger() { shift 2; echo "$SIM_T $*" >> "$D/log"; }
majestic_start   # S95majestic start ran before S96vicheck
write_vi
set -- run
. "$SCRIPT"
"""


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class ViCheck(unittest.TestCase):
    def run_sim(self, *, end: int, events: tuple = (), stream: int = 1, conf: int = 1, cfgdelay: int = 3):
        d = Path(tempfile.mkdtemp(prefix="vicheck-"))
        self.addCleanup(shutil.rmtree, d, True)
        (d / "log").write_text("")
        (d / "harness.sh").write_text(HARNESS)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "D": str(d), "SIM_END": str(end),
               "SIM_EVENTS": " ".join(events), "SIM_STREAM": str(stream), "SIM_CONF": str(conf),
               "SIM_CFGDELAY": str(cfgdelay), "SCRIPT": str(SCRIPT),
               "VICHECK_VI_PROC": str(d / "vi"), "VICHECK_MAJESTIC": "sim_majestic",
               "VICHECK_PIDFILE": str(d / "vicheck.pid")}
        r = subprocess.run([BUSYBOX, "sh", str(d / "harness.sh")], env=env, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = [(int(t), m) for t, _, m in (line.partition(" ") for line in (d / "log").read_text().splitlines())]
        return log, int((d / "restarts").read_text())

    def monitor_restarts(self, log):
        return [t for t, m in log if m.startswith("no frame from the VI")]

    def test_running_at_boot_is_left_alone(self):
        log, restarts = self.run_sim(end=900)
        self.assertEqual([m for _, m in log], ["video input running"])
        self.assertEqual(restarts, 0)

    def test_cold_start_gets_one_restart(self):
        log, restarts = self.run_sim(end=900, stream=0)
        self.assertEqual([m for _, m in log], [
            "VI configured but no frame from the sensor (cold start); restarting majestic once",
            "video input running after one restart"])
        self.assertEqual(restarts, 1)
        self.assertLessEqual(log[0][0], 15)

    def test_sensor_stop_recovers_after_two_restarts(self):
        log, restarts = self.run_sim(end=900, events=("200:sensor-stop",))
        self.assertEqual(restarts, 2)
        first, second = self.monitor_restarts(log)
        self.assertTrue(230 <= first <= 245, first)
        self.assertLessEqual(second - first, 50)
        self.assertEqual(log[-1][1], "video input running again after 2 restart(s)")

    def test_vi_stall_recovers_after_one_restart(self):
        log, restarts = self.run_sim(end=900, events=("200:vi-stall",))
        self.assertEqual(restarts, 1)
        self.assertEqual(log[-1][1], "video input running again after 1 restart(s)")

    def test_no_recovery_backs_off(self):
        log, restarts = self.run_sim(end=1600, events=("200:vi-dead",))
        times = self.monitor_restarts(log)
        self.assertEqual(restarts, len(times))
        self.assertGreaterEqual(len(times), 4)
        self.assertLessEqual(times[1] - times[0], 50)
        self.assertLessEqual(times[2] - times[1], 50)
        self.assertGreaterEqual(times[3] - times[2], 600)
        self.assertFalse(any(m.startswith("video input running again") for _, m in log))

    def test_slow_vi_setup_is_not_progress(self):
        # The VI shows up only after the first poll that follows a restart: its first counter value is not progress.
        log, restarts = self.run_sim(end=1600, events=("200:vi-dead",), cfgdelay=15)
        times = self.monitor_restarts(log)
        self.assertGreaterEqual(len(times), 4)
        self.assertGreaterEqual(times[3] - times[2], 600)
        self.assertFalse(any(m.startswith("video input running again") for _, m in log))

    def test_user_restart_and_stop_are_not_stalls(self):
        log, restarts = self.run_sim(end=900, events=("150:user-restart", "300:user-stop", "420:user-start"))
        self.assertEqual(restarts, 0)
        self.assertEqual([m for _, m in log], ["video input running"])

    def test_vi_never_configured_is_left_alone(self):
        log, restarts = self.run_sim(end=600, conf=0)
        self.assertEqual([m for _, m in log], ["VI not configured within 90 s; nothing done"])
        self.assertEqual(restarts, 0)


if __name__ == "__main__":
    unittest.main()
