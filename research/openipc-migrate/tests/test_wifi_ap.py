#!/usr/bin/env python3
"""wifi/ap/wifi-ap (rescue access point on the reset button) and S97wifiap against stubs.

The script runs under host BusyBox ash with PATH limited to a few BusyBox applets and to one stub script, so
nothing on the host is touched. /sys, /proc, the GPIO and the pin mux registers are files in a temporary
directory: insmod, rmmod, modprobe and the Wi-Fi loader edit the fake module list and network devices, ip copies
the driver profile when ra0 goes up, devmem reads and writes files, logger appends to a log and sleep only counts.
The processes whose pid the script kills (udhcpc, udhcpd, the idle monitor) are real "busybox sleep" processes,
so a kill can only reach the test's own processes. The button watcher reads its GPIO value and the uptime from
FIFOs that the test fills sample by sample.
"""
from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "wifi" / "ap" / "wifi-ap"
S97 = HERE.parent / "wifi" / "ap" / "S97wifiap"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
APPLETS = ("awk", "cat", "cp", "grep", "mkdir", "mkfifo", "mv", "rm", "rmdir", "sed", "sh", "stat", "tr", "wc")
STUBS = ("logger", "fw_printenv", "fw_setenv", "xz", "insmod", "rmmod", "modprobe", "usb", "ip", "udhcpd", "devmem",
         "killall", "pidof", "ifup", "ifdown", "reboot", "sleep", "setsid")
NET = ("ifdown", "ifup", "killall", "pidof", "ip", "rmmod", "insmod", "modprobe", "usb", "xz", "udhcpd", "setsid",
       "reboot")
PSK = "home-passphrase"
NEW_PSK = "new-passphrase"
AP_PSK = "ap-passphrase-1"
WLANMAC = "02:00:00:11:22:33"
STA_MODULE = "mt7601sta 600000 0 - Live 0xbf100000"
SSID = "OpenIPC-BBCC"  # from the fake wlan0 address 02:00:00:aa:bb:cc
WPA = "WPA2-PSK, passphrase from wlanpass"
START_LOG = f'starting the access point "{SSID}" (channel 6, {WPA})'
UP_LOG = "the access point is up at 192.168.4.1"
STOP_LOG = ["stopping the access point", "back in Wi-Fi client mode", "wlan0 has an address"]
JP30 = "\u30ab\u30e1\u30e9\u6551\u6e08\u30cd\u30c3\u30c8\u30ef\u30fc"  # 10 Japanese characters = 30 bytes
JP33 = JP30 + "\u30af"  # 33 bytes
LEARN0 = [(0, 0), (0, 100)]  # button samples (level, 1/100 s) that make 0 the idle level

STUB = r"""
name=${0##*/}
if [ $# = 0 ]; then echo "$name"; else echo "$name $*"; fi >> "$D/calls"
mods=$D/proc/modules
net=$D/sys/class/net
flag() { [ -e "$D/flags/$1" ]; }
drop() { grep -v "^$1 " "$mods" > "$mods.new"; cat "$mods.new" > "$mods"; rm -f "$mods.new"; }
case $name in
logger)
	shift 2
	echo "$*" >> "$D/log" ;;
fw_printenv)
	[ -f "$D/env/$2" ] || { echo "## Error: \"$2\" not defined" >&2; exit 1; }
	cat "$D/env/$2"
	echo ;;
fw_setenv)
	flag setenv-fails && exit 1
	if [ $# = 1 ]; then rm -f "$D/env/$1"; else printf '%s' "$2" > "$D/env/$1"; fi ;;
xz)
	flag xz-fails && exit 1
	cat "$2" ;;
insmod)
	flag insmod-fails && exit 1
	[ -s "$1" ] || exit 1
	cp "$1" "$D/insmod-saw"
	echo "mt7601Uap 514732 0 - Live 0xbf000000" >> "$mods"
	flag no-ra0 || { mkdir -p "$net/ra0/statistics"; echo 0 > "$net/ra0/statistics/rx_packets"; } ;;
rmmod)
	flag "rmmod-$1-fails" && exit 1
	drop "$1"
	case $1 in mt7601Uap) rm -rf "$net/ra0" ;; mt7601sta) rm -rf "$net/wlan0" ;; esac ;;
usb|modprobe)
	flag sta-fails && exit 1
	[ "$name" = usb ] && flag usb-fails && exit 1
	grep -q "^mt7601sta " "$mods" || echo "mt7601sta 600000 0 - Live 0xbf100000" >> "$mods"
	mkdir -p "$net/wlan0"
	echo 02:00:00:aa:bb:cc > "$net/wlan0/address" ;;
ip)
	case "$*" in
	"link set ra0 up")
		cp "$WIFIAP_DAT" "$D/dat-at-up"
		stat -c %a "$WIFIAP_DAT" > "$D/dat-mode"
		flag ra0-up-fails && exit 1 ;;
	"-4 addr show dev wlan0")
		flag no-addr || echo "    inet 192.0.2.50/24 brd 192.0.2.255 scope global wlan0" ;;
	"-4 -o addr show dev wlan0")
		flag no-addr || echo "3: wlan0    inet 192.0.2.50/24 brd 192.0.2.255 scope global wlan0" ;;
	"neigh show dev ra0")
		echo "192.168.4.100 lladdr 02:00:00:00:00:01 REACHABLE"
		echo "192.168.4.101  FAILED" ;;
	esac ;;
udhcpd)
	[ "$1" = -f ] || exit 9
	flag udhcpd-fails && exit 1
	exec "$BB" sleep 30 ;;
devmem)
	f=$D/devmem/$1
	if flag probe; then
		# every access takes 20 ms; GPIO0 changes from 0x38 to 0x78 at its fifth read
		read -r up idle < "$D/proc/uptime"
		awk -v u="$up" -v i="$idle" 'BEGIN { printf "%.2f %s\n", u + 0.02, i }' > "$D/uptime.new"
		mv "$D/uptime.new" "$D/proc/uptime"
		if [ "$1" = 538182652 ]; then
			n=$(cat "$D/gpio0-reads" 2>/dev/null)
			n=$((${n:-0} + 1))
			echo $n > "$D/gpio0-reads"
			[ $n -gt 4 ] && echo 0x00000078 > "$f"
		fi
	fi
	if [ $# = 3 ]; then echo "$3" > "$f"; elif [ -f "$f" ]; then cat "$f"; else echo 0x00000000; fi ;;
pidof)
	flag wpa-stays || exit 1 ;;
sleep)
	[ "$1" = 1 ] && flag udhcpd-fails && "$BB" sleep 0.5
	if [ "$1" = 10 ] && flag rx-grows; then
		rx=$net/ra0/statistics/rx_packets
		echo $(($(cat "$rx") + 1)) > "$rx"
		[ "$(grep -c '^sleep 10$' "$D/calls")" -ge 5 ] && drop mt7601Uap
	fi ;;
setsid)
	exec "$BB" sleep 30 ;;
esac
exit 0
"""


