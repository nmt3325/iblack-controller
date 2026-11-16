#!/usr/bin/env python3
"""camera/migrate_hook.sh and the SoC's SP805 watchdog (om_testlib.FakeSP805 behind OM_DEVMEM, no /dev/mem).

cmsMain.out is the only feeder of the Hi3518EV100 watchdog; unit 1 was reset ~44 s into a quiesced final. quiesce
must switch the watchdog off only after every process stopped and save its control word; resume and wdt-rearm
put it back as saved; every failure leaves the camera as it was (watchdog armed, processes running).
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import HOOK, FakeSP805, private_tmp, rmtree  # noqa: E402
import sb_fixture as fx  # noqa: E402
import build_stockboot as bs  # noqa: E402

BUSYBOX = fx.BUSYBOX
SLEEP = shutil.which("sleep")
KEY, LOCK, CTRL, INTCLR = 0x1ACCE551, 0xC00, 0x008, 0x00C
OFF = [[LOCK, KEY], [CTRL, 0], [LOCK, 0]]           # unlock, stop counter and reset, relock
REARM = [[LOCK, KEY], [INTCLR, 1], [CTRL, 3], [LOCK, 0]]  # unlock, reload, restore, relock


def proc_state(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


@unittest.skipUnless(BUSYBOX and SLEEP, "host BusyBox and sleep required")
class HookWatchdog(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-hookwdt")
        self.addCleanup(rmtree, self.tmp)
        self.om = self.tmp / "om"
        self.om.mkdir()
        self.wdt = FakeSP805(self.tmp)
        self.name = "omw" + secrets.token_hex(4)  # stands in for cmsMain.out
        exe = self.tmp / self.name
        shutil.copy2(SLEEP, exe)
        self.proc = subprocess.Popen([str(exe), "600"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.kill)
        self.devmem = self.wdt.script

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=30)

    def hook(self, *verb, rc=0, watchdog_dev=None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OM_BB": BUSYBOX, "OM_DIR": str(self.om),
               "OM_HOST": "127.0.0.1", "OM_PORT": "9", "OM_RUN_ID": "offline", "OM_QUIESCE_PROCS": self.name,
               "OM_WATCHDOG_DEV": str(watchdog_dev or self.tmp / "no-watchdog"), "OM_DEVMEM": str(self.devmem)}
        r = subprocess.run([BUSYBOX, "sh", str(HOOK), *verb], env=env, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout

    def saved(self):
        p = self.om / "wdt.saved"
        return p.read_text().strip() if p.exists() else None

    def writes(self) -> list:
        return self.wdt.get()["writes"]

    def assertArmedAndRunning(self):
        st = self.wdt.get()
        self.assertEqual((st["ctrl"], st["locked"]), (3, True), st)
        self.assertNotEqual(proc_state(self.proc.pid), "T")
        self.assertIsNone(self.saved())
        self.assertFalse((self.om / "quiesced.pids").exists())

    def test_quiesce_switches_the_watchdog_off_and_resume_rearms_it(self):
        out = self.hook("quiesce")
        self.assertIn("hook: watchdog off (ctrl was 3, counter frozen)", out)
        self.assertIn("hook: quiesced 1 process(es)", out)
        self.assertEqual(proc_state(self.proc.pid), "T")
        st = self.wdt.get()
        self.assertEqual((st["ctrl"], st["locked"], self.saved()), (0, True, "3"))
        self.assertEqual(self.writes(), OFF)
        line = self.hook("wdt-status").strip()
        w = bs.parse_wdt(line)
        self.assertIsNotNone(w, line)
        self.assertEqual((w["ctrl"], w["lock"], w["saved"], w["load"]), (0, 1, 3, st["load"]))
        self.assertTrue(bs.sp805(w))
        self.assertEqual(self.writes(), OFF)  # wdt-status only reads
        out = self.hook("resume")
        self.assertIn("hook: watchdog re-armed", out)
        self.assertArmedAndRunning()
        self.assertEqual(self.writes(), OFF + REARM)
        self.assertIn("saved=none", self.hook("wdt-status"))

    def test_rearm_before_a_reboot_keeps_the_processes_stopped(self):
        self.hook("quiesce", "all")
        self.assertIn("hook: watchdog re-armed", self.hook("wdt-rearm"))
        st = self.wdt.get()
        self.assertEqual((st["ctrl"], st["locked"], self.saved()), (3, True, None))
        self.assertEqual(proc_state(self.proc.pid), "T")  # final: the stock app never runs on the new flash
        self.assertEqual(self.hook("wdt-rearm"), "")      # nothing saved any more: nothing written
        self.hook("resume")
        self.assertArmedAndRunning()
        self.assertEqual(self.writes(), OFF + REARM)

    def test_a_second_quiesce_keeps_the_first_saved_state(self):
        self.hook("quiesce")
        self.assertIn("ctrl was 3", self.hook("quiesce"))
        self.assertEqual(self.saved(), "3")
        self.hook("resume")
        self.assertArmedAndRunning()

    def test_a_watchdog_that_was_off_stays_off(self):
        self.wdt.set(ctrl=0)
        self.assertIn("ctrl was 0", self.hook("quiesce"))
        self.assertEqual(self.saved(), "0")
        out = self.hook("resume")
        self.assertNotIn("re-armed", out)
        self.assertEqual((self.wdt.get()["ctrl"], self.saved()), (0, None))
        self.assertEqual(self.writes(), OFF)
        self.assertNotEqual(proc_state(self.proc.pid), "T")

    def test_a_watchdog_that_does_not_stop_undoes_the_quiesce(self):
        self.wdt.set(mode="stuck")
        out = self.hook("quiesce", "all", rc=1)
        self.assertIn("hook: the watchdog did not stop (ctrl 3)", out)
        self.assertIn("hook: quiesce undone (watchdog restored, processes continued)", out)
        self.assertNotIn("quiesced 1", out)
        self.assertArmedAndRunning()

    def test_an_unreadable_watchdog_undoes_the_quiesce(self):
        self.wdt.set(mode="noread")
        out = self.hook("quiesce", rc=1)
        self.assertIn("hook: cannot read the watchdog", out)
        self.assertIn("quiesce undone", out)
        self.assertEqual(self.writes(), [])
        self.assertNotEqual(proc_state(self.proc.pid), "T")
        self.assertIsNone(self.saved())
        self.assertEqual(self.hook("wdt-status", rc=1).strip(), "wdt unreadable")

    def test_garbage_from_devmem_is_not_a_register(self):
        for reply in ("garbage", "0x", "0x12G4", "0x123456789", ""):
            with self.subTest(reply=reply):
                self.devmem = self.tmp / "devmem-odd"
                self.devmem.write_text(f"#!{BUSYBOX} sh\necho '{reply}'\n")
                self.devmem.chmod(0o755)
                self.assertEqual(self.hook("wdt-status", rc=1).strip(), "wdt unreadable")
                self.assertIn("hook: cannot read the watchdog", self.hook("quiesce", rc=1))
                self.assertNotEqual(proc_state(self.proc.pid), "T")

    def test_a_failed_rearm_is_reported_and_the_saved_state_kept(self):
        self.hook("quiesce")
        self.wdt.set(mode="stuck")
        out = self.hook("resume", rc=1)
        self.assertIn("hook: the watchdog was not re-armed (ctrl 0)", out)
        self.assertNotEqual(proc_state(self.proc.pid), "T")  # the processes are continued anyway
        self.assertEqual(self.saved(), "3")
        self.wdt.set(mode="")
        self.assertIn("hook: watchdog re-armed", self.hook("wdt-rearm"))
        st = self.wdt.get()
        self.assertEqual((st["ctrl"], st["locked"], self.saved()), (3, True, None))

    def test_a_corrupt_saved_state_is_not_written(self):
        self.hook("quiesce")
        (self.om / "wdt.saved").write_text("3; reboot\n")
        self.assertIn("hook: the saved watchdog state is unreadable", self.hook("wdt-rearm", rc=1))
        self.assertEqual(self.writes(), OFF)
        self.hook("resume", rc=1)
        self.assertNotEqual(proc_state(self.proc.pid), "T")

    def test_a_watchdog_device_refuses_before_anything_is_touched(self):
        dev = self.tmp / "watchdog"
        dev.write_bytes(b"")
        self.assertIn("exists; refusing to stop the stock app", self.hook("quiesce", rc=1, watchdog_dev=dev))
        self.assertEqual(self.writes(), [])
        self.assertNotEqual(proc_state(self.proc.pid), "T")


if __name__ == "__main__":
    unittest.main()
