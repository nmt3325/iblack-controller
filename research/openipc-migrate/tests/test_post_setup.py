#!/usr/bin/env python3
"""easy/post_setup.py: the generated camera script, run under host BusyBox against a fake camera root.

The root is a temporary directory (POST_SETUP_ROOT) with proc/mtd, proc/modules, rom/usr/sbin/sysupgrade,
rom/etc/init.d/S95majestic and an /etc like the camera's after a factory reset. PATH holds only BusyBox applets and stubs: fw_printenv/fw_setenv
work on a VAR=VALUE file, cli edits two-level YAML like the camera's yaml-cli (-i/-o/-g/-s, rc 1 for a missing
key; checked against the real one on unit 2), modprobe adds a line to proc/modules, reboot and sync only log.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE / "easy"))
import post_setup  # noqa: E402

BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
APPLETS = ("awk", "base64", "cat", "chmod", "cmp", "cp", "cut", "df", "grep", "head", "md5sum", "mkdir", "mktemp",
           "mv", "od", "rm", "sed", "sh", "tail", "tr", "wc")
FIXED_MTD = ('dev:    size   erasesize  name\nmtd0: 00060000 00010000 "boot"\nmtd1: 00010000 00010000 "env"\n'
             'mtd2: 001d0000 00010000 "kernel"\nmtd3: 004e0000 00010000 "rootfs"\n'
             'mtd4: 000e0000 00010000 "rootfs_data"\n')
STOCK_MTD = ('dev:    size   erasesize  name\nmtd0: 00200000 00010000 "boot"\nmtd1: 000c0000 00010000 "rootfs"\n'
             'mtd2: 00280000 00010000 "appfs"\n')
ROM_UPDATER = "#!/bin/sh\nscr_version=1.0.84\necho real updater\n"
ROM_S95 = "#!/bin/sh\necho firmware majestic \"$@\"\n"

STUBS = {
    "fw_printenv": r'''
[ -f "$PS_ENV" ] || exit 1
if [ "$1" = -n ]; then
	grep -q "^$2=" "$PS_ENV" || { echo "## Error: \"$2\" not defined" >&2; exit 1; }
	sed -n "s/^$2=//p" "$PS_ENV"
	exit 0
fi
cat "$PS_ENV"
''',
    "fw_setenv": r'''
echo "fw_setenv $*" >> "$PS_LOG"
grep -v "^$1=" "$PS_ENV" > "$PS_ENV.new"
[ $# -lt 2 ] || echo "$1=$2" >> "$PS_ENV.new"
mv "$PS_ENV.new" "$PS_ENV"
''',
    "cli": r'''
in= out= mode= key= val=
while [ $# -gt 0 ]; do
	case $1 in
	-i) in=$2; shift 2 ;;
	-o) out=$2; shift 2 ;;
	-g) mode=get key=$2; shift 2 ;;
	-s) mode=set key=$2 val=$3; shift 3 ;;
	*) exit 9 ;;
	esac
done
[ -f "$in" ] || { echo "Failed to open input file $in" >&2; exit 1; }
k=${key#.} sec=${k%%.*} name=${k#*.}
if [ $mode = get ]; then
	awk -v s="$sec" -v n="$name" '
		/^[^ #]/ { cur = $0; sub(/:.*/, "", cur) }
		/^  [^ ]/ && cur == s { l = $0; sub(/^  /, "", l); split(l, a, ": "); if (a[1] == n) { print a[2]; f = 1 } }
		END { exit !f }' "$in"
	exit
fi
echo "cli set $key $val" >> "$PS_LOG"
awk -v s="$sec" -v n="$name" -v v="$val" '
	function flush() { if (ins && !done) { print "  " n ": " v; done = 1 } }
	/^[^ #]/ { flush(); cur = $0; sub(/:.*/, "", cur); ins = (cur == s) }
	ins && /^  [^ ]/ { l = $0; sub(/^  /, "", l); split(l, a, ": "); if (a[1] == n) { print "  " n ": " v; done = 1; next } }
	{ print }
	END { flush(); if (!done) { print s ":"; print "  " n ": " v } }' "$in" > "$in.cli" && mv "$in.cli" "${out:-$in}"
''',
    "modprobe": r'''
echo "modprobe $*" >> "$PS_LOG"
echo "$1 16384 0 - Live 0xbf000000" >> "$POST_SETUP_ROOT/proc/modules"
''',
    "reboot": 'echo reboot >> "$PS_LOG"\n',
    "sync": 'echo sync >> "$PS_LOG"\n',
    "logger": ":\n",
}


def tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (p.stat().st_mode & 0o777, hashlib.md5(p.read_bytes()).hexdigest())
    return out


@unittest.skipUnless(BUSYBOX, "host BusyBox required")
class PostSetup(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="post-setup-"))
        self.addCleanup(shutil.rmtree, self.d, True)
        b = self.d / "bin"
        b.mkdir()
        for applet in APPLETS:
            (b / applet).symlink_to(BUSYBOX)
        for name, body in STUBS.items():
            (b / name).write_text(f"#!{BUSYBOX} sh\n{body}")
            (b / name).chmod(0o755)
        (self.d / "tmp").mkdir()
        self.root = r = self.d / "root"
        for sub in ("proc", "rom/usr/sbin", "rom/etc/init.d", "etc/init.d", "etc/sensors", "usr/sbin", "usr/lib"):
            (r / sub).mkdir(parents=True)
        (r / "proc/mtd").write_text(FIXED_MTD)
        (r / "proc/modules").write_text("open_adc 2364 0 - Live 0xbf0f6000\nmt7601sta 728316 1 - Live 0xbf000000\n")
        for p in (r / "rom/usr/sbin/sysupgrade", r / "usr/sbin/sysupgrade"):  # overlayfs shows the rom file
            p.write_text(ROM_UPDATER)
            p.chmod(0o755)
        for p in (r / "rom/etc/init.d/S95majestic", r / "etc/init.d/S95majestic"):
            p.write_text(ROM_S95)
            p.chmod(0o755)
        (r / "etc/modules").write_text("vfat\nexfat\n")
        (r / "etc/sensors/imx222_i2c_1080p.ini").write_text("[sensor]\n")
        self.env = self.d / "env"
        self.env.write_text("bootdelay=1\nwlanssid=example\n")
        self.log = self.d / "log"
        self.log.write_text("")

    def run_script(self, mode="apply", reboot=False, script=None):
        if script is None:
            script = post_setup.build_script(mode, reboot)
        f = self.d / "post_setup.sh"
        f.write_text(script)
        env = {"PATH": str(self.d / "bin"), "POST_SETUP_ROOT": str(self.root), "TMPDIR": str(self.d / "tmp"),
               "PS_ENV": str(self.env), "PS_LOG": str(self.log)}
        r = subprocess.run([BUSYBOX, "sh", str(f)], env=env, capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.stderr, "")
        self.assertEqual(list((self.d / "tmp").iterdir()), [], "temporary directory left behind")
        return r.returncode, r.stdout.splitlines()

    def envvars(self) -> dict:
        return dict(line.split("=", 1) for line in self.env.read_text().splitlines())

    def states(self, lines) -> dict:
        out = {}
        for line in lines:
            for state in ("changed", "already", "would change", "note", "failed"):
                prefix = f"post_setup: {state}: "
                if line.startswith(prefix):
                    out.setdefault(state, []).append(line[len(prefix):])
        return out

    def test_fresh_root_then_nothing_to_do(self):
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        st = self.states(lines)
        self.assertEqual(set(st), {"changed"}, lines)
        self.assertEqual(len(st["changed"]), 11, lines)
        self.assertEqual(lines[-1], "post_setup: result: 11 changed, reboot the camera to use them")
        r = self.root
        for rel, src in (("usr/sbin/sysupgrade", "update/sysupgrade-guard.sh"),
                         ("etc/sensors/ar0141_i2c_720p.ini", "video/ar0141_i2c_720p.ini"),
                         ("etc/majestic.yaml", "night/majestic.yaml"),
                         ("etc/init.d/S94lightsensor", "night/S94lightsensor"),
                         ("etc/init.d/S96vicheck", "video/S96vicheck"),
                         ("usr/lib/libmajaudiofix.so", "audio/libmajaudiofix.so"),
                         ("etc/init.d/S95majestic", "audio/S95majestic")):
            self.assertEqual((r / rel).read_bytes(), (BASE / src).read_bytes(), rel)
        for rel in ("usr/sbin/sysupgrade", "etc/init.d/S94lightsensor", "etc/init.d/S96vicheck",
                    "etc/init.d/S95majestic"):
            self.assertEqual((r / rel).stat().st_mode & 0o777, 0o755, rel)
        self.assertEqual((r / "etc/majestic.yaml").stat().st_mode & 0o777, 0o600)
        self.assertEqual((r / "usr/lib/libmajaudiofix.so").stat().st_mode & 0o777, 0o644)
        self.assertEqual((r / "rom/usr/sbin/sysupgrade").read_text(), ROM_UPDATER)
        self.assertEqual((r / "rom/etc/init.d/S95majestic").read_text(), ROM_S95)
        self.assertEqual((r / "etc/modules").read_text(), "vfat\nexfat\nwdt\n")
        self.assertIn("wdt 16384", (r / "proc/modules").read_text())
        env = self.envvars()
        self.assertEqual(env["sensor"], post_setup.DEFAULT_SENSOR)
        self.assertEqual(env["upgrade"], post_setup.NO_ONLINE_UPDATE_URL)
        self.assertEqual(env["wlanssid"], "example")
        calls = self.log.read_text().splitlines()
        self.assertEqual(sorted(c for c in calls if c != "sync"),
                         sorted(["fw_setenv upgrade " + post_setup.NO_ONLINE_UPDATE_URL, "modprobe wdt",
                                 "fw_setenv sensor " + post_setup.DEFAULT_SENSOR]))
        self.assertNotIn("reboot", calls)
        self.assertFalse(list(r.rglob("*.post_setup")))

        before = tree(r)
        self.log.write_text("")
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        st = self.states(lines)
        self.assertEqual(set(st), {"already"}, lines)
        self.assertEqual(len(st["already"]), 11)
        self.assertEqual(lines[-1], "post_setup: result: nothing to change")
        self.assertEqual(tree(r), before)
        self.assertEqual(self.log.read_text(), "")
        rc, lines = self.run_script("check")
        self.assertEqual((rc, lines[-1]), (0, "post_setup: result: nothing to change"))

    def test_check_changes_nothing(self):
        before, env = tree(self.root), self.env.read_text()
        rc, lines = self.run_script("check")
        self.assertEqual(rc, 1, lines)
        st = self.states(lines)
        self.assertEqual(set(st), {"would change"}, lines)
        self.assertEqual(len(st["would change"]), 11)
        self.assertEqual(lines[-1], "post_setup: result: 11 would change, then a reboot")
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.env.read_text(), env)
        self.assertEqual(self.log.read_text(), "")

    def test_existing_majestic_yaml_keeps_owner_values(self):
        y = self.root / "etc/majestic.yaml"
        y.write_text("isp:\n  blkCnt: 4\nvideo0:\n  fps: 20\nnightMode:\n  minThreshold: 80\n  irCutPin1: 2\n"
                     "audio:\n  outputEnabled: true\n  outputVolume: 70\n")
        y.chmod(0o600)
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        text = y.read_text()
        for want in ("  blkCnt: 9", "  fps: 20", "  minThreshold: 80", "  maxThreshold: 150", "  irCutPin1: 2",
                     "  lightMonitor: true", "  irCutSingleInvert: true", "  backlightPin: 52", "  adcReadout: true",
                     "  adcInvert: true", "  outputEnabled: true", "  outputVolume: 70", "  speakerPin: 63"):
            self.assertIn(want + "\n", text)
        self.assertNotIn("minThreshold: 100", text)
        line = [x for x in lines if "/etc/majestic.yaml" in x][0]
        self.assertEqual(line, "post_setup: changed: /etc/majestic.yaml: isp.blkCnt=9 nightMode.lightMonitor=true "
                               "nightMode.irCutSingleInvert=true nightMode.backlightPin=52 "
                               "nightMode.adcReadout=true nightMode.adcInvert=true audio.speakerPin=63 "
                               "nightMode.maxThreshold=150")
        self.assertEqual(y.stat().st_mode & 0o777, 0o600)
        rc, lines = self.run_script("check")
        self.assertEqual(rc, 0, lines)

    def test_wrong_layout_changes_nothing(self):
        (self.root / "proc/mtd").write_text(STOCK_MTD)
        before, env = tree(self.root), self.env.read_text()
        for mode in ("apply", "check"):
            rc, lines = self.run_script(mode)
            self.assertEqual(rc, 2, lines)
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith("post_setup: error: /proc/mtd does not show the fixed slots"))
            self.assertTrue(lines[0].endswith("nothing was changed"))
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.env.read_text(), env)
        self.assertEqual(self.log.read_text(), "")

    def test_missing_rom_updater_or_env_changes_nothing(self):
        (self.root / "rom/usr/sbin/sysupgrade").unlink()
        before = tree(self.root)
        rc, lines = self.run_script()
        self.assertEqual((rc, lines), (2, ["post_setup: error: /rom/usr/sbin/sysupgrade is missing; nothing was changed"]))
        (self.root / "rom/usr/sbin/sysupgrade").write_text(ROM_UPDATER)
        self.env.unlink()
        before = tree(self.root)
        rc, lines = self.run_script()
        self.assertEqual((rc, lines), (2, ["post_setup: error: fw_printenv cannot read the U-Boot env; nothing was changed"]))
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.log.read_text(), "")

    def test_damaged_payload_changes_nothing(self):
        script = post_setup.build_script()
        md5 = hashlib.md5((BASE / "night/S94lightsensor").read_bytes()).hexdigest()
        self.assertEqual(script.count(md5), 1)
        before = tree(self.root)
        rc, lines = self.run_script(script=script.replace(md5, "0" * 32))
        self.assertEqual((rc, lines), (2, ["post_setup: error: s94 arrived damaged; nothing was changed"]))
        self.assertEqual(tree(self.root), before)
        self.assertEqual(self.log.read_text(), "")

    def test_modules_without_final_newline_and_other_env_values(self):
        (self.root / "etc/modules").write_text("vfat\nexfat")
        self.env.write_text("sensor=imx222\nupgrade=https://example.invalid/own.tgz\n")
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        self.assertEqual((self.root / "etc/modules").read_text(), "vfat\nexfat\nwdt\n")
        st = self.states(lines)
        self.assertEqual(st["note"], ["env upgrade is 'https://example.invalid/own.tgz', not '"
                                      + post_setup.NO_ONLINE_UPDATE_URL + "'; left as is",
                                      "env sensor is 'imx222', not 'ar0141'; left as is"])
        self.assertEqual(self.envvars(), {"sensor": "imx222", "upgrade": "https://example.invalid/own.tgz"})
        self.assertNotIn("fw_setenv", self.log.read_text())

    def test_wdt_line_with_arguments_counts(self):
        (self.root / "etc/modules").write_text("vfat\nwdt nodeamon=0\n")
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        self.assertIn("post_setup: already: /etc/modules: wdt", lines)
        self.assertEqual((self.root / "etc/modules").read_text(), "vfat\nwdt nodeamon=0\n")

    def test_same_content_wrong_mode_is_fixed(self):
        s96 = self.root / "etc/init.d/S96vicheck"
        s96.write_bytes((BASE / "video/S96vicheck").read_bytes())
        s96.chmod(0o644)
        rc, lines = self.run_script()
        self.assertIn("post_setup: changed: /etc/init.d/S96vicheck (made executable)", lines)
        self.assertEqual(s96.stat().st_mode & 0o777, 0o755)

    def test_microphone_fix_only_over_the_firmware_s95majestic(self):
        s95 = self.root / "etc/init.d/S95majestic"
        own = "#!/bin/sh\n# my own start of majestic\nexec /usr/bin/majestic\n"
        s95.write_text(own)
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        st = self.states(lines)
        self.assertEqual(st["note"], ["/etc/init.d/S95majestic is not the firmware's; left as is (no audio fix)"])
        self.assertEqual(len(st["changed"]), 9, lines)
        self.assertEqual(s95.read_text(), own)
        self.assertFalse((self.root / "usr/lib/libmajaudiofix.so").exists())

    def test_older_microphone_fix_is_replaced(self):
        s95 = self.root / "etc/init.d/S95majestic"
        s95.write_text("#!/bin/sh\nLD_PRELOAD=/usr/lib/libmajaudiofix.so exec sh /rom/etc/init.d/S95majestic \"$@\"\n")
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        self.assertIn("post_setup: changed: /etc/init.d/S95majestic (audio fix)", lines)
        self.assertEqual(s95.read_bytes(), (BASE / "audio/S95majestic").read_bytes())
        self.assertEqual(s95.stat().st_mode & 0o777, 0o755)

    def test_missing_s95majestic_gets_no_microphone_fix(self):
        note = "/etc/init.d/S95majestic or the firmware's copy in /rom is missing; left as is (no audio fix)"
        s95, rom = self.root / "etc/init.d/S95majestic", self.root / "rom/etc/init.d/S95majestic"
        for gone in (s95, rom):
            with self.subTest(gone=str(gone.relative_to(self.root))):
                for p in (s95, rom):
                    p.write_text(ROM_S95)
                gone.unlink()
                rc, lines = self.run_script()
                self.assertEqual(rc, 0, lines)
                self.assertIn("post_setup: note: " + note, lines)
                self.assertFalse(gone.exists())
                self.assertFalse((self.root / "usr/lib/libmajaudiofix.so").exists())

    def test_reboot_only_when_needed(self):
        rc, lines = self.run_script(reboot=True)
        self.assertEqual((rc, lines[-1]), (0, "post_setup: result: 11 changed, rebooting now"))
        self.assertEqual(self.log.read_text().splitlines()[-1], "reboot")
        self.log.write_text("")
        rc, lines = self.run_script(reboot=True)
        self.assertEqual((rc, lines[-1]), (0, "post_setup: result: nothing to change"))
        self.assertEqual(self.log.read_text(), "")
        (self.root / "usr/sbin/sysupgrade").write_text(ROM_UPDATER)  # a guard-only change needs no reboot
        rc, lines = self.run_script(reboot=True)
        self.assertEqual((rc, lines[-1]), (0, "post_setup: result: 1 changed, no reboot needed"))
        self.assertNotIn("reboot", self.log.read_text())

    def test_installed_guard_status_is_checked(self):
        (self.root / "rom/usr/sbin/sysupgrade").write_text(ROM_UPDATER.replace("1.0.84", "1.0.99"))
        rc, lines = self.run_script()
        self.assertEqual(rc, 0, lines)
        self.assertIn("post_setup: note: the guard has not reviewed the updater in /rom and refuses every write "
                      "until it is updated", lines)

    def test_ssh_option_runs_the_script_through_a_command(self):
        env = (f"env PATH={self.d / 'bin'} POST_SETUP_ROOT={self.root} TMPDIR={self.d / 'tmp'} PS_ENV={self.env} "
               f"PS_LOG={self.log} {BUSYBOX} sh -c")
        r = subprocess.run([sys.executable, str(BASE / "easy/post_setup.py"), "--ssh", env, "--check"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("post_setup: result: 11 would change, then a reboot", r.stdout)
        r = subprocess.run([sys.executable, str(BASE / "easy/post_setup.py"), "--ssh", "false"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3)
        self.assertIn("connection or login problem", r.stderr)

    def test_option_errors(self):
        with self.assertRaises(ValueError):
            post_setup.build_script("check", reboot=True)
        with self.assertRaises(ValueError):
            post_setup.build_script("other")
        r = subprocess.run([sys.executable, str(BASE / "easy/post_setup.py"), "--print", "--check", "--reboot"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
