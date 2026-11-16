#!/usr/bin/env python3
"""wifi/wifi-switch-trial.sh (non-persistent move of wlan0 to another network) against stub network tools.

The script runs under host BusyBox ash with PATH limited to a few BusyBox applets and to stubs, so nothing on the
host is touched: ip, iwconfig, wpa_supplicant, udhcpc, ping, killall, pidof, wlan_conf, fw_printenv, sync, reboot
and sleep are shell stubs that append their arguments to a call log. sleep returns at once; while the script waits
for the keep flag, the sleep stub creates the flag if the test asks for it. wpa_supplicant records the file it was
given, so ip can hand out a lease on one network and not on the other.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "wifi" / "wifi-switch-trial.sh"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
APPLETS = ("awk", "basename", "cat", "cut", "grep", "rm", "touch", "tr")
STUBS = ("ip", "iwconfig", "wpa_supplicant", "udhcpc", "ping", "killall", "pidof", "wlan_conf", "fw_printenv",
         "sync", "reboot", "sleep")
NEW_CONF = "network={ new }\n"

STUB = r"""
name=$(basename "$0")
echo "$name $*" >> "$D/calls"
case "$name" in
ip)
	case "$*" in
	route) echo "default via 192.0.2.1 dev wlan0  metric 10" ;;
	"-4 addr show wlan0" | "-4 -o addr show wlan0")
		if [ "$(cat "$D/active" 2>/dev/null)" = "$WIFI_TRIAL_NEW" ] && [ -e "$D/new-no-lease" ]; then exit 0; fi
		echo "3: wlan0    inet 192.0.2.50/24 brd 192.0.2.255 scope global wlan0" ;;
	esac ;;
iwconfig)
	echo "wlan0  IEEE 802.11bgn  Frequency:2.462 GHz  Access Point: 02:00:00:00:00:01"
	echo "          Bit Rate=65 Mb/s   Signal level=-49 dBm" ;;
wpa_supplicant) echo "$7" > "$D/active" ;;
ping) [ -e "$D/ping-fails" ] && exit 1 ;;
pidof) exit 1 ;;
fw_printenv) case "$2" in wlanssid) echo old-ssid ;; wlanpass) echo old-passphrase ;; esac ;;
wlan_conf) printf 'network={ %s %s }\n' "$1" "$2" ;;
sleep) [ "$1" = 5 ] && [ -e "$D/keep-on-wait" ] && touch "$WIFI_TRIAL_KEEP" ;;
esac
exit 0
"""


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class WifiSwitchTrial(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="wifi-trial-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        bin_ = self.d / "bin"
        bin_.mkdir()
        for applet in APPLETS:
            (bin_ / applet).symlink_to(BUSYBOX)
        for stub in STUBS:
            p = bin_ / stub
            p.write_text(f"#!{BUSYBOX} sh\n{STUB}")
            p.chmod(0o755)
        self.new = self.d / "wpa_new.conf"
        self.new.write_text(NEW_CONF)
        self.old = self.d / "wpa_old.conf"
        self.keep = self.d / "keep"

    def flag(self, name: str):
        (self.d / name).write_text("")

    def run_trial(self, wait: int = 10):
        env = {"PATH": str(self.d / "bin"), "D": str(self.d), "WIFI_TRIAL_LOG": str(self.d / "log"),
               "WIFI_TRIAL_NEW": str(self.new), "WIFI_TRIAL_KEEP": str(self.keep), "WIFI_TRIAL_OLD": str(self.old),
               "WIFI_TRIAL_PIDFILE": str(self.d / "udhcpc.wlan0.pid")}
        r = subprocess.run([BUSYBOX, "sh", str(SCRIPT), str(wait)], env=env, capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL)
        self.assertEqual((r.stdout, r.stderr), ("", ""))
        return r.returncode

    def log(self):
        # drop the uptime stamp; keep the lines the script wrote itself
        lines = (self.d / "log").read_text().splitlines()
        return [line.split(" ", 1)[1] for line in lines if line[:1].isdigit()]

    def calls(self, name: str):
        lines = (self.d / "calls").read_text().splitlines()
        return [line for line in lines if line.split(" ", 1)[0] == name]

    def supplicant_configs(self):
        return [line.rsplit(" ", 1)[1] for line in self.calls("wpa_supplicant")]

    def test_keep_flag_keeps_the_new_network(self):
        self.flag("keep-on-wait")
        self.assertEqual(self.run_trial(), 0)
        self.assertEqual(self.supplicant_configs(), [str(self.new)])
        self.assertEqual(self.calls("wlan_conf") + self.calls("fw_printenv") + self.calls("reboot"), [])
        log = self.log()
        self.assertEqual(log[:2], ["start wait=10", "joined: lease ok"])
        self.assertIn("addr: 192.0.2.50/24", log)
        self.assertIn("link: Frequency:2.462 Bit Rate=65 Signal level=-49 ", log)
        self.assertEqual(log[-2:], ["ping gw: ok", "keep flag seen at t=5; staying"])
        self.assertFalse(self.old.exists())

    def test_without_keep_flag_it_returns_to_the_env_network(self):
        self.assertEqual(self.run_trial(), 0)
        self.assertEqual(self.supplicant_configs(), [str(self.new), str(self.old)])
        self.assertEqual(self.old.read_text(), "network={ old-ssid old-passphrase }\n")
        self.assertEqual(self.calls("wlan_conf"), ["wlan_conf old-ssid old-passphrase"])
        self.assertEqual(self.calls("reboot"), [])
        log = self.log()
        i = log.index("no keep flag after 10s; reverting")
        self.assertEqual(log[i + 1], "reverted: lease ok")
        self.assertEqual(log[-1], "reverted: gw reachable")
        self.assertEqual(self.calls("udhcpc")[-1].split()[-2:], ["-i", "wlan0"])

    def test_no_lease_on_the_new_network_still_returns(self):
        self.flag("new-no-lease")
        self.assertEqual(self.run_trial(), 0)
        log = self.log()
        self.assertEqual(log[1:3], ["joined: no lease", "addr: "])
        self.assertIn("reverted: lease ok", log)
        self.assertEqual(log[-1], "reverted: gw reachable")
        self.assertEqual(self.calls("reboot"), [])

    def test_unreachable_old_gateway_reboots(self):
        self.flag("ping-fails")
        self.assertEqual(self.run_trial(wait=0), 0)
        self.assertEqual(self.log()[-1], "revert failed; rebooting")
        self.assertEqual(self.calls("sync"), ["sync "])
        self.assertEqual(self.calls("reboot"), ["reboot "])

    def test_missing_config_changes_nothing(self):
        self.new.unlink()
        self.assertEqual(self.run_trial(), 1)
        self.assertEqual(self.log(), [f"missing {self.new}"])
        self.assertEqual([c for c in (self.d / "calls").read_text().splitlines() if not c.startswith("sleep ")], [])

    def test_log_never_holds_the_passphrase(self):
        self.run_trial()
        text = (self.d / "log").read_text()
        self.assertNotIn("old-passphrase", text)
        self.assertNotIn("old-ssid", text)
        self.assertNotIn(NEW_CONF.strip(), text)


if __name__ == "__main__":
    unittest.main()
