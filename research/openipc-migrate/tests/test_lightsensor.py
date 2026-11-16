#!/usr/bin/env python3
"""night/S94lightsensor (points majestic's ADC readout at the light sensor) against a fake /dev.

The script runs under host BusyBox ash with PATH limited to BusyBox applets, so it can only use what the camera's
BusyBox offers. The fake /dev is a temporary directory: ADC_CH1 is a symlink to /dev/null (a character device, like
the node of the open_adc module), ADC_CH0 a regular file that stands in for the unconnected channel-0 node. logger
is a shell function that writes to a file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "night" / "S94lightsensor"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
APPLETS = ("mv", "ln", "rm", "readlink")
BOARD_CH0 = "channel 0 node\n"
LINKED = "ADC_CH0 now reads the light sensor (ADC_CH1)"

HARNESS = r"""
logger() { shift 2; echo "$*" >> "$D/log"; }
set -- "$ACTION"
. "$SCRIPT"
"""


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class LightSensor(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="lightsensor-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.dev = self.d / "dev"
        self.dev.mkdir()
        (self.d / "bin").mkdir()
        for applet in APPLETS:
            (self.d / "bin" / applet).symlink_to(BUSYBOX)
        (self.d / "log").write_text("")
        (self.d / "harness.sh").write_text(HARNESS)

    def board(self, *, ch0: bool = True, ch1: str | None = "chardev"):
        if ch0:
            (self.dev / "ADC_CH0").write_text(BOARD_CH0)
        if ch1 == "chardev":
            (self.dev / "ADC_CH1").symlink_to("/dev/null")
        elif ch1 == "file":
            (self.dev / "ADC_CH1").write_text("not a device\n")

    def run_script(self, action: str):
        env = {"PATH": str(self.d / "bin"), "D": str(self.d), "ACTION": action, "SCRIPT": str(SCRIPT),
               "LIGHTSENSOR_DEV": str(self.dev)}
        r = subprocess.run([BUSYBOX, "sh", str(self.d / "harness.sh")], env=env, capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stderr, "")
        return r.returncode, r.stdout

    def log(self):
        return (self.d / "log").read_text().splitlines()

    def entries(self):
        return sorted(p.name for p in self.dev.iterdir())

    def assert_linked(self):
        ch0 = self.dev / "ADC_CH0"
        self.assertTrue(ch0.is_symlink())
        self.assertEqual(os.readlink(ch0), "ADC_CH1")  # relative, like the node names devtmpfs creates

    def assert_untouched(self):
        self.assertEqual(self.entries(), ["ADC_CH0", "ADC_CH1"])
        self.assertFalse((self.dev / "ADC_CH0").is_symlink())
        self.assertEqual((self.dev / "ADC_CH0").read_text(), BOARD_CH0)

    def test_start_points_ch0_at_the_sensor(self):
        self.board()
        self.assertEqual(self.run_script("start")[0], 0)
        self.assert_linked()
        self.assertEqual(self.entries(), ["ADC_CH0", "ADC_CH0.orig", "ADC_CH1"])
        self.assertEqual((self.dev / "ADC_CH0.orig").read_text(), BOARD_CH0)
        self.assertEqual(self.log(), [LINKED])

    def test_second_start_changes_nothing(self):
        self.board()
        self.run_script("start")
        rc, out = self.run_script("start")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ADC_CH0 already points to ADC_CH1\n")
        self.assert_linked()
        self.assertEqual((self.dev / "ADC_CH0.orig").read_text(), BOARD_CH0)
        self.assertEqual(self.log(), [LINKED])

    def test_missing_sensor_leaves_ch0_alone(self):
        self.board(ch1=None)
        self.assertEqual(self.run_script("start")[0], 1)
        self.assertEqual(self.entries(), ["ADC_CH0"])
        self.assertEqual((self.dev / "ADC_CH0").read_text(), BOARD_CH0)
        self.assertEqual(self.log(), ["ADC_CH1 not found (open_adc not loaded?); ADC_CH0 left as it is"])

    def test_sensor_must_be_a_character_device(self):
        self.board(ch1="file")
        self.assertEqual(self.run_script("start")[0], 1)
        self.assert_untouched()

    def test_stop_restores_the_channel0_node(self):
        self.board()
        self.run_script("start")
        self.assertEqual(self.run_script("stop")[0], 0)
        self.assert_untouched()

    def test_stop_without_start_changes_nothing(self):
        self.board()
        self.assertEqual(self.run_script("stop")[0], 0)
        self.assert_untouched()
        self.assertEqual(self.log(), [])

    def test_without_channel0_node(self):
        self.board(ch0=False)
        self.assertEqual(self.run_script("start")[0], 0)
        self.assert_linked()
        self.assertEqual(self.entries(), ["ADC_CH0", "ADC_CH1"])
        self.run_script("stop")
        self.assertEqual(self.entries(), ["ADC_CH1"])

    def test_restart_keeps_the_channel0_node(self):
        self.board()
        self.run_script("start")
        self.assertEqual(self.run_script("restart")[0], 0)
        self.assert_linked()
        self.assertEqual((self.dev / "ADC_CH0.orig").read_text(), BOARD_CH0)
        self.assertEqual(self.log(), [LINKED, LINKED])

    def test_unknown_action(self):
        self.board()
        rc, out = self.run_script("status")
        self.assertEqual(rc, 1)
        self.assertTrue(out.startswith("Usage: "), out)
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