def dat(ssid=SSID, ch=6, auth="WPA2PSK", enc="AES", psk=PSK) -> str:
    head = ["Default", "CountryRegion=1", "CountryRegionABand=7", "CountryCode=JP", "BssidNum=1", f"SSID1={ssid}",
            "WirelessMode=9", "TxRate=0", f"Channel={ch}"]
    middle = ("BasicRate=15 BeaconPeriod=100 DtimPeriod=1 TxPower=100 DisableOLBC=0 BGProtection=0 TxPreamble=0 "
              "RTSThreshold=2347 FragThreshold=2346 TxBurst=1 PktAggregate=0 WmmCapable=1 APSDCapable=0 DLSCapable=0 "
              "NoForwarding=0 HideSSID=0 ShortSlot=1 AutoChannelSelect=0 IEEE8021X=0 IEEE80211H=0 PreAuth=0").split()
    tail = ("RekeyMethod=DISABLE RekeyInterval=0 PMKCachePeriod=10 DefaultKeyID=2 HT_HTC=0 HT_RDG=0 HT_EXTCHA=0 "
            "HT_LinkAdapt=0 HT_OpMode=0 HT_MpduDensity=5 HT_BW=0 HT_AutoBA=1 HT_AMSDU=0 HT_BAWinSize=64 HT_GI=1 "
            "HT_MCS=33 HT_DisallowTKIP=1 MaxStaNum=4 RadioOn=1").split()
    return "\n".join(head + middle + [f"AuthMode={auth}", f"EncrypType={enc}", f"WPAPSK1={psk}"] + tail) + "\n"


def state(ssid=SSID, ch=6, sec=WPA, idle=600) -> str:
    return f"ssid={ssid}\naddress=192.168.4.1\nchannel={ch}\nsecurity={sec}\nidle={idle}\n"


def alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def wait_until(cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        time.sleep(0.01)
    return True


def kill_strays(d: Path):
    """SIGKILL what the scripts left running (the sleep stand-ins): every process with D=<d> in its environment."""
    marker = f"D={d}".encode()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            env = (p / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        if marker in env:
            try:
                os.kill(int(p.name), signal.SIGKILL)
            except OSError:
                pass


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class Fixture(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="wifi-ap-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.addCleanup(kill_strays, self.d)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        for applet in APPLETS:
            (self.bin / applet).symlink_to(BUSYBOX)
        for stub in STUBS:
            p = self.bin / stub
            p.write_text(f"#!{BUSYBOX} sh\n{STUB}")
            p.chmod(0o755)
        self.ko = self.d / "mt7601Uap.ko.xz"
        self.ko.write_bytes(b"fake mt7601Uap module\n")
        self.run_dir = self.d / "run" / "wifi-ap"
        self.lock = self.d / "run" / "wifi-ap.lock"
        self.dat = self.d / "tmp" / "mt7601ap" / "RT2870AP.dat"
        self.env = {"PATH": str(self.bin), "D": str(self.d), "BB": BUSYBOX, "WIFIAP_FG": "1",
                    "WIFIAP_KO": str(self.ko), "WIFIAP_RUN": str(self.run_dir), "WIFIAP_DAT": str(self.dat),
                    "WIFIAP_SYS": str(self.d / "sys"), "WIFIAP_PROC": str(self.d / "proc"),
                    "WIFIAP_USB": str(self.bin / "usb"), "WIFIAP_UDHCPC_PID": str(self.d / "run" / "udhcpc.wlan0.pid"),
                    "WIFIAP_BUTTON_PID": str(self.d / "run" / "wifi-ap-button.pid"), "WIFIAP_POLL": "0.01"}
        self.make_station()

    def make_station(self):
        """Wi-Fi client mode: mt7601sta loaded, wlan0 and eth0 present, the default U-Boot variables."""
        for sub in ("run", "flags", "env", "sys", "tmp", "proc", "devmem"):
            shutil.rmtree(self.d / sub, ignore_errors=True)
        for name in ("calls", "log", "dat-at-up", "dat-mode", "insmod-saw", "gpio0-reads"):
            (self.d / name).unlink(missing_ok=True)
        for sub in ("run", "flags", "env", "sys/class/net/wlan0", "sys/class/net/eth0", "sys/class/gpio", "devmem",
                    "proc/sys/net/ipv4/conf/wlan0"):
            (self.d / sub).mkdir(parents=True)
        (self.d / "proc/modules").write_text(STA_MODULE + "\n")
        (self.d / "proc/uptime").write_text("100.00 50.00\n")
        for name in ("arp_ignore", "arp_announce"):
            (self.d / "proc/sys/net/ipv4/conf/wlan0" / name).write_text("0\n")
        (self.d / "sys/class/net/wlan0/address").write_text("02:00:00:aa:bb:cc\n")
        (self.d / "sys/class/net/eth0/address").write_text("02:00:00:00:00:e0\n")
        (self.d / "sys/class/gpio/export").write_text("")
        (self.d / "devmem/0x200F0138").write_text("0x00000000\n")
        self.setenv(wlanssid="home-net", wlanpass=PSK, wlandev="mt7601u-test", wlanmac=WLANMAC)

    def setenv(self, **values):
        for name, value in values.items():
            p = self.d / "env" / name
            if value is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(value)

    def envdump(self) -> dict:
        return {p.name: p.read_text() for p in (self.d / "env").iterdir()}

    def flag(self, name: str):
        (self.d / "flags" / name).write_text("")

    def run_ap(self, *args, fg=True, extra=None):
        env = dict(self.env, **(extra or {}))
        if not fg:
            del env["WIFIAP_FG"]
        return subprocess.run([BUSYBOX, "sh", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                              timeout=60, stdin=subprocess.DEVNULL)

    def ok(self, *args, **kw) -> str:
        r = self.run_ap(*args, **kw)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stdout)
        return r.stdout

    def start_ap(self):
        """do-start, wait for the (stand-in) idle monitor, then forget the calls and the log"""
        self.ok("do-start")
        self.assertTrue(wait_until(lambda: self.calls("setsid")), "no idle monitor")
        self.reset()

    def reset(self):
        for name in ("calls", "log"):
            (self.d / name).unlink(missing_ok=True)

    def log(self) -> list:
        p = self.d / "log"
        return p.read_text().splitlines() if p.exists() else []

    def calls(self, *names) -> list:
        p = self.d / "calls"
        lines = p.read_text().splitlines() if p.exists() else []
        return [line for line in lines if line.split(" ", 1)[0] in names]

    def modules(self) -> list:
        return [line.split()[0] for line in (self.d / "proc/modules").read_text().splitlines() if line.strip()]

    def sleeper(self) -> subprocess.Popen:
        p = subprocess.Popen([BUSYBOX, "sleep", "30"], env=self.env, stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait(5)))
        return p

    def start_calls(self) -> list:
        return ["ifdown -f wlan0", "killall -q wpa_supplicant", "pidof wpa_supplicant", "ip link set wlan0 down",
                "rmmod mt7601sta", f"xz -dc {self.ko}", f"insmod {self.run_dir}/mt7601Uap.ko", "ip link set ra0 up",
                "ip addr add 192.168.4.1/24 dev ra0", f"udhcpd -f {self.run_dir}/udhcpd.conf",
                f"setsid sh {SCRIPT} do-monitor"]

    def stop_calls(self) -> list:
        return ["ip addr flush dev ra0", "ip link set ra0 down", "rmmod mt7601Uap", "usb mt7601u-test",
                f"ip link set dev wlan0 address {WLANMAC}", "ifup wlan0", "ip -4 addr show dev wlan0"]

    def assert_client_mode(self):
        self.assertEqual(self.modules(), ["mt7601sta"])
        self.assertTrue((self.d / "sys/class/net/wlan0").is_dir())
        self.assertFalse((self.d / "sys/class/net/ra0").exists())
        self.assertFalse(self.dat.exists())
        self.assertFalse(self.lock.exists())
        for name in ("state", "udhcpd.conf", "udhcpd.leases", "udhcpd.pid", "monitor.pid", "mt7601Uap.ko"):
            self.assertFalse((self.run_dir / name).exists(), name)


class StartStop(Fixture):
    def test_start_brings_up_a_wpa2_access_point_without_leaking_the_passphrase(self):
        udhcpc = self.sleeper()
        (self.d / "run/udhcpc.wlan0.pid").write_text(f"{udhcpc.pid}\n")
        out = self.ok("start")
        self.assertEqual(out, "Starting the access point; connections over wlan0 will drop.\n"
                              "Progress: logread | grep wifi-ap\n")
        self.assertEqual(udhcpc.wait(5), -signal.SIGTERM)
        self.assertTrue(wait_until(lambda: self.calls("setsid")))
        self.assertEqual(self.calls(*NET), self.start_calls())
        self.assertEqual(self.log(), [START_LOG, UP_LOG])
        # the driver saw the profile when ra0 went up; only root could read it, and it is gone now
        self.assertEqual((self.d / "dat-at-up").read_text(), dat())
        self.assertEqual((self.d / "dat-mode").read_text(), "600\n")
        self.assertFalse(self.dat.exists())
        self.assertFalse(self.dat.parent.exists())
        self.assertEqual((self.d / "insmod-saw").read_bytes(), self.ko.read_bytes())
        self.assertEqual((self.run_dir / "udhcpd.conf").read_text(), "\n".join([
            "start 192.168.4.100", "end 192.168.4.150", "interface ra0", "max_leases 8",
            f"lease_file {self.run_dir}/udhcpd.leases", "opt subnet 255.255.255.0", "opt router 192.168.4.1",
            "opt dns 192.168.4.1", "opt lease 3600"]) + "\n")
        self.assertEqual((self.run_dir / "state").read_text(), state())
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()),
                         ["monitor.pid", "state", "udhcpd.conf", "udhcpd.leases", "udhcpd.pid"])
        for name in ("udhcpd.pid", "monitor.pid"):
            self.assertTrue(alive(int((self.run_dir / name).read_text())), name)
        self.assertEqual(self.modules(), ["mt7601Uap"])
        self.assertTrue((self.d / "sys/class/net/ra0").is_dir())
        self.assertFalse((self.d / "sys/class/net/wlan0").exists())
        self.assertFalse(self.lock.exists())
        for text in (out, (self.d / "calls").read_text(), (self.d / "log").read_text(),
                     (self.run_dir / "state").read_text()):
            self.assertNotIn(PSK, text)

    def test_open_access_point(self):
        self.setenv(wlanap_pass="none")
        self.ok("do-start")
        self.assertEqual((self.d / "dat-at-up").read_text(), dat(auth="OPEN", enc="NONE", psk=""))
        self.assertEqual((self.run_dir / "state").read_text(), state(sec="open"))
        self.assertEqual(self.log()[0], f'starting the access point "{SSID}" (channel 6, open)')

    def test_own_settings_reach_the_driver(self):
        self.setenv(wlanap_ssid="Camera rescue ", wlanap_channel="011", wlanap_idle="0", wlanap_pass=AP_PSK)
        self.ok("do-start")
        self.assertEqual((self.d / "dat-at-up").read_text(), dat(ssid="Camera rescue ", ch=11, psk=AP_PSK))
        sec = "WPA2-PSK, passphrase from wlanap_pass"
        self.assertEqual((self.run_dir / "state").read_text(), state("Camera rescue ", 11, sec, 0))
        self.assertEqual(self.log(), [f'starting the access point "Camera rescue " (channel 11, {sec})', UP_LOG])
        # wlanap_idle=0: no idle monitor
        self.assertEqual(self.calls("setsid"), [])
        self.assertFalse((self.run_dir / "monitor.pid").exists())

    def test_start_twice(self):
        self.start_ap()
        self.ok("do-start")
        self.assertEqual(self.log(), ["the access point is already running"])
        self.assertEqual(self.calls(*NET), [])

    def test_stop_goes_back_to_client_mode(self):
        self.start_ap()
        udhcpd = int((self.run_dir / "udhcpd.pid").read_text())
        monitor = int((self.run_dir / "monitor.pid").read_text())
        out = self.ok("stop")
        self.assertEqual(out, "Going back to Wi-Fi client mode.\nProgress: logread | grep wifi-ap\n")
        self.assertTrue(wait_until(lambda: not alive(udhcpd) and not alive(monitor)))
        self.assertEqual(self.calls(*NET), self.stop_calls())
        self.assertEqual(self.log(), STOP_LOG)
        self.assert_client_mode()
        conf = self.d / "proc/sys/net/ipv4/conf/wlan0"
        self.assertEqual([(conf / n).read_text() for n in ("arp_ignore", "arp_announce")], ["1\n", "2\n"])

    def test_arp_settings_only_with_ethernet_in_use(self):
        for case in ("netmode=wifi", "no eth0"):
            with self.subTest(case):
                self.make_station()
                if case == "no eth0":
                    shutil.rmtree(self.d / "sys/class/net/eth0")
                else:
                    self.setenv(netmode="wifi")
                self.start_ap()
                self.ok("do-stop")
                self.assert_client_mode()
                conf = self.d / "proc/sys/net/ipv4/conf/wlan0"
                self.assertEqual([(conf / n).read_text() for n in ("arp_ignore", "arp_announce")], ["0\n", "0\n"])

    def test_stop_without_access_point(self):
        self.ok("do-stop")
        self.assertEqual(self.log(), ["the access point is not running"])
        self.assertEqual(self.calls(*NET), [])

    def test_toggle_switches_both_ways(self):
        out = self.ok("toggle")
        self.assertEqual(out, "Switching between the access point and Wi-Fi client mode.\n"
                              "Progress: logread | grep wifi-ap\n")
        self.assertEqual(self.modules(), ["mt7601Uap"])
        self.ok("toggle")
        self.assertEqual(self.log(), [START_LOG, UP_LOG] + STOP_LOG)
        self.assert_client_mode()

    def test_commands_run_detached(self):
        for cmd in ("start", "stop", "toggle"):
            with self.subTest(cmd):
                self.reset()
                r = self.run_ap(cmd, fg=False)
                self.assertEqual((r.returncode, r.stderr), (0, ""))
                self.assertTrue(wait_until(lambda: self.calls(*NET)))
                self.assertEqual(self.calls(*NET), [f"setsid sh {SCRIPT} do-{cmd}"])

    def test_usage(self):
        for args in ((), ("start", "now"), ("bogus",), ("join",), ("join", "a", "b", "c")):
            with self.subTest(args=args):
                r = self.run_ap(*args)
                self.assertEqual(r.returncode, 1)
                self.assertTrue(r.stderr.startswith("usage: wifi-ap start|stop|toggle|status|join"), r.stderr)
        self.assertEqual(self.calls(*NET, "fw_setenv"), [])


