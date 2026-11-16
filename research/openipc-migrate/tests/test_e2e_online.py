#!/usr/bin/env python3
"""End-to-end offline test of the stock-U-Boot online migration (loopback only: no camera, no real flash).

serve_migration.py on 127.0.0.1 drives the REAL camera/stage2.sh, migrate_hook.sh, installer/om_stockboot.sh
and camera/trial.sh against the synthetic camera of sb_fixture (stock MTD files, --sim-dir):
  * phase1 and trial.sh run under BusyBox sh with a PATH that offers only the applets of the stock
    BusyBox 1.18.4 (logging wrappers; dangerous ones are blocked, kernel/network tools of the trial stubbed);
  * phase2, the installer and the hook run under a stand-in for the OpenIPC runtime BusyBox (the host
    BusyBox plus crc32; flash_eraseall, reboot, mknod, mount and devmem refuse to run); the SoC's SP805
    watchdog is the file-backed om_testlib.FakeSP805 behind OM_DEVMEM;
  * build_stockboot.py turns the uploads the server stored into the next action set, as the operator
    does: diagnose -> wdtgate -> probe-arm -> collect -> trial-arm -> (trial boot) -> collect -> final.
The vendor U-Boot is modelled by sb_fixture.uboot_saved (it runs the armed bootcmd up to saveenv). The
real ARM userlands are exercised separately under qemu by validate_online_qemu.py.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import (HOOK, INSTALLER, ROOT, STAGE2, STOCK_WGET_SAFE, FakeSP805, free_port,  # noqa: E402
                        make_bundle, private_tmp, rmtree, sm, start_server, write_action_set)
import sb_fixture as fx  # noqa: E402
from sb_fixture import sha, sl  # noqa: E402

import build_stockboot as bs  # noqa: E402
import auth_fixture as af  # noqa: E402

BUSYBOX = fx.BUSYBOX
# validate_online_qemu.py runs this suite with the REAL camera binaries under qemu-arm: OM_E2E_STOCK_BB is a
# launcher of the stock BusyBox 1.18.4 (phase1, trial.sh and their applets) and OM_E2E_RUNTIME_BB one of the
# patched OpenIPC BusyBox that phase1 downloads (phase2, om_stockboot.sh, the hook). Default: host BusyBox.
STOCK_BB = os.environ.get("OM_E2E_STOCK_BB") or BUSYBOX
RUNTIME_BB = os.environ.get("OM_E2E_RUNTIME_BB")
SLEEP = shutil.which("sleep")
MKSQ = os.environ.get("OM_MKSQUASHFS") or shutil.which("mksquashfs")
UNSQ = os.environ.get("OM_UNSQUASHFS") or shutil.which("unsquashfs")
SSID, PASS = "dummy-ssid-for-test", "dummy-pass-for-test"
# Applets of the camera's stock BusyBox 1.18.4 (stock rootfs); phase1 and trial.sh get nothing else.
STOCK_APPLETS = tuple((
    "ash awk basename blockdev cat clear cmp cp crond crontab date dd deallocvt df dirname dmesg echo env expr "
    "false getty grep halt ifconfig init insmod iostat ip kill killall linuxrc ln login ls lsmod md5sum mkdir "
    "mkdosfs mkfs.vfat mknod modprobe more mount mv od ping poweroff ps pwd reboot rev rm rmdir rmmod route "
    "sendmail setconsole sh sha1sum sleep stat stty sum swapoff swapon sync sysctl tail telnetd time top touch "
    "true udhcpc umount uname usleep wget yes").split())
# Recorded but never executed by the harness.
BLOCKED = frozenset((
    "blockdev clear crond crontab deallocvt getty halt ifconfig init insmod ip kill killall linuxrc login mkdosfs "
    "mkfs.vfat mknod modprobe more mount ping poweroff reboot rmmod route sendmail setconsole stty swapoff swapon "
    "sysctl telnetd top udhcpc umount").split())
# trial.sh: the kernel/network side of the trial boot (modules, Wi-Fi link, DHCP) is stubbed.
TRIAL_STUBS = {"insmod": "exit 0", "lsmod": 'echo "mt7601sta 0 0 - Live 0x0 (stub)"', "ifconfig": "exit 0",
               "udhcpc": "exit 0", "dmesg": "echo dmesg-stub", "uname": "echo 3.0.8",
               "sleep": 'echo "$1" >> "$OMT_SLEEP_LOG"; ' + f'exec {SLEEP} 0.01' }
RUN_TIMEOUT = int(os.environ.get("OM_E2E_RUN_TIMEOUT", "900"))  # one boot of the stock firmware
# Host BusyBox wget crashes on -T; the stand-ins drop "-T N" (the camera tools keep it).
STRIP_T = ('skip=\nfor a do\n  shift\n  if [ -n "$skip" ]; then skip=; continue; fi\n'
           '  if [ "$a" = -T ]; then skip=1; continue; fi\n  set -- "$@" "$a"\ndone\n')
# What the stock BusyBox 1.18.4 rejects (qemu runs of the real binary), so that runs on the host BusyBox fail the
# same way: dd takes only if/of/bs/count/skip/seek, grep has no -x.
STOCK_OPTION_GUARDS = {
    "dd": ('for a do\n  case "$a" in if=*|of=*|bs=*|count=*|skip=*|seek=*) ;;\n'
           '    *) echo "dd: $a: not an operand of the stock BusyBox 1.18.4 dd" >&2; exit 1;;\n  esac\ndone\n'),
    "grep": ('for a do\n  case "$a" in --) break;; -*x*) echo "grep: invalid option -- x" >&2; exit 1;; -*) ;;\n'
             '    *) break;;\n  esac\ndone\n'),
}
# The stock wget form-escapes --post-data (om_testlib.STOCK_WGET_SAFE) and the host one posts it as is: its stand-in
# escapes it first, so the server decodes the same body on both.
WGET_FORM_PY = ("import os, sys\nfrom urllib.parse import quote_from_bytes\na = sys.argv[2:]\n"
                "if '--post-data' in a[:-1]:\n    i = a.index('--post-data') + 1\n"
                f"    a[i] = quote_from_bytes(os.fsencode(a[i]), safe={STOCK_WGET_SAFE!r})\n"
                "os.execv(sys.argv[1], [sys.argv[1], 'wget'] + a)\n")
NEEDED = {"awk", "base64", "cat", "cmp", "cp", "dd", "df", "expr", "grep", "head", "ls", "md5sum", "mkdir", "mknod",
          "od", "pidof", "readlink", "reboot", "rm", "sh", "sha1sum", "sha256sum", "sleep", "sync", "tail", "timeout",
          "tr", "true", "wc", "wget"}
DAT = "Default\nCountryRegion=1\nSSID=\nNetworkType=Infra\nAuthMode=OPEN\nEncrypType=NONE\nWPAPSK=\n"
MDAT = {"mdat1": (0x540000, 0x20000), "mdat2": (0x560000, 0x20000)}


def runtime_standin(crc: Path) -> str:
    guard = ('  flash_eraseall|reboot|poweroff|halt|mknod|mount|umount|insmod|devmem) echo "$1 must not run offline" >&2;'
             " exit 99;;\n  sync) exit 0;;\n")
    if RUNTIME_BB:
        return ("#!/bin/sh\n# qemu launcher of the real OpenIPC runtime BusyBox (offline validation).\n"
                'case "${1:-}" in\n' + guard + f'esac\nexec {RUNTIME_BB} "$@"\n')
    return ("#!/bin/sh\n# Offline stand-in for the OpenIPC runtime BusyBox (host BusyBox + crc32).\n"
            'case "${1:-}" in\n'
            f"  --list) {BUSYBOX} --list; echo crc32; echo flash_eraseall; exit 0;;\n"
            f'  crc32) shift; exec {sys.executable} {crc} "$@";;\n' + guard +
            "  wget) shift\n" + STRIP_T + f'    exec {BUSYBOX} wget "$@";;\nesac\nexec {BUSYBOX} "$@"\n')


def proc_state(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def host_applets() -> set:
    if not BUSYBOX:
        return set()
    r = subprocess.run([BUSYBOX, "--list"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    return set(r.stdout.split())


@unittest.skipUnless(BUSYBOX and SLEEP and NEEDED <= host_applets(), "host BusyBox with the needed applets required")
class OnlineChain(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-e2e")
        self.addCleanup(rmtree, self.tmp)
        self.cam = fx.Camera(self.tmp / "cam")
        self.fw, self.oenv = fx.firmware(), fx.oenv()
        self.om_dir, self.app, self.uploads = self.tmp / "om", self.tmp / "app", self.tmp / "uploads"
        self.stockbin, self.trialbin, self.trace = self.tmp / "stockbin", self.tmp / "trialbin", self.tmp / "trace.log"
        for d in (self.app, self.stockbin, self.trialbin):
            d.mkdir()
        for name in STOCK_APPLETS:
            self.write_wrapper(self.stockbin, name, None)
            self.write_wrapper(self.trialbin, name, "tr", TRIAL_STUBS.get(name))
        (self.tmp / "crc32.py").write_text(fx.CRC32_PY)
        (self.tmp / "wget-form.py").write_text(WGET_FORM_PY)
        self.dummy_name = "omq" + secrets.token_hex(4)  # stands in for cmsMain.out
        exe = self.tmp / self.dummy_name
        shutil.copy2(SLEEP, exe)
        self.dummy = subprocess.Popen([str(exe), "900"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.kill_dummy)
        self.port, self.srv, self.bundle_n = free_port(), None, 0
        self.wdt = FakeSP805(self.tmp)  # armed (ctrl 3) and write-locked, as cmsMain.out leaves it
        self.addCleanup(self.stop_server)
        old = bs.FIRMWARE_SHA256
        bs.FIRMWARE_SHA256 = sha(self.fw)  # the synthetic image instead of the pinned build
        self.addCleanup(setattr, bs, "FIRMWARE_SHA256", old)

    def write_wrapper(self, bindir: Path, name: str, tag, body=None) -> None:
        text = f"#!{BUSYBOX} sh\n"
        if tag is None:  # phase2 runs with OM_CONF inside OM_DIR
            text += f'case "${{OM_CONF:-}}" in "{self.om_dir}/"*) t=p2;; *) t=p1;; esac\n'
        else:
            text += f"t={tag}\n"
        text += f'echo "$t {name}" >> "{self.trace}"\n'
        if body is not None:
            text += body + "\n"
        elif name in BLOCKED:
            text += "exit 126\n"
        elif name == "sync":  # recorded only: a host-wide sync can take minutes on a busy machine
            text += "exit 0\n"
        elif name == "wget" and STOCK_BB == BUSYBOX:
            text += STRIP_T + f'exec {sys.executable} {self.tmp / "wget-form.py"} {BUSYBOX} "$@"\n'
        elif name in STOCK_OPTION_GUARDS:  # like the stock BusyBox 1.18.4 applet, also when the host one runs
            text += STOCK_OPTION_GUARDS[name] + f'exec {STOCK_BB} {name} "$@"\n'
        else:
            text += f'exec {STOCK_BB} {name} "$@"\n'
        (bindir / name).write_text(text)
        os.chmod(bindir / name, 0o755)

    def kill_dummy(self):
        if self.dummy.poll() is None:
            self.dummy.kill()
        self.dummy.wait(timeout=30)

    def stop_server(self):
        if self.srv is not None:
            self.srv.stop()
            self.srv = None

    # ---- fixtures ---------------------------------------------------------------------------------------
    def served_files(self) -> dict:
        return {"runtime/busybox": runtime_standin(self.tmp / "crc32.py").encode(),
                "runtime/ld-musl.so": b"placeholder: the host stand-in needs no musl loader\n",
                "runtime/om_stockboot.sh": INSTALLER.read_bytes(), "runtime/migrate_hook.sh": HOOK.read_bytes(),
                "payload/firmware.bin": self.fw, "payload/env-openipc.img": self.oenv}

    def setup_bundle(self, *, conf_files=None, conf_run_id=None, overrides=None):
        ov = {"OM_DIR": self.om_dir, "OM_LOCK": self.tmp / "lock", "OM_APP_DIR": self.app,
              "OM_POLL_INTERVAL": 1, "OM_POLL_MAX": 3, "OM_IDLE_MAX": 2, "OM_MIN_AVAIL_KB": 1, "OM_MIN_DIRFREE_KB": 1,
              "OM_RETRY_SLEEP": 1, "OM_HOLD_MAX": 2, "OM_STOCK_BB": STOCK_BB, "OM_SIM_DIR": self.cam.sim,
              "OM_SIM_MTD_PROC": self.cam.sim / "proc_mtd", "OM_QUIESCE_PROCS": self.dummy_name,
              "OM_WATCHDOG_DEV": self.tmp / "no-watchdog", "OM_DEVMEM": self.wdt.script, "OM_SIM_GATE_HOLD": 2}
        ov.update(overrides or {})
        self.bundle_n += 1
        self.bundle = self.tmp / f"bundle-{self.bundle_n}"
        self.run_id, conf = make_bundle(self.bundle, self.served_files(), host="127.0.0.1", port=self.port,
                                        overrides=ov, allow_test_keys=True, conf_files=conf_files)
        if conf_run_id:
            conf = conf.replace(f"OM_RUN_ID='{self.run_id}'", f"OM_RUN_ID='{conf_run_id}'")
        (self.app / "stage2.sh").write_bytes(STAGE2.read_bytes())
        os.chmod(self.app / "stage2.sh", 0o755)
        (self.app / "migrate.conf").write_text(conf)

    def start(self, mode: str, action_dir=None, *, write: bool = True, reboot: bool = False):
        self.stop_server()
        kw = {}
        if mode in sm.ACTIONS:
            kw["backup_dir"] = self.uploads
            wphrase, rphrase, _ = sm.ACTIONS[mode]
            if wphrase and write:
                kw["write_confirm"] = wphrase
                if reboot and rphrase:
                    kw["reboot_confirm"] = rphrase
        self.srv, self.console = start_server(self.bundle, mode, port=self.port, action_dir=action_dir, **kw)
        return self.srv

    def boot(self) -> str:
        """A (re)boot of the stock firmware: /dev is a tmpfs, so the RAM stage and the lock are gone."""
        rmtree(self.om_dir)
        if (self.tmp / "lock").is_dir():
            (self.tmp / "lock").rmdir()
        return self.run_phase1()

    def run_phase1(self) -> str:
        """Start stage2.sh the way the patched runapp.sh does and wait for the whole chain to end."""
        env = {"PATH": str(self.stockbin), "OM_CONF": str(self.app / "migrate.conf")}
        proc = subprocess.Popen([STOCK_BB, "sh", str(self.app / "stage2.sh"), "phase1"], env=env, cwd=self.tmp,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail("stage2.sh did not finish")
        self.assertEqual(proc.returncode, 0, out)
        return out.decode(errors="replace")

    def action(self, mode: str, action_dir=None, **kw):
        """One camera action: a server in `mode` and a fresh boot that runs it, then the server stops."""
        self.start(mode, action_dir, **kw)
        n = len(self.srv.events)
        self.boot()
        events = [(p, e) for _, p, e in self.srv.events[n:]]
        self.assertIn(("2", f"action-start-{mode}"), events)
        self.assertIn(("2", f"action-rc-{mode}-0"), events, self.action_out())
        self.assertFalse([e for p, e in events if e.startswith("stopped")], events)
        self.assertEqual(self.srv.action_rc, 0)
        self.assertNotIn(self.run_id, self.console.getvalue())
        return events

    def flash_state(self) -> str:
        p = self.om_dir / "stage" / "flash.state"
        return p.read_text().strip() if p.exists() else "clean"

    def action_out(self) -> str:
        p = self.om_dir / "action.out"
        return p.read_text(errors="replace") if p.exists() else ""

    def bs(self, *argv, rc=0):
        if argv and argv[0] in ("trial", "plan") and "--auth-runtime" not in argv:
            argv = (*argv, "--auth-runtime", str(af.runtime(self.tmp / "auth-runtime")))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            got = bs.main(["--state", str(self.tmp / "state"), *argv])
        text = out.getvalue() + err.getvalue()
        self.assertEqual(got, rc, text)
        for secret in (SSID, PASS, "00:00:5e:00:53:01", self.run_id):
            self.assertNotIn(secret, text)
        return json.loads(out.getvalue()) if rc == 0 else text

    def set_flash(self, off: int, data: bytes) -> None:
        flash = bytearray(self.cam.flash())
        flash[off:off + len(data)] = data
        self.cam.write_flash(bytes(flash))

    def fix_diag_info(self) -> None:
        """The host has no MT7601 on USB (and may have a watchdog): give the newest diag-info the
        camera's facts, the way test_build_stockboot does."""
        info = sorted(self.uploads.glob("diag-info-*.bin"))[-1]
        secs, cur = [], None
        for line in info.read_text().splitlines():
            if line.startswith("== "):
                cur = [line[3:], []]
                secs.append(cur)
            elif cur:
                cur[1].append(line)
        fixed = {"usb": ["1-1 148f:7601 802.11 n WLAN"], "watchdog": ["ls: /dev/watchdog: No such file or directory"]}
        info.write_text("".join(f"== {n}\n" + "".join(x + "\n" for x in fixed.get(n, body)) for n, body in secs))

    def oipc_rootfs(self) -> Path:
        r = self.tmp / "oipc"
        mod = r / "lib" / "modules" / "3.0.8"
        (mod / "kernel" / "net" / "wireless").mkdir(parents=True)
        (mod / "extra").mkdir()
        (mod / "hisilicon").mkdir()
        (mod / "modules.dep").write_text("kernel/net/wireless/cfg80211.ko:\nextra/mt7601sta.ko: "
                                         "kernel/net/wireless/cfg80211.ko\nhisilicon/wdt.ko:\n")
        (mod / "kernel" / "net" / "wireless" / "cfg80211.ko").write_bytes(fx.rbytes(5, 4000))
        (mod / "extra" / "mt7601sta.ko").write_bytes(fx.rbytes(6, 9000))
        (mod / "hisilicon" / "wdt.ko").write_bytes(fx.rbytes(7, 1024))
        (r / "etc" / "mediatek").mkdir(parents=True)
        (r / "etc" / "mediatek" / "MT7601USTA.dat").write_text(DAT)
        return r

    def wifi_vars(self) -> Path:
        p = self.tmp / "wifi.vars"
        p.write_text(f"wlanssid={SSID}\nwlanpass={PASS}\n")
        p.chmod(0o600)
        return p

    def action_set(self, action: str, files: dict) -> Path:
        flash = self.cam.flash()
        return write_action_set(self.tmp / f"set-{action}-{secrets.token_hex(3)}", action, files,
                                sha(flash[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN]), sha(flash[:sl.UBOOT_LEN]))

    # ---- observations -------------------------------------------------------------------------------------
    def events(self, phase=None):
        return [e for _, p, e in self.srv.events if phase is None or p == phase]

    def traced(self, tag: str) -> list:
        if not self.trace.exists():
            return []
        return [line.split()[1] for line in self.trace.read_text().splitlines() if line.startswith(tag + " ")]

    def assertInOrder(self, want, got):
        it = iter(got)
        missing = [w for w in want if not any(g == w for g in it)]
        self.assertFalse(missing, f"{missing} not in order in {got}")

    def assertWatchdogArmed(self):
        st = self.wdt.get()
        self.assertEqual((st["ctrl"], st["locked"]), (3, True), st)

    def assertFlashUnchanged(self):
        self.assertEqual(sha(self.cam.flash()), sha(self.cam.original))

    def assertToolDiscipline(self):
        self.assertEqual(self.traced("p2"), [], "phase2/installer/hook must only use the RAM BusyBox")
        used = set(self.traced("p1"))
        self.assertFalse(used & BLOCKED, used)
        self.assertTrue(used <= set(STOCK_APPLETS), used)

    def check_final_layout(self, final: bytes):
        after = self.cam.flash()
        self.assertEqual(after[:sl.UBOOT_LEN], self.cam.original[:sl.UBOOT_LEN])  # vendor U-Boot never written
        self.assertEqual(after[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN], final)
        self.assertEqual(after[sl.OIPC_ENV_OFF:sl.OIPC_ENV_OFF + sl.OIPC_ENV_LEN], self.oenv)
        self.assertEqual(after[sl.FW_OFF:sl.FW_OFF + sl.FW_LEN], self.fw)
        self.assertEqual(after[sl.OVERLAY_OFF:], b"\xff" * sl.OVERLAY_LEN)

    # ---- the trial boot ----------------------------------------------------------------------------------
    def run_trial(self, trial: bytes, senv: bytes = None, conf: Path = None, apply: bool = True) -> tuple:
        """trial.sh from the payload that was written to bakfs, under the stock applets, with the tl and
        senv partitions of the trial layout mapped onto files of the simulated flash (senv: what U-Boot left
        there, default the flash). Returns the log flags and the senv bytes; apply: tl goes to the flash."""
        self.trial_n = getattr(self, "trial_n", 0) + 1
        base = self.tmp / f"trial-{self.trial_n}"
        base.mkdir()
        used = struct.unpack_from("<Q", trial, sl.TK_LEN + 40)[0]
        img, pay = base / "tp.squashfs", base / "tp"
        img.write_bytes(trial[sl.TK_LEN:sl.TK_LEN + used])
        subprocess.run([UNSQ, "-no-progress", "-d", str(pay), str(img)], check=True, capture_output=True,
                       timeout=300, stdin=subprocess.DEVNULL)
        self.assertEqual((pay / "trial.sh").read_bytes(), (ROOT / "camera" / "trial.sh").read_bytes())
        work, dev, net, proc = (base / n for n in ("omt", "omt-dev", "net", "trial-proc"))
        for d in (dev, net / "lo", net / "eth0", net / "ra0", proc):
            d.mkdir(parents=True)
        flash = self.cam.flash()
        (dev / "b7").write_bytes(flash[sl.TL_OFF:sl.TL_OFF + sl.TL_LEN])
        (dev / "b8").write_bytes(flash[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] if senv is None else senv)
        os.symlink("b7", dev / "c7")  # char and block device of one partition: the same bytes
        os.symlink("b8", dev / "c8")
        (proc / "cmdline").write_text(sl.TRIAL_BOOTARGS + "\n")
        (proc / "mtd").write_text("dev:    size   erasesize  name\n" + "".join(
            'mtd%d: %08x %08x "%s"\n' % (i, s, sl.ERASE, n) for i, (n, _, s, _) in enumerate(sl.TRIAL_LAYOUT)))
        (proc / "devices").write_text("Character devices:\n 90 mtd\n\nBlock devices:\n 31 mtdblock\n")
        env = {"PATH": "/nonexistent", "OMT_TEST_PATH": str(self.trialbin), "OMT_PAYLOAD": str(pay),
               "OMT_WORK": str(work), "OMT_CONF": str(conf or self.app / "migrate.conf"),
               "OMT_PROC_MTD": str(proc / "mtd"), "OMT_PROC_CMDLINE": str(proc / "cmdline"),
               "OMT_PROC_DEVICES": str(proc / "devices"),
               "OMT_NET_DIR": str(net), "OMT_DEV_DIR": str(dev), "OMT_NO_ETC": "1", "OMT_NO_REBOOT": "1",
               "OMT_SLEEP_LOG": str(base / "sleeps"),
               "OMT_SUPPLICANT": str(base / "auth-stub"),
               "OMT_AUTH_PID": str(base / "auth.pid"),
               "OMT_AUTH_MODE": getattr(self, "auth_failure", "")}
        # One line per start: PID and driver backend. The *-fallback modes fail the nl80211 attempt (exit 255
        # as a rejected configuration or driver init does, or no event until it is stopped) and connect on wext.
        (base / "auth-stub").write_text("#!/bin/sh\n"
            'echo "$$ $4" >> "$OMT_AUTH_PID"\n'
            '[ "$1 $3 $5" = "-i -D -c" ] || exit 99\n'
            'case "$OMT_AUTH_MODE" in exit) exit 1;; timeout) ;;\n'
            ' reject) echo CTRL-EVENT-ASSOC-REJECT;;\n'
            ' exit-fallback) [ "$4" = wext ] || exit 255; echo CTRL-EVENT-CONNECTED;;\n'
            ' timeout-fallback) [ "$4" = wext ] && echo CTRL-EVENT-CONNECTED;;\n'
            ' *) echo CTRL-EVENT-CONNECTED;; esac\n'
            f"exec {SLEEP} 900\n")
        (base / "auth-stub").chmod(0o755)

        if getattr(self, "auth_failure", "") == "pin":
            (pay / "auth.sha1").write_text("0" * 40 + "\n")
        sent = len(self.srv.uploads.get("trial-log", [])) if self.srv else 0
        r = subprocess.run([STOCK_BB, "sh", str(pay / "trial.sh")], env=env, cwd=self.tmp, capture_output=True,
                           text=True, timeout=600, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pidfile = base / "auth.pid"
        self.auth_backends, left = [], []
        marker = ("OMT_AUTH_PID=" + str(pidfile)).encode()
        for line in (pidfile.read_text().splitlines() if pidfile.exists() else []):
            pid, backend = line.split(" ", 1)
            self.auth_backends.append(backend)
            try:
                # Clean up only this harness child, identified by its unique environment.
                if proc_state(int(pid)) != "Z" and \
                        marker in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
                    os.kill(int(pid), 9)
                    left.append(backend)
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        self.assertEqual(left, [], "the trial left an authentication child running")
        log = (work / "trial.log").read_text()
        if self.srv and len(self.srv.uploads.get("trial-log", [])) > sent:  # stored as trial.sh wrote it
            self.assertEqual(self.srv.uploads["trial-log"][-1], sha((work / "trial.log").read_bytes().rstrip(b"\n")))
        for secret in (SSID, PASS):
            self.assertNotIn(secret, log)
        flags = dict(re.findall(r"^omt ([a-z0-9_]+)=(.*)$", log, re.M))
        sleeps = (base / "sleeps").read_text().splitlines() if (base / "sleeps").exists() else []
        if flags.get("result") == "completed":
            self.assertIn("70", sleeps, "success must follow the full stability wait")
        else:
            self.assertNotIn("70", sleeps, "a failed trial must not claim the stability window")
        tl = (dev / "b7").read_bytes()
        self.assertEqual(len(tl), sl.TL_LEN)
        self.assertEqual(tl[sl.TL_TEXT_LEN:sl.TL_TEXT_LEN + len(sl.TL_MARKER)], sl.TL_MARKER)
        self.assertEqual(tl[sl.TL_TEXT_LEN:], sl.tl_blank()[sl.TL_TEXT_LEN:])  # marker and padding intact
        self.assertIn("checkpoint end written to tl", log)
        text, tl_flags = bs.parse_trial_log(tl)  # the last checkpoint: the whole log up to its own note
        self.assertTrue(log.startswith(text) and log[len(text):].count("\n") == 1, log[len(text):])
        self.assertEqual(tl_flags, flags)
        if apply:
            self.set_flash(sl.TL_OFF, tl)
        used_tools = set(self.traced("tr"))
        self.assertTrue(used_tools <= set(STOCK_APPLETS), used_tools)
        return flags, (dev / "b8").read_bytes()

    # ---- the whole migration --------------------------------------------------------------------------------
    @unittest.skipUnless(MKSQ and UNSQ, "the trial payload needs mksquashfs/unsquashfs (OM_MKSQUASHFS/OM_UNSQUASHFS)")
    def test_full_migration_over_the_network(self):
        self.setup_bundle()
        fw_path, dump = self.tmp / "firmware.bin", self.tmp / "spi.bin"
        fw_path.write_bytes(self.fw)
        dump.write_bytes(self.cam.original)
        # 1. diagnose: read-only, uploads the facts build_stockboot.py needs
        ev = self.action("diagnose")
        self.assertInOrder([("1", "go-received"), ("1", "runtime-ready"), ("2", "phase2-start"),
                            ("2", "action-start-diagnose"), ("installer", "diagnose-done"),
                            ("2", "action-rc-diagnose-0"), ("2", "idle-exit")], ev)
        self.assertFlashUnchanged()
        self.assertEqual(sorted(self.srv.uploads), ["diag-blocks", "diag-env", "diag-info", "diag-uboot",
                                                    "log-diagnose"])
        self.assertEqual(self.srv.uploads["diag-uboot"], [sha(self.cam.original[:sl.UBOOT_LEN])])
        self.assertEqual(self.wdt.get()["writes"], [])  # diagnose only reads the watchdog
        self.assertEqual(self.srv.served, {rel: 1 for rel in sm.RUNTIME_FILES})  # no payload outside final
        self.fix_diag_info()
        res = self.bs("diag", "--uploads", str(self.uploads), "--spi-dump", str(dump))
        self.assertTrue(res["ok"], res)
        # 1b. wdtgate: every stock process stopped and the SP805 off for the hold, then re-armed and resumed
        ev = self.action("wdtgate")
        self.assertInOrder([("2", "action-start-wdtgate"), ("installer", "wdtgate-quiesced"),
                            ("installer", "wdtgate-held-1"), ("installer", "wdtgate-held-2"),
                            ("installer", "wdtgate-done"), ("2", "action-rc-wdtgate-0"), ("2", "idle-exit")], ev)
        self.assertFlashUnchanged()
        self.assertNotEqual(proc_state(self.dummy.pid), "T")
        self.assertWatchdogArmed()
        self.assertEqual(len(self.srv.uploads["wdtgate-info"]), 2)  # while quiesced, and after the resume
        self.assertEqual(self.srv.served, {rel: 1 for rel in sm.RUNTIME_FILES})
        with mock.patch.object(bs, "MIN_GATE_HOLD", 1):
            res = self.bs("wdtgate", "--uploads", str(self.uploads))
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["watchdog_enabled_before"], res["sp805_identified"], res["counter_frozen_while_held"]),
                         (True, True, True))
        # 2. probe-arm: the vendor env gets the saveenv probe; the "reboot" is the probe boot
        out = self.tmp / "act-probe"
        self.bs("probe", "--out", str(out))
        probe = (out / "env-probe.img").read_bytes()
        ev = self.action("probe-arm", out, reboot=True)
        self.assertInOrder([("installer", "armed-probe-arm"), ("installer", "rebooting-probe-arm"),
                            ("2", "action-rc-probe-arm-0")], ev)
        self.assertEqual(self.cam.senv(), probe)
        self.assertEqual(self.cam.flash()[sl.SENV_OFF + sl.SENV_LEN:], self.cam.original[sl.SENV_OFF + sl.SENV_LEN:])
        self.assertNotEqual(proc_state(self.dummy.pid), "T")  # the stock app was resumed
        self.assertWatchdogArmed()  # re-armed before the reboot
        self.set_flash(sl.SENV_OFF, fx.uboot_saved(probe))  # U-Boot: setenv stock bootcmd, saveenv, boot stock
        self.action("collect")
        self.assertTrue(self.bs("collect", "--uploads", str(self.uploads), "--stage", "probe")["ok"])
        # 3. trial-arm: trial image into bakfs, armed env; the trial boot reports over the network
        out = self.tmp / "act-trial"
        res = self.bs("trial", "--firmware", str(fw_path), "--oipc-rootfs", str(self.oipc_rootfs()),
                      "--wifi-vars", str(self.wifi_vars()), "--mksquashfs", MKSQ, "--out", str(out))
        self.assertLessEqual(res["payload_bytes"], sl.TP_LEN)
        trial, armed = (out / "trial.img").read_bytes(), (out / "env-armed.img").read_bytes()
        self.action("trial-arm", out, reboot=True)
        flash = self.cam.flash()
        self.assertEqual(flash[sl.TRIAL_OFF:sl.TRIAL_OFF + sl.TRIAL_LEN], trial)
        self.assertEqual(self.cam.senv(), armed)
        # The trial-arm server keeps running: the camera reboots into the trial and reports to it.
        self.set_flash(sl.SENV_OFF, fx.uboot_saved(armed))  # U-Boot saves the stock bootcmd, boots the trial
        flags, senv = self.run_trial(trial)
        self.assertEqual(senv, self.cam.senv())  # the env U-Boot saved is left alone
        nonce = json.loads((self.tmp / "state" / "trial-build.json").read_text())["nonce"]
        self.assertEqual(flags, {"nonce": nonce, "kernel": "3.0.8", "cmdline_mark": "1", "mtd_table": "ok",
                                 "env": "saved-by-uboot", "insmod_cfg80211": "0", "insmod_mt7601sta": "0",
                                 "iface": "ra0", "dhcp": "ok", "host": "ok", "result": "completed",
                                 "insmod_wdt": "0", "ifup": "0", "dhcp_attempt": "1", "stable_window": "ok",
                                 "auth_binary": "ok", "auth": "connected"})
        self.assertEqual(self.auth_backends, ["nl80211"])
        self.assertEqual(self.events("trial"), ["trial-network-up", "trial-completed"])
        self.assertEqual(len(self.srv.uploads["trial-log"]), 1)
        # Had saveenv failed, the armed env would still be there: trial.sh writes the stock env back over the
        # whole senv partition (here without a migrate.conf, so it reports nowhere and skips the network).
        flags, senv = self.run_trial(trial, senv=armed, conf=self.tmp / "no-migrate.conf", apply=False)
        self.assertEqual((flags["env"], flags["result"]), ("armed-restored", "no-migrate-conf"))
        self.assertEqual(senv, (self.tmp / "state" / "env-live-probe.img").read_bytes())
        self.assertEqual(len(self.srv.uploads["trial-log"]), 1)
        self.action("collect")  # back in stock: the saved env boots the stock kernel
        res = self.bs("collect", "--uploads", str(self.uploads), "--stage", "trial")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["log_source"], "trial-log upload")
        # 4. final: mdat backups to the host, then the OpenIPC layout behind the vendor U-Boot
        out = self.tmp / "act-final"
        self.bs("final", "--firmware", str(fw_path), "--out", str(out))
        final = (out / "env-final.img").read_bytes()
        ev = self.action("final", out, reboot=True)
        self.assertInOrder([("2", "action-start-final"), ("installer", "final-pre-ponr"), ("installer", "ponr"),
                            ("installer", "final-verified"), ("installer", "rebooting-final"),
                            ("2", "action-rc-final-0"), ("2", "hold-final-verified-power-cycle"),
                            ("2", "hold-final-verified-power-cycle")], ev)
        self.check_final_layout(final)
        self.assertEqual({n: d for n, d in self.srv.uploads.items() if n in MDAT},
                         {n: [sha(self.cam.original[o:o + s])] for n, (o, s) in MDAT.items()})
        self.assertEqual(len(list(self.uploads.glob("mdat1-*.bin"))), 1)
        self.assertEqual(self.srv.served, {**{rel: 1 for rel in sm.RUNTIME_FILES + sm.PAYLOAD_FILES},
                                           "action/env-final.img": 1})
        self.assertEqual(self.flash_state(), "final")
        self.assertEqual(proc_state(self.dummy.pid), "T")  # stock writers stay stopped on the migrated flash
        self.assertWatchdogArmed()  # back on for the reboot into OpenIPC
        self.assertIn("watchdog off (ctrl was 3, counter frozen)", self.action_out())
        self.assertFalse((self.om_dir / "payload").exists())
        self.assertToolDiscipline()
        self.assertTrue({"wget", "sha1sum", "md5sum", "expr"} <= set(self.traced("p1")))
        # Replay only the extracted trial in the file-backed harness: no real device and no network.
        # The successful collection above is left untouched; the failure mailboxes are not applied.
        for mode in ("watchdog", "ifup", "dhcp", "auth-exit", "auth-timeout", "auth-reject", "auth-pin",
                     "auth-exit-fallback", "auth-timeout-fallback"):
            self.auth_failure = mode.removeprefix("auth-") if mode.startswith("auth-") else ""
            with self.subTest(trial_failure=mode):
                self.trace.write_text("")
                self.write_wrapper(self.trialbin, "insmod", "tr",
                    'case "$1" in */wdt.ko) exit 1;; esac; exit 0' if mode == "watchdog" else "exit 0")
                self.write_wrapper(self.trialbin, "ifconfig", "tr",
                    '[ "$2" != up ]' if mode == "ifup" else "exit 0")
                self.write_wrapper(self.trialbin, "udhcpc", "tr", "exit 1" if mode == "dhcp" else "exit 0")
                flags, _ = self.run_trial(trial, senv=fx.uboot_saved(armed),
                                          conf=self.tmp / "no-migrate.conf", apply=False)
                self.assertEqual(flags["result"], {"watchdog": "watchdog-init-failed",
                    "ifup": "wifi-ifup-failed", "dhcp": "no-dhcp-lease",
                    "auth-exit": "wifi-auth-process-exit", "auth-timeout": "wifi-auth-failed",
                    "auth-reject": "wifi-auth-failed", "auth-pin": "auth-runtime-invalid",
                    "auth-exit-fallback": "no-migrate-conf", "auth-timeout-fallback": "no-migrate-conf"}[mode])
                self.assertNotIn("stable_window", flags)
                self.assertEqual(self.traced("tr").count("udhcpc"),
                                 {"dhcp": 8, "auth-exit-fallback": 1, "auth-timeout-fallback": 1}.get(mode, 0))
                # Each attempt's outcome and the supplicant's own exit status (128+15: the stub dies on SIGTERM).
                keys = ("auth", "auth_rc", "auth_wext", "auth_wext_rc")
                self.assertEqual({k: v for k, v in flags.items() if k in keys},
                                 {"dhcp": {"auth": "connected"},
                                  "auth-exit": {"auth": "process-exit", "auth_rc": "1",
                                                "auth_wext": "process-exit", "auth_wext_rc": "1"},
                                  "auth-timeout": {"auth": "wait-expired", "auth_rc": "143",
                                                   "auth_wext": "wait-expired", "auth_wext_rc": "143"},
                                  "auth-reject": {"auth": "association-rejected", "auth_rc": "143",
                                                  "auth_wext": "association-rejected", "auth_wext_rc": "143"},
                                  "auth-exit-fallback": {"auth": "process-exit", "auth_rc": "255",
                                                         "auth_wext": "connected"},
                                  "auth-timeout-fallback": {"auth": "wait-expired", "auth_rc": "143",
                                                            "auth_wext": "connected"}}.get(mode, {}))
                # Never two at once: wext only after the nl80211 supplicant is confirmed gone.
                self.assertEqual(self.auth_backends, [] if mode in ("watchdog", "ifup", "auth-pin") else
                                 ["nl80211"] if mode == "dhcp" else ["nl80211", "wext"])
                if mode == "watchdog":
                    self.assertNotIn("insmod_cfg80211", flags)
                elif mode == "dhcp":
                    self.assertEqual((flags["dhcp"], flags["dhcp_attempt"]), ("fail", "8"))

    # ---- stays inert ------------------------------------------------------------------------------------
    def test_standby_times_out_and_the_lock_blocks_a_second_start(self):
        self.setup_bundle()
        self.start("standby")
        self.boot()
        self.assertEqual(self.events(), ["standby"] * 3 + ["standby-timeout"])
        self.assertEqual(self.srv.served, {})
        self.assertEqual(sorted(p.name for p in self.om_dir.iterdir()), ["manifest", "stage2.log"])
        before = len(self.srv.events)
        self.run_phase1()
        self.assertEqual(len(self.srv.events), before)  # a second start ends at the lock directory
        self.assertFlashUnchanged()
        self.assertToolDiscipline()

    def test_abort(self):
        self.setup_bundle()
        self.start("abort")
        self.boot()
        self.assertEqual(self.events(), ["aborted"])
        self.assertEqual(self.srv.served, {})

    def test_foreign_run_id_gets_nothing_even_from_a_final_server(self):
        self.setup_bundle(conf_run_id=secrets.token_hex(16))
        final, _ = sl.build_final(self.cam.e0)
        self.start("final", self.action_set("final", {"env-final.img": final}), reboot=True)
        self.boot()
        self.assertEqual(self.srv.events, [])
        self.assertEqual(self.srv.refused, 4)  # three manifest polls + the standby-timeout report
        self.assertEqual(self.srv.served, {})
        self.assertFlashUnchanged()

    def test_go_without_the_phrase_is_ignored(self):
        self.setup_bundle()
        self.start("diagnose")
        self.srv.manifest = lambda: f"run={self.run_id}\nmode=go\naction=diagnose\nseq={'1' * 16}\n".encode()
        self.boot()
        self.assertEqual(self.events(), ["go-phrase-missing"] * 3 + ["standby-timeout"])
        self.assertEqual(self.srv.served, {})

    def test_low_memory_stops_before_any_download(self):
        self.setup_bundle(overrides={"OM_MIN_AVAIL_KB": 999999999})
        self.start("diagnose")
        self.boot()
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertRegex(events[0], r"^lowmem-[0-9]+-[0-9]+$")
        self.assertEqual(self.srv.served, {})

    def fake_zero_space(self, kind: str, total: int, *, mount_kind="tmpfs", mount_opts="rw"):
        self.write_wrapper(self.stockbin, "df", None,
                           "cat <<'EOF'\nFilesystem 1K-blocks Used Available Use% Mounted on\nnone 0 0 0 0% /dev\nEOF")
        self.write_wrapper(self.stockbin, "stat", None,
                           "cat <<'EOF'\n  File: /dev/om\n    ID: 0 Namelen: 255 Type: " + kind +
                           f"\nBlock size: 4096\nBlocks: Total: {total} Free: 0 Available: 0\nEOF")

        mounts = self.tmp / "mounts.fixture"
        mounts.write_text(f"none {self.om_dir} {mount_kind} {mount_opts} 0 0\n")
        self.write_wrapper(self.stockbin, "awk", None,
                           'if [ "$1" = "-v" ] && [ "${2%%=*}" = p ] && [ "$4" = /proc/mounts ]; then\n'
                           f'  exec {STOCK_BB} awk "$1" "$2" "$3" "{mounts}"\n'
                           f'fi\nexec {STOCK_BB} awk "$@"')

    def test_ramfs_zero_accounting_allows_read_only_diagnose(self):
        self.setup_bundle(overrides={"OM_MIN_AVAIL_KB": 12288, "OM_MIN_DIRFREE_KB": 10240})
        self.fake_zero_space("romfs", 0)  # real stock stat -f labels RAMFS as romfs
        self.action("diagnose")
        self.assertFlashUnchanged()
        self.assertToolDiscipline()

    def test_romfs_alias_on_read_only_ramfs_stops_before_download(self):
        self.setup_bundle()
        self.fake_zero_space("romfs", 0, mount_kind="ramfs", mount_opts="ro")
        self.start("diagnose")
        self.boot()
        self.assertRegex(self.events()[0], r"^lowmem-[0-9]+-0$")
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.srv.served, {})
        self.assertFlashUnchanged()

    def test_genuine_romfs_does_not_get_ramfs_capacity_bypass(self):
        self.setup_bundle()
        self.fake_zero_space("romfs", 0, mount_kind="romfs", mount_opts="ro")
        self.start("diagnose")
        self.boot()
        self.assertRegex(self.events()[0], r"^lowmem-[0-9]+-0$")
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.srv.served, {})
        self.assertFlashUnchanged()

    def test_full_bounded_tmpfs_still_stops_before_download(self):
        self.setup_bundle()
        self.fake_zero_space("tmpfs", 131072)
        self.start("diagnose")
        self.boot()
        self.assertRegex(self.events()[0], r"^lowmem-[0-9]+-0$")
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.srv.served, {})
        self.assertFlashUnchanged()

    # ---- verification failures ----------------------------------------------------------------------------
    def test_runtime_pin_mismatch_stops_phase1(self):
        for rel, name, served in (("runtime/busybox", "busybox", ["runtime/busybox"]),
                                  ("runtime/om_stockboot.sh", "om_stockboot.sh", sm.RUNTIME_FILES[:3])):
            with self.subTest(rel):
                pinned = self.served_files()
                pinned[rel] += b"# the reviewed file differs\n"
                self.setup_bundle(conf_files=pinned)
                self.start("diagnose")
                self.boot()
                self.assertEqual(self.events(), ["go-received", f"hash-mismatch-{name}"])
                self.assertEqual(self.srv.served, {r: 1 for r in served})
                self.assertFalse((self.om_dir / name).exists())
        self.assertFlashUnchanged()

    def test_final_refuses_a_firmware_that_differs_from_its_pin(self):
        pinned = self.served_files()
        pinned["payload/firmware.bin"] = self.fw[:-1] + bytes([self.fw[-1] ^ 1])
        self.setup_bundle(conf_files=pinned)
        final, _ = sl.build_final(self.cam.e0)
        self.start("final", self.action_set("final", {"env-final.img": final}), reboot=True)
        self.boot()
        self.assertInOrder(["phase2-start", "payload-mismatch-firmware", "idle-exit"], self.events("2"))
        self.assertNotIn("action-start-final", self.events("2"))  # the installer never ran
        self.assertEqual(self.srv.served.get("payload/firmware.bin"), 3)
        self.assertNotIn("payload/env-openipc.img", self.srv.served)
        self.assertEqual(self.srv.uploads, {})
        self.assertFalse((self.om_dir / "payload").exists())
        self.assertNotEqual(proc_state(self.dummy.pid), "T")
        self.assertFlashUnchanged()

    # ---- refusals and clean aborts on the camera ------------------------------------------------------------
    def probe_set(self) -> Path:
        return self.action_set("probe-arm", {"env-probe.img": sl.build_probe(self.cam.e0)[0]})

    def check_clean_stop(self, mode: str, reason: str):
        events = self.events()
        self.assertInOrder([f"action-start-{mode}", "stopped-clean", f"action-rc-{mode}-1", "idle-exit"], events)
        self.assertIn(reason, self.action_out())
        self.assertNotEqual(proc_state(self.dummy.pid), "T")  # resumed (or never stopped)
        self.assertEqual(self.flash_state(), "clean")
        self.assertFlashUnchanged()
        self.assertToolDiscipline()

    def test_write_and_reboot_phrases_gate_the_installer(self):
        self.setup_bundle()
        probe_set = self.probe_set()
        self.start("probe-arm", probe_set, reboot=True)
        real = self.srv.manifest
        self.srv.manifest = lambda: b"".join(line + b"\n" for line in real().splitlines()
                                             if not line.startswith((b"write=", b"reboot=")))
        self.boot()
        self.check_clean_stop("probe-arm", "write action without --allow-write and its confirm phrase")
        self.assertEqual(self.srv.served.get("action/env-probe.img"), 1)
        # With the write phrase but without the reboot phrase: armed, and no reboot is even attempted.
        self.start("probe-arm", probe_set, reboot=False)
        self.boot()
        self.assertInOrder(["action-start-probe-arm", "armed-probe-arm", "action-rc-probe-arm-0", "idle-exit"],
                           self.events())
        self.assertNotIn("rebooting-probe-arm", self.events())
        self.assertIn("reboot not authorized", self.action_out())
        self.assertEqual(self.cam.senv(), sl.build_probe(self.cam.e0)[0])

    def test_dirty_state_only_accepts_final_or_collect(self):
        self.setup_bundle()
        (self.om_dir / "stage").mkdir(parents=True)
        (self.om_dir / "stage" / "flash.state").write_text("dirty\n")
        self.start("probe-arm", self.probe_set(), reboot=True)
        self.run_phase1()  # no reboot: the RAM stage of the interrupted final survives
        self.assertEqual(self.events("2"), ["phase2-start", "refused-probe-arm-in-state-dirty",
                                            "hold-dirty-retry-final", "hold-dirty-retry-final"])
        self.assertEqual(self.srv.served, {rel: 1 for rel in sm.RUNTIME_FILES})
        self.assertFlashUnchanged()

    def test_backup_export_failure_keeps_stock(self):
        self.setup_bundle()
        final, _ = sl.build_final(self.cam.e0)
        self.start("final", self.action_set("final", {"env-final.img": final}), reboot=True)
        self.srv.fail_uploads = True
        self.boot()
        self.check_clean_stop("final", "host did not confirm the mdat1/mdat2 backups; nothing written")
        self.assertNotIn("ponr", self.events("installer"))
        self.assertEqual(self.srv.uploads, {})

    def test_watchdog_device_refuses_to_quiesce(self):
        watchdog = self.tmp / "watchdog"
        watchdog.write_bytes(b"")
        self.setup_bundle(overrides={"OM_WATCHDOG_DEV": watchdog})
        self.start("probe-arm", self.probe_set(), reboot=True)
        self.boot()
        self.check_clean_stop("probe-arm", "hook quiesce failed; nothing written")

    def test_watchdog_that_does_not_stop_keeps_stock(self):
        self.setup_bundle()
        self.wdt.set(mode="stuck")
        self.start("probe-arm", self.probe_set(), reboot=True)
        self.boot()
        self.check_clean_stop("probe-arm", "hook quiesce failed; nothing written")
        self.assertIn("the watchdog did not stop (ctrl 3)", self.action_out())
        self.assertWatchdogArmed()
        self.assertNotEqual(self.cam.senv(), sl.build_probe(self.cam.e0)[0])

    def test_unreadable_watchdog_keeps_stock(self):
        self.setup_bundle()
        self.wdt.set(mode="noread")
        self.start("final", self.action_set("final", {"env-final.img": sl.build_final(self.cam.e0)[0]}),
                   reboot=True)
        self.boot()
        self.check_clean_stop("final", "hook quiesce failed; nothing written")
        self.assertIn("hook: cannot read the watchdog", self.action_out())
        self.assertNotIn("ponr", self.events("installer"))
        self.assertEqual(sorted(self.srv.uploads), ["log-final"])  # no mdat backup was even taken
        self.assertEqual(self.wdt.get()["writes"], [])


if __name__ == "__main__":
    unittest.main()
