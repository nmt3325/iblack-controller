#!/usr/bin/env python3
"""End-to-end offline test of the online migration chain (loopback only: no camera, no real flash).

A MigrationServer on 127.0.0.1 drives the REAL camera/stage2.sh, migrate_hook.sh and installer:
  * phase1 runs under BusyBox sh with a PATH that offers only the applets of the
    stock BusyBox 1.18.4 (logging wrappers; dangerous ones are blocked);
  * phase2, the installer and the hook run under a stand-in for the OpenIPC
    runtime BusyBox (the host BusyBox behind a small wrapper);
  * the installer only touches the files of simulate_install.Sandbox (OM_SIM_DIR).
The real camera runtime (ARM musl BusyBox) is exercised separately under qemu by
validate_online_qemu.py.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import signal
import stat
import subprocess
import unittest
from pathlib import Path

import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import HOOK, INSTALLER, STAGE2, free_port, make_bundle, private_tmp, rmtree, sm, start_server
from simulate_install import Sandbox

BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
SLEEP = shutil.which("sleep")
# Applets of the camera's stock BusyBox 1.18.4 (stock rootfs); phase1 gets nothing else.
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
MDAT = {"mdat1": (0x540000, 0x20000), "mdat2": (0x560000, 0x20000)}
REGIONS = ("bootloader", "env", "firmware", "overlay")
RUN_TIMEOUT = 900
# Host BusyBox wget crashes on -T; the stand-ins drop "-T N" (the camera tools keep it).
STRIP_T = ('skip=\nfor a do\n  shift\n  if [ -n "$skip" ]; then skip=; continue; fi\n'
           '  if [ "$a" = -T ]; then skip=1; continue; fi\n  set -- "$@" "$a"\ndone\n')
RUNTIME_STANDIN = ("#!/bin/sh\n# Offline stand-in for the OpenIPC runtime BusyBox (host BusyBox).\n"
                   'if [ "${1:-}" = wget ]; then\nshift\n' + STRIP_T + f'exec {BUSYBOX} wget "$@"\nfi\n'
                   f'exec {BUSYBOX} "$@"\n')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def proc_state(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def host_applets() -> set:
    if not BUSYBOX:
        return set()
    r = subprocess.run([BUSYBOX, "--list"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    return set(r.stdout.split())


NEEDED = {"awk", "base64", "cat", "chmod", "cmp", "cp", "dd", "df", "expr", "grep", "head", "md5sum", "mkdir",
          "pidof", "rm", "rmdir", "sh", "sha1sum", "sha256sum", "sleep", "sync", "tail", "timeout", "tr", "true",
          "wc", "wget"}


@unittest.skipUnless(BUSYBOX and SLEEP and NEEDED <= host_applets(), "host BusyBox with the needed applets required")
class OnlineChain(unittest.TestCase):
    def setUp(self):
        self.tmp = private_tmp("om-e2e")
        self.addCleanup(rmtree, self.tmp)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.om_dir, self.app = self.tmp / "om", self.tmp / "app"
        self.stockbin, self.trace = self.tmp / "stockbin", self.tmp / "trace.log"
        self.app.mkdir()
        self.stockbin.mkdir()
        for name in STOCK_APPLETS:
            self.write_wrapper(name)
        self.dummy_name = "omq" + secrets.token_hex(4)  # stands in for cmsMain.out
        exe = self.tmp / self.dummy_name
        shutil.copy2(SLEEP, exe)
        self.dummy = subprocess.Popen([str(exe), "900"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.kill_dummy)
        self.srv = None
        self.addCleanup(self.stop_server)

    def write_wrapper(self, name: str) -> None:
        text = (f"#!{BUSYBOX} sh\n"
                f'case "${{OM_CONF:-}}" in "{self.om_dir}/"*) p=p2;; *) p=p1;; esac\n'
                f'echo "$p {name}" >> "{self.trace}"\n')
        if name in BLOCKED:
            text += "exit 126\n"
        elif name == "wget":
            text += STRIP_T + f'exec {BUSYBOX} wget "$@"\n'
        else:
            text += f'exec {BUSYBOX} {name} "$@"\n'
        path = self.stockbin / name
        path.write_text(text)
        os.chmod(path, 0o755)

    def kill_dummy(self):
        if self.dummy.poll() is None:
            self.dummy.kill()
        self.dummy.wait(timeout=30)

    def stop_server(self):
        if self.srv is not None:
            self.srv.stop()

    # ---- fixtures ---------------------------------------------------------------------------
    def served_files(self, env_mode="image") -> dict:
        files = {"runtime/busybox": RUNTIME_STANDIN.encode(),
                 "runtime/ld-musl.so": b"placeholder: the host stand-in needs no musl loader\n",
                 "runtime/openipc_migrate.sh": INSTALLER.read_bytes(), "runtime/migrate_hook.sh": HOOK.read_bytes(),
                 "payload/firmware.bin": self.sb.fw, "payload/uboot.bin": self.sb.ub}
        if env_mode == "image":
            files["payload/env.img"] = self.sb.env
        return files

    def setup_run(self, mode, *, env_mode="image", conf_files=None, conf_run_id=None, overrides=None, **server_kw):
        port = free_port()
        ov = {"OM_DIR": self.om_dir, "OM_LOCK": self.tmp / "lock", "OM_APP_DIR": self.app,
              "OM_POLL_INTERVAL": 1, "OM_POLL_MAX": 3, "OM_MIN_AVAIL_KB": 1, "OM_MIN_DIRFREE_KB": 1,
              "OM_RETRY_SLEEP": 1, "OM_HOLD_MAX": 1, "OM_STOCK_BB": BUSYBOX, "OM_SIM_DIR": self.sb.dev,
              "OM_SIM_MTD_PROC": self.sb.proc_mtd, "OM_QUIESCE_PROCS": self.dummy_name,
              "OM_WATCHDOG_DEV": self.tmp / "no-watchdog"}
        ov.update(overrides or {})
        self.run_id, conf = make_bundle(self.tmp / "bundle", self.served_files(env_mode), env_mode=env_mode,
                                        host="127.0.0.1", port=port, overrides=ov, allow_test_keys=True,
                                        conf_files=conf_files)
        if conf_run_id:
            conf = conf.replace(f"OM_RUN_ID='{self.run_id}'", f"OM_RUN_ID='{conf_run_id}'")
        (self.app / "stage2.sh").write_bytes(STAGE2.read_bytes())
        os.chmod(self.app / "stage2.sh", 0o755)
        (self.app / "migrate.conf").write_text(conf)
        if mode in ("rehearse", "write"):
            server_kw.setdefault("backup_dir", self.tmp / "backups")
        if mode == "write":
            server_kw.setdefault("write_confirm", sm.WRITE_PHRASE)
        self.srv, self.console = start_server(self.tmp / "bundle", mode, port=port, **server_kw)

    def run_phase1(self) -> str:
        """Start stage2.sh the way the patched runapp.sh does and wait for the whole chain to end."""
        env = {"PATH": str(self.stockbin), "OM_CONF": str(self.app / "migrate.conf")}
        proc = subprocess.Popen([BUSYBOX, "sh", str(self.app / "stage2.sh"), "phase1"], env=env, cwd=self.tmp,
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

    # ---- observations -------------------------------------------------------------------------
    def events(self, phase=None):
        return [e for _, p, e in self.srv.events if phase is None or p == phase]

    def pairs(self):
        return [(p, e) for _, p, e in self.srv.events]

    def traced(self, phase: str) -> list:
        if not self.trace.exists():
            return []
        return [line.split()[1] for line in self.trace.read_text().splitlines() if line.startswith(phase + " ")]

    def assertFlashUnchanged(self):
        self.assertEqual(sha(self.sb.read_flash()), sha(self.sb.original))

    def assertBackupsExported(self):
        want = {n: sha(self.sb.original[o:o + s]) for n, (o, s) in MDAT.items()}
        self.assertEqual(self.srv.backups, want)
        for n, (o, s) in MDAT.items():
            self.assertIn(b"\0", self.sb.original[o:o + s])  # NUL bytes survive (base64 transport)
            saved = list((self.tmp / "backups").glob(f"{n}-*"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_bytes(), self.sb.original[o:o + s])
            self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)

    def assertToolDiscipline(self):
        self.assertEqual(self.traced("p2"), [], "phase2/installer/hook must only use the RAM BusyBox")
        used = set(self.traced("p1"))
        self.assertFalse(used & BLOCKED, used)
        self.assertTrue(used <= set(STOCK_APPLETS), used)

    def prefix(self, mode="write"):
        return [("1", "go-received"), ("1", "runtime-ready"), ("2", "phase2-start"), ("2", "payloads-verified"),
                ("2", f"installer-start-{mode}")]

    # ---- the happy paths ----------------------------------------------------------------------
    def check_write(self, env_mode):
        self.setup_run("write", env_mode=env_mode, reboot_confirm=sm.REBOOT_PHRASE)
        self.run_phase1()
        self.assertEqual(sha(self.sb.read_flash()), sha(self.sb.expected_after(env_mode)))
        installer = [("installer", "writing-start")]
        for r in REGIONS:
            installer += [("installer", f"region-begin-{r}"), ("installer", f"region-done-{r}")]
        self.assertEqual(self.pairs(), self.prefix() + installer + [("installer", "verified"),
                                                                    ("2", "installer-rc-0"), ("2", "migrated")])
        self.assertBackupsExported()
        self.assertEqual(self.srv.served, {rel: 1 for rel in self.srv.bundle.files})
        self.assertEqual(len(self.srv.reports), 1)
        report = self.srv.reports[0].decode()
        self.assertIn("all regions written and verified in order: bootloader env firmware overlay", report)
        self.assertIn("Reboot withheld", report)  # the simulation never reboots, even with the phrase
        self.assertEqual(proc_state(self.dummy.pid), "T")  # stock writers stay stopped on migrated flash
        self.assertFalse((self.om_dir / "payload").exists())
        self.assertFalse(list(self.om_dir.glob("dl.*")))
        self.assertToolDiscipline()
        self.assertTrue({"wget", "sha1sum", "md5sum", "expr"} <= set(self.traced("p1")))
        self.assertNotIn(self.run_id, self.console.getvalue())

    def test_write_with_env_image(self):
        self.check_write("image")

    def test_write_with_env_erase(self):
        self.check_write("erase")

    def test_rehearsal_exports_backups_and_writes_nothing(self):
        self.setup_run("rehearse")
        self.run_phase1()
        self.assertFlashUnchanged()
        self.assertEqual(self.pairs(), self.prefix("rehearsal") + [("2", "installer-rc-0"), ("2", "rehearsal-ok")])
        self.assertBackupsExported()
        self.assertIn("DRY RUN complete: nothing was written", self.srv.reports[0].decode())
        self.assertNotEqual(proc_state(self.dummy.pid), "T")  # resumed after the rehearsal
        self.assertToolDiscipline()

    # ---- stays inert --------------------------------------------------------------------------
    def test_standby_times_out_and_the_lock_blocks_a_second_start(self):
        self.setup_run("standby")
        self.run_phase1()
        self.assertEqual(self.events(), ["standby"] * 3 + ["standby-timeout"])
        self.assertEqual(self.srv.served, {})
        self.assertEqual(sorted(p.name for p in self.om_dir.iterdir()), ["manifest", "stage2.log"])
        before = len(self.srv.events)
        self.run_phase1()
        self.assertEqual(len(self.srv.events), before)  # second start ends at the lock directory
        self.assertFlashUnchanged()
        self.assertToolDiscipline()

    def test_abort(self):
        self.setup_run("abort")
        self.run_phase1()
        self.assertEqual(self.events(), ["aborted"])
        self.assertEqual(self.srv.served, {})

    def test_foreign_run_id_gets_nothing_even_from_a_write_server(self):
        self.setup_run("write", conf_run_id=secrets.token_hex(16))
        self.run_phase1()
        self.assertEqual(self.srv.events, [])
        self.assertEqual(self.srv.refused, 4)  # three manifest polls + the standby-timeout report
        self.assertEqual(self.srv.served, {})
        self.assertFlashUnchanged()

    def test_go_without_the_phrase_is_ignored(self):
        self.setup_run("rehearse")
        self.srv.manifest = lambda: f"run={self.run_id}\nmode=go\n".encode()
        self.run_phase1()
        self.assertEqual(self.events(), ["go-phrase-missing"] * 3 + ["standby-timeout"])
        self.assertEqual(self.srv.served, {})

    def test_low_memory_stops_before_any_download(self):
        self.setup_run("write", overrides={"OM_MIN_AVAIL_KB": 999999999})
        self.run_phase1()
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertRegex(events[0], r"^lowmem-[0-9]+-[0-9]+$")
        self.assertEqual(self.srv.served, {})

    # ---- verification failures ------------------------------------------------------------------
    def test_runtime_pin_mismatch_stops_phase1(self):
        pinned = self.served_files()
        pinned["runtime/busybox"] += b"# the reviewed runtime differs\n"
        self.setup_run("write", conf_files=pinned)
        self.run_phase1()
        self.assertEqual(self.events(), ["go-received", "hash-mismatch-busybox"])
        self.assertEqual(self.srv.served, {"runtime/busybox": 1})
        self.assertFalse((self.om_dir / "busybox").exists())
        self.assertFlashUnchanged()

    def test_payload_pin_mismatch_stops_before_the_installer(self):
        pinned = self.served_files()
        pinned["payload/firmware.bin"] = self.sb.fw[:-1] + bytes([self.sb.fw[-1] ^ 1])
        self.setup_run("write", conf_files=pinned)
        self.run_phase1()
        self.assertEqual(self.events("2"), ["phase2-start", "payload-mismatch-firmware"])
        self.assertEqual(self.srv.served.get("payload/firmware.bin"), 3)
        self.assertNotIn("payload/uboot.bin", self.srv.served)
        self.assertFalse((self.om_dir / "payload").exists())
        self.assertEqual(self.srv.backups, {})
        self.assertFlashUnchanged()

    def test_go_withdrawn_before_the_installer(self):
        self.setup_run("write")
        real, calls = self.srv.manifest, []

        def manifest():
            calls.append(1)
            return real() if len(calls) == 1 else f"run={self.run_id}\nmode=standby\n".encode()
        self.srv.manifest = manifest
        self.run_phase1()
        self.assertEqual(self.pairs(), self.prefix()[:4] + [("2", "go-withdrawn")])
        self.assertEqual(self.srv.backups, {})
        self.assertNotEqual(proc_state(self.dummy.pid), "T")
        self.assertFlashUnchanged()

    # ---- installer aborts before writing ---------------------------------------------------------
    def check_clean_abort(self, reason):
        self.run_phase1()
        self.assertEqual(self.pairs(), self.prefix() + [("installer", "abort-clean"), ("2", "installer-rc-1"),
                                                        ("2", "stopped-rc-1")])
        self.assertEqual(self.srv.backups, {})
        self.assertIn(reason, self.srv.reports[0].decode())
        self.assertNotEqual(proc_state(self.dummy.pid), "T")  # resumed
        self.assertFlashUnchanged()
        self.assertToolDiscipline()

    def test_backup_export_failure_aborts_cleanly(self):
        self.setup_run("write")
        self.srv.fail_uploads = True
        self.check_clean_abort("hook 'export' failed")

    def test_watchdog_device_aborts_cleanly(self):
        watchdog = self.tmp / "watchdog"
        watchdog.write_bytes(b"")
        self.setup_run("write", overrides={"OM_WATCHDOG_DEV": watchdog})
        self.check_clean_abort("hook 'quiesce' failed")


if __name__ == "__main__":
    unittest.main()