class Failures(Fixture):
    def revert(self, flag: str, messages: list) -> None:
        self.flag(flag)
        r = self.run_ap("do-start")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.log(), [START_LOG] + messages +
                         ["the access point failed, going back to Wi-Fi client mode", "back in Wi-Fi client mode"])
        self.assert_client_mode()
        self.assertEqual(self.calls("ifup"), ["ifup wlan0"])
        self.assertEqual(self.calls("setsid"), [])

    def test_unpack_failure(self):
        self.revert("xz-fails", [f"cannot unpack {self.ko}"])
        self.assertEqual(self.calls("insmod", "rmmod"), ["rmmod mt7601sta"])

    def test_insmod_failure(self):
        self.revert("insmod-fails", ["cannot load mt7601Uap"])
        self.assertEqual(self.calls("rmmod"), ["rmmod mt7601sta"])

    def test_no_ra0(self):
        self.revert("no-ra0", ["ra0 did not appear"])
        self.assertEqual(self.calls("sleep"), ["sleep 1"] * 10)
        self.assertEqual(self.calls("rmmod"), ["rmmod mt7601sta", "rmmod mt7601Uap"])

    def test_ra0_up_failure_still_deletes_the_profile(self):
        self.revert("ra0-up-fails", ["cannot bring ra0 up"])
        self.assertTrue((self.d / "dat-at-up").exists())
        self.assertFalse(self.dat.parent.exists())
        self.assertEqual(self.calls("rmmod"), ["rmmod mt7601sta", "rmmod mt7601Uap"])

    def test_udhcpd_failure(self):
        self.revert("udhcpd-fails", ["cannot start udhcpd"])
        self.assertEqual(self.calls("ip")[-3:], ["ip addr flush dev ra0", "ip link set ra0 down",
                                                f"ip link set dev wlan0 address {WLANMAC}"])

    def test_missing_module_file_leaves_wifi_alone(self):
        self.ko.unlink()
        r = self.run_ap("do-start")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.log(), [f"missing {self.ko}"])
        self.assertEqual(self.calls(*NET), [])

    def test_station_driver_that_does_not_unload(self):
        self.flag("rmmod-mt7601sta-fails")
        r = self.run_ap("do-start")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls("rmmod"), ["rmmod mt7601sta"] * 5)
        self.assertEqual(self.calls("insmod", "usb", "modprobe"), [])
        self.assertEqual(self.log(), [START_LOG, "cannot unload mt7601sta", "back in Wi-Fi client mode"])
        self.assertEqual(self.calls("ifup"), ["ifup wlan0"])
        self.assert_client_mode()

    def test_wpa_supplicant_that_does_not_quit_is_waited_for_5_s(self):
        self.flag("wpa-stays")
        self.ok("do-start")
        # 5 waiting for wpa_supplicant, 1 for udhcpd to settle
        self.assertEqual(self.calls("sleep"), ["sleep 1"] * (5 + 1))
        self.assertEqual(self.log(), [START_LOG, UP_LOG])

    def test_wlan0_that_does_not_come_back_reboots(self):
        self.start_ap()
        self.flag("sta-fails")
        r = self.run_ap("do-stop")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.log(), ["stopping the access point", "wlan0 did not come back, rebooting"])
        self.assertEqual(self.calls("usb", "modprobe", "reboot"), ["usb mt7601u-test", "modprobe mt7601sta", "reboot"])
        self.assertEqual(self.calls("sleep"), ["sleep 1"] * 15)
        self.assertEqual(self.calls("ifup"), [])
        self.assertFalse(self.lock.exists())

    def test_modprobe_when_the_wifi_loader_fails(self):
        self.start_ap()
        self.flag("usb-fails")
        self.ok("do-stop")
        self.assertEqual(self.calls("usb", "modprobe"), ["usb mt7601u-test", "modprobe mt7601sta"])
        self.assert_client_mode()


