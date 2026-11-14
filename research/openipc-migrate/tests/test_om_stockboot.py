#!/usr/bin/env python3
"""om_stockboot.sh against a simulated 8 MiB flash (stock MTD files): every action, refusals, write
faults before and after the point of no return, and a resumed final. Offline only (--sim-dir)."""
from __future__ import annotations

import os
import shutil
import unittest

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sb_fixture import BUSYBOX, Camera, firmware, oenv, sha, sl, trial_image, uboot_saved  # noqa: E402
from om_testlib import private_tmp, rmtree  # noqa: E402

FW, OENV = firmware(), oenv()


@unittest.skipUnless(BUSYBOX, "needs a host BusyBox")
class StockbootActions(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-sb")
        self.cam = Camera(self.tmp)

    def tearDown(self):
        rmtree(self.tmp)

    def untouched_outside(self, lo: int, hi: int, before: bytes):
        after = self.cam.flash()
        self.assertEqual(after[:lo], before[:lo])
        self.assertEqual(after[hi:], before[hi:])

    def test_diagnose_and_collect_are_read_only(self):
        rc, out = self.cam.run("diagnose")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cam.flash(), self.cam.original)
        rc, out = self.cam.run("collect")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cam.flash(), self.cam.original)
        self.assertNotIn("dummy-ssid", out)

    def test_probe_arm_writes_only_the_vendor_env(self):
        probe, _ = sl.build_probe(self.cam.e0)
        self.cam.payload({"env-probe.img": probe})
        rc, out = self.cam.run("probe-arm", reboot=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("SIMULATION: reboot skipped", out)
        self.assertEqual(self.cam.senv(), probe)
        self.untouched_outside(sl.SENV_OFF, sl.SENV_OFF + sl.SENV_LEN, self.cam.original)
        self.assertEqual(self.cam.state(), "armed")

    def test_write_actions_need_the_phrase_and_the_planned_env(self):
        probe, _ = sl.build_probe(self.cam.e0)
        self.cam.payload({"env-probe.img": probe})
        rc, out = self.cam.run("probe-arm", write=False)
        self.assertEqual(rc, 1, out)
        rc, out = self.cam.run("probe-arm", env={"OM_WRITE_CONFIRM": "yes"}, extra=("--allow-write",), write=False)
        self.assertEqual(rc, 1, out)
        self.cam.payload({"env-probe.img": probe}, envlive=b"another env")
        rc, out = self.cam.run("probe-arm")
        self.assertEqual(rc, 1, out)
        self.assertIn("differs from the env the host planned", out)
        self.cam.payload({"env-probe.img": probe}, uboot=b"another boot loader")
        rc, out = self.cam.run("probe-arm")
        self.assertEqual(rc, 1, out)
        self.cam.payload({"env-probe.img": self.cam.e0})   # not a probe env
        rc, out = self.cam.run("probe-arm")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.cam.flash(), self.cam.original)

    def test_env_write_failure_restores_the_previous_env(self):
        probe, _ = sl.build_probe(self.cam.e0)
        self.cam.payload({"env-probe.img": probe})
        rc, out = self.cam.run("probe-arm", fault="bad-env-once")
        self.assertEqual(rc, 0, out)     # first attempt corrupted, rewrite verified
        self.cam.write_flash(self.cam.original)
        (self.cam.stage / "flash.state").unlink()
        rc, out = self.cam.run("probe-arm", fault="bad-env")
        self.assertEqual(rc, 2, out)     # the env block can never be verified
        self.assertIn("could not be restored", out)

    def test_trial_arm_then_restore(self):
        post = uboot_saved(sl.build_probe(self.cam.e0)[0])
        flash = bytearray(self.cam.original)
        flash[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] = post
        self.cam.write_flash(bytes(flash))
        armed, _ = sl.build_trial(post)
        timg = trial_image()
        self.cam.payload({"trial.img": timg, "env-armed.img": armed})
        rc, out = self.cam.run("trial-arm", fault="bad-part-5-once")
        self.assertEqual(rc, 0, out)
        after = self.cam.flash()
        self.assertEqual(after[sl.TRIAL_OFF:], timg)
        self.assertEqual(after[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN], armed)
        self.assertEqual(after[:sl.SENV_OFF], bytes(flash[:sl.SENV_OFF]))
        gap = slice(sl.SENV_OFF + sl.SENV_LEN, sl.TRIAL_OFF)
        self.assertEqual(after[gap], bytes(flash[gap]))
        self.cam.payload({"env-base.img": self.cam.e0})
        rc, out = self.cam.run("restore-env")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cam.senv(), self.cam.e0)
        self.assertEqual(self.cam.state(), "clean")
        rc, out = self.cam.run("restore-env")
        self.assertEqual(rc, 0, out)
        self.assertIn("nothing written", out)

    def test_remnants_behind_the_env_end_marker_are_ignored(self):
        """U-Boot's saveenv before the trial boot leaves the tail of the armed bootcmd (trial marker included)
        behind the end marker: a re-arm, a restore and final must only look at the live definitions."""
        saved = uboot_saved(sl.build_trial(uboot_saved(sl.build_probe(self.cam.e0)[0]))[0])
        live, tail = saved[4:].split(b"\0\0", 1)
        self.assertNotIn(sl.TRIAL_MARK.encode(), live)
        self.assertIn(sl.TRIAL_MARK.encode(), tail)
        flash = bytearray(self.cam.original)
        flash[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] = saved
        self.cam.write_flash(bytes(flash))
        self.cam.payload({"trial.img": trial_image(), "env-armed.img": sl.build_trial(saved)[0]})
        rc, out = self.cam.run("trial-arm")
        self.assertEqual(rc, 0, out)
        self.cam.payload({"env-base.img": saved})
        rc, out = self.cam.run("restore-env")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cam.senv(), saved)
        final, _ = sl.build_final(saved)
        self.cam.payload({"env-final.img": final, "firmware.bin": FW, "env-openipc.img": OENV})
        rc, out = self.cam.run("final", extra=("--firmware-sha256", sha(FW), "--oenv-sha256", sha(OENV)))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cam.senv(), final)

    def test_trial_image_failure_leaves_the_env_alone(self):
        armed, _ = sl.build_trial(self.cam.e0)
        self.cam.payload({"trial.img": trial_image(), "env-armed.img": armed})
        rc, out = self.cam.run("trial-arm", fault="bad-part-5")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.cam.senv(), self.cam.e0)
        self.assertEqual(self.cam.state(), "clean")

    def final_payload(self):
        final, _ = sl.build_final(self.cam.e0)
        self.cam.payload({"env-final.img": final, "firmware.bin": FW, "env-openipc.img": OENV})
        return final, ("--firmware-sha256", sha(FW), "--oenv-sha256", sha(OENV))

    def check_final_layout(self, final: bytes):
        after = self.cam.flash()
        self.assertEqual(after[:sl.UBOOT_LEN], self.cam.original[:sl.UBOOT_LEN])
        self.assertEqual(after[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN], final)
        self.assertEqual(after[sl.OIPC_ENV_OFF:sl.OIPC_ENV_OFF + sl.OIPC_ENV_LEN], OENV)
        self.assertEqual(after[sl.FW_OFF:sl.FW_OFF + sl.FW_LEN], FW)
        self.assertEqual(after[sl.OVERLAY_OFF:], b"\xff" * sl.OVERLAY_LEN)

    def test_final_clean_run_with_transient_faults(self):
        final, extra = self.final_payload()
        rc, out = self.cam.run("final", extra=extra, fault="transient")
        self.assertEqual(rc, 0, out)
        self.check_final_layout(final)
        self.assertEqual(self.cam.state(), "final")
        rc, out = self.cam.run("probe-arm")
        self.assertEqual(rc, 1, out)

    def test_final_pre_ponr_failure_keeps_stock(self):
        _, extra = self.final_payload()
        rc, out = self.cam.run("final", extra=extra, fault="bad-oenv")
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.cam.state(), "clean")
        after = self.cam.flash()
        self.assertEqual(after[:sl.OIPC_ENV_OFF], self.cam.original[:sl.OIPC_ENV_OFF])
        self.assertEqual(after[sl.TL_OFF:], self.cam.original[sl.TL_OFF:])

    def test_final_degraded_then_resumed(self):
        final, extra = self.final_payload()
        rc, out = self.cam.run("final", extra=extra, fault="bad-kernel")
        self.assertEqual(rc, 2, out)
        self.assertIn("unverified regions: kernel", out)
        self.assertEqual(self.cam.state(), "dirty")
        self.assertEqual(self.cam.senv(), final)            # the final env is always written last
        rc, out = self.cam.run("probe-arm")                  # dirty: only final/collect, still rc 2
        self.assertEqual(rc, 2, out)
        self.assertIn("only final (retry) or collect may run", out)
        rc, out = self.cam.run("final", extra=extra)
        self.assertEqual(rc, 0, out)
        self.assertIn("continuing an interrupted final", out)
        self.check_final_layout(final)

    def test_final_partition_fault_recovers_by_rewrite(self):
        final, extra = self.final_payload()
        rc, out = self.cam.run("final", extra=extra, fault="bad-part-2-once")
        self.assertEqual(rc, 0, out)
        self.check_final_layout(final)

    def test_final_needs_the_pinned_payloads(self):
        _, extra = self.final_payload()
        rc, out = self.cam.run("final", extra=("--firmware-sha256", "0" * 64, "--oenv-sha256", sha(OENV)))
        self.assertEqual(rc, 1, out)
        armed, _ = sl.build_trial(self.cam.e0)
        self.cam.payload({"env-final.img": armed, "firmware.bin": FW, "env-openipc.img": OENV})
        rc, out = self.cam.run("final", extra=extra)
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.cam.flash(), self.cam.original)


if __name__ == "__main__":
    unittest.main()