class Lock(Fixture):
    def hold(self, pid=None):
        self.lock.mkdir()
        if pid is not None:
            (self.lock / "pid").write_text(f"{pid}\n")

    def test_a_press_while_busy_is_ignored(self):
        self.hold(self.sleeper().pid)
        self.ok("do-press")
        self.assertEqual(self.log(), ["busy, ignoring the button"])
        self.assertEqual(self.calls(*NET, "sleep"), [])
        self.assertTrue(self.lock.is_dir())

    def test_a_command_waits_3_minutes_for_the_lock(self):
        self.hold(self.sleeper().pid)
        r = self.run_ap("do-start")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls("sleep"), ["sleep 1"] * 180)
        self.assertEqual(self.calls(*NET), [])

    def test_stale_locks_are_taken_over(self):
        dead = subprocess.Popen([BUSYBOX, "true"])
        dead.wait()
        for pid, cmd, sleeps in ((dead.pid, "do-press", 0), (None, "do-start", 3)):
            with self.subTest(pid=pid):
                self.make_station()
                self.hold(pid)
                self.ok(cmd)
                # and 1 for udhcpd to settle
                self.assertEqual(self.calls("sleep"), ["sleep 1"] * (sleeps + 1))
                self.assertEqual(self.log(), [START_LOG, UP_LOG])
                self.assertFalse(self.lock.exists())


class Join(Fixture):
    SAVED = ["Saved. The camera now joins that network; this connection will drop.",
             "If it gets no address within a minute, the access point comes back."]

    def test_bad_input_is_refused(self):
        ssid = "the SSID must have 1-32 bytes"
        psk = "the passphrase must have 8-63 characters (or 64 hex digits)"
        cases = ((("",), ssid), (("x" * 33,), ssid), ((JP33,), ssid), (("tab\there", PSK), ssid),
                 (("net", "short"), psk), (("net", "p" * 64), psk), (("net", "p" * 65), psk),
                 (("net", "pass\x01word"), psk))
        before = self.envdump()
        for args, message in cases:
            with self.subTest(args=args):
                r = self.run_ap("join", *args)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", f"wifi-ap: {message}\n"))
        self.assertEqual(self.calls("fw_setenv", *NET), [])
        self.assertEqual(self.envdump(), before)

    def test_join_from_the_access_point(self):
        self.start_ap()
        out = self.ok("join", "new-net", NEW_PSK)
        self.assertEqual(out.splitlines(), self.SAVED + ["The access point keeps its passphrase (saved as wlanap_pass)."])
        self.assertEqual(self.calls("fw_setenv"), [f"fw_setenv wlanap_pass {PSK}", f"fw_setenv wlanpass {NEW_PSK}",
                                                   "fw_setenv wlanssid new-net"])
        self.assertEqual(self.envdump(), {"wlanssid": "new-net", "wlanpass": NEW_PSK, "wlanap_pass": PSK,
                                          "wlandev": "mt7601u-test", "wlanmac": WLANMAC})
        self.assertEqual(self.log(), ["saved a new Wi-Fi network, switching to it"] + STOP_LOG)
        self.assert_client_mode()
        log = (self.d / "log").read_text()
        self.assertNotIn(PSK, log)
        self.assertNotIn(NEW_PSK, log)

    def test_no_address_on_the_new_network_brings_the_access_point_back(self):
        self.start_ap()
        self.flag("no-addr")
        self.ok("join", "new-net", NEW_PSK)
        self.assertEqual(self.log(), ["saved a new Wi-Fi network, switching to it", "stopping the access point",
                                      "back in Wi-Fi client mode",
                                      "wlan0 has no address yet; wpa_supplicant and udhcpc keep trying",
                                      "no address on the new network, starting the access point again",
                                      f'starting the access point "{SSID}" (channel 6, WPA2-PSK, passphrase from '
                                      'wlanap_pass)', UP_LOG])
        # 60 waiting for an address, 1 for udhcpd to settle
        self.assertEqual(self.calls("sleep"), ["sleep 1"] * (60 + 1))
        # same passphrase as before the join, although wlanpass has changed
        self.assertEqual((self.d / "dat-at-up").read_text(), dat())
        self.assertEqual(self.modules(), ["mt7601Uap"])

    def test_join_an_open_network_in_client_mode(self):
        out = self.ok("join", "cafe")
        self.assertEqual(out.splitlines(), self.SAVED + ["The access point keeps its passphrase (saved as wlanap_pass)."])
        self.assertEqual(self.calls("fw_setenv"), [f"fw_setenv wlanap_pass {PSK}", "fw_setenv wlanpass",
                                                   "fw_setenv wlanssid cafe"])
        self.assertNotIn("wlanpass", self.envdump())
        self.assertEqual(self.calls(*NET), ["ifdown -f wlan0", "killall -q wpa_supplicant", "pidof wpa_supplicant",
                                            "ifup wlan0", "ip -4 addr show dev wlan0"])
        self.assertEqual(self.log(), ["saved a new Wi-Fi network, switching to it", "wlan0 has an address"])

    def test_a_set_wlanap_pass_is_left_alone(self):
        self.setenv(wlanap_pass=AP_PSK)
        out = self.ok("join", "new-net", "0123456789abcdef" * 4)
        self.assertEqual(out.splitlines(), self.SAVED)
        self.assertEqual(self.calls("fw_setenv"), [f"fw_setenv wlanpass {'0123456789abcdef' * 4}",
                                                   "fw_setenv wlanssid new-net"])
        self.assertEqual(self.envdump()["wlanap_pass"], AP_PSK)

    def test_an_open_access_point_stays_open(self):
        self.setenv(wlanpass=None)
        out = self.ok("join", "new-net", NEW_PSK)
        self.assertEqual(out.splitlines(), self.SAVED + ["The access point stays open (saved as wlanap_pass=none)."])
        self.assertEqual(self.envdump()["wlanap_pass"], "none")

    def test_fw_setenv_failure_stops_the_join(self):
        self.flag("setenv-fails")
        r = self.run_ap("join", "new-net", NEW_PSK)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", "wifi-ap: fw_setenv wlanap_pass failed\n"))
        self.assertEqual(self.calls(*NET), [])
        self.assertEqual(self.envdump()["wlanpass"], PSK)


class Monitor(Fixture):
    def test_no_traffic_for_the_idle_time_goes_back(self):
        self.setenv(wlanap_idle="30")
        self.start_ap()
        self.assertIn("idle=30\n", (self.run_dir / "state").read_text())
        self.ok("do-monitor")
        self.assertEqual(self.calls("sleep"), ["sleep 10"] * 4)
        self.assertEqual(self.log(), ["no traffic for 30 s, going back to Wi-Fi client mode"] + STOP_LOG)
        self.assert_client_mode()

    def test_traffic_keeps_the_access_point(self):
        self.start_ap()
        self.flag("rx-grows")
        self.ok("do-monitor")  # ends when the module goes away at the fifth tick
        self.assertEqual(self.calls("sleep"), ["sleep 10"] * 5)
        self.assertEqual(self.log(), [])
        self.assertEqual((self.d / "sys/class/net/ra0/statistics/rx_packets").read_text(), "5\n")

    def test_idle_time_0_means_no_monitor(self):
        self.start_ap()
        (self.run_dir / "state").write_text(state(idle=0))
        self.ok("do-monitor")
        self.assertEqual(self.calls("sleep"), [])


class Status(Fixture):
    def conf(self, **extra) -> dict:
        lines = self.ok("status", extra=extra).splitlines()
        i = lines.index("access point when started:")
        return dict(line[2:].split("=", 1) for line in lines[i + 1:i + 6])

    def test_client_mode(self):
        self.assertEqual(self.ok("status"), "\n".join([
            "mode: Wi-Fi client", "  wlan0: 192.0.2.50/24", "access point when started:", f"  ssid={SSID}",
            "  address=192.168.4.1", "  channel=6", f"  security={WPA}", "  idle=600",
            "button: gpio6, watcher not running"]) + "\n")
        self.setenv(wlanap_gpio="off")
        self.assertTrue(self.ok("status").endswith("\nbutton: off\n"))

    def test_access_point_mode(self):
        self.start_ap()
        (self.d / "run/wifi-ap-button.pid").write_text(f"{self.sleeper().pid}\n")
        self.assertEqual(self.ok("status"), "\n".join([
            "mode: access point", f"  ssid={SSID}", "  address=192.168.4.1", "  channel=6", f"  security={WPA}",
            "  idle=600", "  clients seen: 1", "button: gpio6, watcher running"]) + "\n")

    def test_settings_are_checked(self):
        cases = (("wlanap_channel", "011", "channel", "11"), ("wlanap_channel", "13", "channel", "13"),
                 ("wlanap_channel", "0", "channel", "6"), ("wlanap_channel", "14", "channel", "6"),
                 ("wlanap_channel", "abc", "channel", "6"), ("wlanap_idle", "0", "idle", "0"),
                 ("wlanap_idle", "00030", "idle", "30"), ("wlanap_idle", "1000000000", "idle", "600"),
                 ("wlanap_idle", "-5", "idle", "600"), ("wlanap_ssid", "Camera rescue ", "ssid", "Camera rescue "),
                 ("wlanap_ssid", "x" * 32, "ssid", "x" * 32),
                 ("wlanap_ssid", JP30, "ssid", JP30),
                 ("wlanap_pass", "none", "security", "open"), ("wlanap_pass", "open", "security", "open"),
                 ("wlanap_pass", AP_PSK, "security", "WPA2-PSK, passphrase from wlanap_pass"),
                 ("wlanap_pass", "0123456789abcdef" * 4, "security", "WPA2-PSK, passphrase from wlanap_pass"),
                 ("wlanpass", None, "security", "open"), ("wlanpass", "1234567", "security", "open"),
                 ("wlanpass", "x" * 64, "security", "open"))
        for name, value, key, want in cases:
            with self.subTest(name=name, value=value):
                self.make_station()
                self.setenv(**{name: value})
                self.assertEqual(self.conf()[key], want)
                self.assertEqual(self.log(), [])
        for value in ("x" * 33, JP33, "tab\there"):
            with self.subTest(wlanap_ssid=value):
                self.make_station()
                self.setenv(wlanap_ssid=value)
                self.assertEqual(self.conf()["ssid"], SSID)
                self.assertEqual(self.log(), ["wlanap_ssid is not 1-32 bytes, using the default name"])
        with self.subTest("wlanap_pass too short"):
            self.make_station()
            self.setenv(wlanap_pass="short")
            self.assertEqual(self.conf()["security"], WPA)
            self.assertEqual(self.log(), ["wlanap_pass is not 8-63 characters, ignoring it"])
        with self.subTest("name from eth0, then none"):
            self.make_station()
            (self.d / "sys/class/net/wlan0/address").unlink()
            self.assertEqual(self.conf()["ssid"], "OpenIPC-00E0")
            (self.d / "sys/class/net/eth0/address").unlink()
            self.assertEqual(self.conf()["ssid"], "OpenIPC")
        with self.subTest("WIFIAP_* first"):
            self.make_station()
            self.setenv(wlanap_channel="11", wlanap_ssid="from-env")
            conf = self.conf(WIFIAP_CHANNEL="3", WIFIAP_SSID="override", WIFIAP_IDLE="5", WIFIAP_PASS="none")
            self.assertEqual(conf, {"ssid": "override", "address": "192.168.4.1", "channel": "3", "security": "open",
                                    "idle": "5"})


class Probe(Fixture):
    def test_probe_prints_the_pin_that_changes(self):
        self.flag("probe")
        (self.d / "devmem/538182652").write_text("0x00000038\n")  # GPIO0 data
        (self.d / "devmem/538182656").write_text("0x00000024\n")  # GPIO0 direction: bits 2 and 5 are outputs
        (self.d / "devmem/0x200F0138").write_text("0x00000001\n")
        out = self.ok("probe", "1")
        first = " ".join(f"GPIO{g}=0x{0x38 if g == 0 else 0:02x}" for g in range(12)) + " "
        self.assertEqual(out.splitlines(), [
            "Watching the GPIO inputs for 1 s: hold the button for 3 s, release it, repeat.", first,
            "0.9 s: GPIO0_6 = gpio6 (input): 0 -> 1", "1 change(s). Use the gpio number of the button as wlanap_gpio."])
        self.assertEqual(self.log(), ["pin mux 0x200F0138: 0x00000001 -> 0"])
        self.assertEqual((self.d / "devmem/0x200F0138").read_text(), "0\n")


class Button(Fixture):
    def setUp(self):
        super().setUp()
        self.gpio = self.d / "sys/class/gpio/gpio6"
        self.gpio.mkdir()
        (self.gpio / "direction").write_text("out\n")
        self.value = self.gpio / "value"
        os.mkfifo(self.value)
        self.uptime = self.d / "proc/uptime"
        self.uptime.unlink()
        os.mkfifo(self.uptime)

    def feed(self, proc, path: Path, text: str):
        """Write one line once the watcher opens the FIFO for reading."""
        end = time.monotonic() + 10
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                if e.errno != errno.ENXIO:
                    raise
            if proc.poll() is not None:
                self.fail(f"the button watcher exited with {proc.returncode}: {self.log()}")
            if time.monotonic() > end:
                self.fail(f"the button watcher does not read {path.name}: {self.log()}")
            time.sleep(0.001)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)

    def watch(self, samples) -> list:
        """Run the watcher over samples of (level, time in 1/100 s); returns the log once the last one is handled."""
        self.reset()
        with open(self.d / "button.out", "w") as out:
            proc = subprocess.Popen([BUSYBOX, "sh", str(SCRIPT), "button"], env=self.env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=out)
        try:
            self.feed(proc, self.value, f"{samples[0][0]}\n")  # the check before the loop
            self.assertTrue(wait_until(lambda: "watching the button on gpio6" in self.log()), self.log())
            for level, cs in samples:
                self.feed(proc, self.value, f"{level}\n")
                t = 10000 + cs
                self.feed(proc, self.uptime, f"{t // 100}.{t % 100:02d} 50.00\n")
            self.feed(proc, self.value, f"{samples[-1][0]}\n")  # the watcher is done with the last sample
        finally:
            proc.kill()
            proc.wait(5)
        self.assertEqual((self.d / "button.out").read_text(), "")
        self.assertEqual((self.d / "run/wifi-ap-button.pid").read_text(), f"{proc.pid}\n")
        self.assertEqual((self.gpio / "direction").read_text(), "in\n")
        self.assertEqual((self.d / "sys/class/gpio/export").read_text(), "")
        return self.log()

    def switched(self) -> bool:
        return wait_until(lambda: self.calls("setsid") == [f"setsid sh {SCRIPT} do-press"])

    def test_holding_the_button_for_3_s_switches(self):
        (self.d / "devmem/0x200F0138").write_text("0x00000001\n")  # the pin is not a GPIO yet
        hold = [(1, t) for t in range(200, 481, 20)]  # pressed from 2.0 s to 4.8 s, sampled every 0.2 s
        self.assertEqual(self.watch(LEARN0 + hold + [(0, 500), (0, 520)]), [
            "pin mux 0x200F0138: 0x00000001 -> 0", "watching the button on gpio6", "gpio6 idle level: 0",
            "button held for 3.0 s: switching"])
        self.assertTrue(self.switched())
        self.assertEqual(self.calls("devmem"), ["devmem 0x200F0138 32", "devmem 0x200F0138 32 0"])

    def test_hold_times(self):
        ignored = "ignored (hold it for 3-30 s)"
        cases = ((0, None), (80, f"1.0 s: {ignored}"), (259, f"2.7 s: {ignored}"), (260, "2.8 s: switching"),
                 (2980, "30.0 s: switching"), (2981, f"30.0 s: {ignored}"), (3080, f"31.0 s: {ignored}"))
        for span, held in cases:  # span: first to last pressed sample
            with self.subTest(span=span):
                log = self.watch(LEARN0 + [(1, 200), (1, 200 + span), (0, 220 + span), (0, 240 + span)])
                self.assertEqual(log[2:], [f"button held for {held}"] if held else [])
                if held and held.endswith("switching"):
                    self.assertTrue(self.switched())
                else:
                    self.assertEqual(self.calls("setsid"), [])

    def test_a_bounce_does_not_end_the_press(self):
        # the level changes at 0.5 s, so 0 becomes the idle level a second later
        samples = [(1, 0), (0, 50), (0, 150), (1, 200), (0, 300), (1, 320), (1, 500), (0, 520), (0, 540)]
        self.assertEqual(self.watch(samples), ["watching the button on gpio6", "gpio6 idle level: 0",
                                               "button held for 3.2 s: switching"])
        self.assertTrue(self.switched())

    def test_inverted_button(self):
        samples = [(1, 0), (1, 100), (0, 200), (0, 480), (1, 500), (1, 520)]
        self.assertEqual(self.watch(samples), ["watching the button on gpio6", "gpio6 idle level: 1",
                                               "button held for 3.0 s: switching"])
        self.assertTrue(self.switched())

    def test_a_level_that_stays_for_a_minute_becomes_the_idle_level(self):
        samples = LEARN0 + [(1, 200), (1, 6200), (1, 6201), (0, 6300), (0, 6600), (1, 6620), (1, 6640)]
        self.assertEqual(self.watch(samples), [
            "watching the button on gpio6", "gpio6 idle level: 0",
            "gpio6 stayed at 1 for a minute, taking it as the idle level", "button held for 3.2 s: switching"])
        self.assertTrue(self.switched())

    def test_a_bad_wlanap_gpio_means_gpio6(self):
        self.setenv(wlanap_gpio="GPIO0_6")
        self.assertEqual(self.watch(LEARN0), ["wlanap_gpio=GPIO0_6 is not a GPIO number, using 6",
                                              "watching the button on gpio6", "gpio6 idle level: 0"])

    def test_gpio74_is_exported_and_muxed(self):
        self.setenv(wlanap_gpio="74")
        r = self.run_ap("button")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.log(), ["pin mux 0x200F00D0: 0x00000000 -> 1", "cannot read gpio74"])
        self.assertEqual((self.d / "sys/class/gpio/export").read_text(), "74\n")
        self.assertEqual(self.calls("devmem"), ["devmem 0x200F00D0 32", "devmem 0x200F00D0 32 1"])

    def test_button_off(self):
        for value in ("off", "none", "no"):
            with self.subTest(value):
                self.reset()
                self.setenv(wlanap_gpio=value)
                r = self.run_ap("button")
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
                self.assertEqual(self.log(), [f"the button is off (wlanap_gpio={value})"])
        self.assertFalse((self.d / "run/wifi-ap-button.pid").exists())
        self.assertEqual(self.calls("devmem"), [])


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class InitScript(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="wifi-ap-s97-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.addCleanup(kill_strays, self.d)
        bin_ = self.d / "bin"
        bin_.mkdir()
        for applet in ("cat", "rm"):
            (bin_ / applet).symlink_to(BUSYBOX)
        self.daemon = self.d / "wifi-ap"
        self.daemon.write_text(f'#!{BUSYBOX} sh\necho "$*" > "$D/daemon-args"\nexec {BUSYBOX} sleep 30\n')
        self.daemon.chmod(0o755)
        self.pidfile = self.d / "wifi-ap-button.pid"
        text = S97.read_text()
        for old, new in (("DAEMON=/usr/sbin/wifi-ap\n", f"DAEMON={self.daemon}\n"),
                         ("PIDFILE=/var/run/wifi-ap-button.pid\n", f"PIDFILE={self.pidfile}\n")):
            self.assertEqual(text.count(old), 1)
            text = text.replace(old, new)
        self.script = self.d / "S97wifiap"
        self.script.write_text(text)
        self.env = {"PATH": str(bin_), "D": str(self.d)}

    def s97(self, arg: str) -> subprocess.CompletedProcess:
        return subprocess.run([BUSYBOX, "sh", str(self.script), arg], env=self.env, capture_output=True, text=True,
                              timeout=30, stdin=subprocess.DEVNULL)

    def watcher(self) -> int:
        pid = int(self.pidfile.read_text())
        self.assertTrue(alive(pid))
        self.assertTrue(wait_until(lambda: (self.d / "daemon-args").exists()))
        self.assertEqual((self.d / "daemon-args").read_text(), "button\n")
        status = Path(f"/proc/{pid}/status").read_text().splitlines()
        ignored = int(next(line for line in status if line.startswith("SigIgn:")).split()[1], 16)
        self.assertTrue(ignored & (1 << (signal.SIGHUP - 1)), "SIGHUP is not ignored")
        return pid

    def test_start_stop_restart(self):
        r = self.s97("start")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "Starting wifi-ap button watcher: OK\n", ""))
        first = self.watcher()
        self.assertEqual(self.s97("start").stdout, "wifi-ap button watcher is already running\n")
        self.assertEqual(int(self.pidfile.read_text()), first)
        (self.d / "daemon-args").unlink()
        r = self.s97("restart")
        self.assertEqual(r.stdout, "Stopping wifi-ap button watcher: OK\nStarting wifi-ap button watcher: OK\n")
        self.assertTrue(wait_until(lambda: not alive(first)))
        second = self.watcher()
        self.assertNotEqual(second, first)
        r = self.s97("stop")
        self.assertEqual((r.returncode, r.stdout), (0, "Stopping wifi-ap button watcher: OK\n"))
        self.assertTrue(wait_until(lambda: not alive(second)))
        self.assertFalse(self.pidfile.exists())

    def test_nothing_to_start_without_wifi_ap(self):
        self.daemon.chmod(0o644)
        r = self.s97("start")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(self.pidfile.exists())

    def test_usage(self):
        r = self.s97("bogus")
        self.assertEqual((r.returncode, r.stdout), (1, f"Usage: {self.script} {{start|stop|restart}}\n"))


if __name__ == "__main__":
    unittest.main()
