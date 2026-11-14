#!/usr/bin/env python3
"""Exercise the online-migration pieces with the REAL ARM binaries under qemu-arm (offline, loopback only).

Never contacts a camera and never opens /dev/mtd*: the installer only writes the
files of tests/simulate_install.Sandbox and the hook talks to a MigrationServer
bound to 127.0.0.1. Checks:
  runtime_patch          the bundle BusyBox differs from the pinned OpenIPC one only in PT_INTERP
  runtime_on_stock_root  patched musl BusyBox runs next to the stock uClibc rootfs with its loader
                         at /dev/om/ld-musl.so; the unpatched one cannot start there
  shells_parse           stock BusyBox 1.18.4 sh parses stage2.sh, runapp.sh and migrate.conf;
                         the OpenIPC sh parses stage2.sh, the installer and the hook
  stock_applets          the stock applet list equals the one the x86 E2E test allows in phase1
  phase1_standby_stock   phase1 under the stock sh and stock applets polls and gives up
  phase1_go_stock        phase1 under the stock tools downloads the real runtime, verifies it with
                         stock sha1sum + md5sum and installs it; it then cannot exec the ARM runtime
                         here (no binfmt), which is the expected end of this check
  installer_write_sim    installer + hook under the OpenIPC sh/BusyBox: quiesce, stability check,
                         base64 backup export, simulated writes of all four regions, final sweep
  installer_rehearsal    the same read-only: backups exported, nothing written, app resumed
  full_chain_write       the whole camera side in one run: stock-sh phase1 polls, gets GO, downloads
                         and pins the runtime, hands over to phase2 under the OpenIPC sh, which
                         fetches and pins the payloads, re-reads the manifest and runs the installer
                         + hook in write mode against the Sandbox files (served "busybox" is a qemu
                         launcher of the real patched BusyBox so that the x86 host can exec it)
  raw_post_truncates     OpenIPC wget --post-file stops at the first NUL (why backups use base64)

Inputs (private, never committed): --bundle (from build_migration_appfs.py),
--stock-rootfs (extracted stock rootfs tree), --qemu-arm (+ --qemu-lib-path),
--unsquashfs. Writes a sanitized JSON summary (no run id, host, env or digests
of backups) to --out-json and exits 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import build_migration_appfs as bma  # noqa: E402
import serve_migration as sm  # noqa: E402
from simulate_install import Sandbox  # noqa: E402
from test_e2e_online import BLOCKED, STOCK_APPLETS  # noqa: E402

SCHEMA = "iblack-openipc-migrate-online-qemu/1"
STOCK_DIRS = ("bin", "sbin", "lib", "usr")  # no proc/dev/sys: qemu -L would shadow the host ones
MDAT = {"mdat1": (0x540000, 0x20000), "mdat2": (0x560000, 0x20000)}
REGIONS = ("bootloader", "env", "firmware", "overlay")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def proc_state(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]


def write_exec(path: Path, text: str) -> Path:
    path.write_text(text)
    os.chmod(path, 0o755)
    return path


class Lab:
    def __init__(self, args):
        self.args = args
        self.tmp = Path("/dev/shm" if os.access("/dev/shm", os.W_OK) else "/tmp") / f"om-qemu-{secrets.token_hex(6)}"
        self.tmp.mkdir(mode=0o700)
        self.bundle = sm.load_bundle(args.bundle)
        self.files = {rel: f.data for rel, f in self.bundle.files.items()}
        self.results = {}
        self.srv = None

    # ---- set-up ----------------------------------------------------------------------------------
    def setup(self):
        a = self.args
        self.stockq = self.tmp / "stockq"
        self.stockq.mkdir()
        for d in STOCK_DIRS:
            if (a.stock_rootfs / d).exists():
                shutil.copytree(a.stock_rootfs / d, self.stockq / d, symlinks=True)
        for p in self.stockq.rglob("*"):
            if p.is_file() and not p.is_symlink():
                os.chmod(p, os.stat(p).st_mode | 0o500)
        self.rtq = self.tmp / "rtq"
        shutil.copytree(self.stockq, self.rtq, symlinks=True)
        (self.rtq / "dev" / "om").mkdir(parents=True)
        write_exec(self.rtq / "dev/om/busybox", "")
        (self.rtq / "dev/om/busybox").write_bytes(self.files["runtime/busybox"])
        write_exec(self.rtq / "dev/om/ld-musl.so", "")
        (self.rtq / "dev/om/ld-musl.so").write_bytes(self.files["runtime/ld-musl.so"])
        lib = f"LD_LIBRARY_PATH='{a.qemu_lib_path}' " if a.qemu_lib_path else ""
        self.qw = write_exec(self.tmp / "qemu-arm.sh",
                             f"#!/bin/sh\n{lib}exec '{a.qemu_arm}' -U LD_LIBRARY_PATH -cpu arm926 \"$@\"\n")
        self.stock_bb = [str(self.qw), "-L", str(self.stockq), str(self.stockq / "bin/busybox")]
        self.rt_bb = [str(self.qw), "-L", str(self.rtq), str(self.rtq / "dev/om/busybox")]
        self.bbw = write_exec(self.tmp / "rt-busybox",
                              f"#!/bin/sh\nexec {' '.join(self.rt_bb)} \"$@\"\n")
        self.stock_list = self.run(self.stock_bb + ["--list"]).stdout.split()
        self.stockbin = self.tmp / "stockbin"
        self.stockbin.mkdir()
        for name in self.stock_list:
            body = "exit 126\n" if name in BLOCKED else f"exec {' '.join(self.stock_bb)} {name} \"$@\"\n"
            write_exec(self.stockbin / name, "#!/bin/sh\n" + body)
        self.port, self.run_id = free_port(), secrets.token_hex(16)
        self.lab_bundle = self.make_lab_bundle(self.files, "real")

    def make_lab_bundle(self, files: dict, tag: str) -> Path:
        """A loopback copy of the bundle with our own port/run id (pins computed from `files`)."""
        stage2 = bma.STAGE2.read_bytes()
        own = bma.render_conf(bma.conf_values("127.0.0.1", self.port, self.run_id, files, stage2,
                                              self.bundle.env_mode))
        return bma.assemble_bundle(self.tmp / f"bundle-{tag}", files, own, host="127.0.0.1", port=self.port,
                                   run_id=self.run_id, env_mode=self.bundle.env_mode, stage2=stage2,
                                   appfs=b"placeholder")

    def start_dummy(self):
        """A stand-in for the stock app: a sleeping process with a unique name for the hook to SIGSTOP."""
        name = "omq" + secrets.token_hex(4)
        exe = self.tmp / name
        shutil.copy2(shutil.which("sleep"), exe)
        return name, subprocess.Popen([str(exe), "3000"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True)

    def run(self, cmd, env=None, timeout=600, **kw):
        env = {"PATH": "/usr/bin:/bin", **(env or {})}
        return subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=self.tmp, **kw)

    def serve(self, mode, bundle=None, **kw):
        self.stop()
        if mode in ("rehearse", "write"):
            kw.setdefault("backup_dir", self.tmp / f"backups-{secrets.token_hex(3)}")
        if mode == "write":
            kw.setdefault("write_confirm", sm.WRITE_PHRASE)
        self.srv = sm.MigrationServer(sm.load_bundle(bundle or self.lab_bundle), mode, bind="127.0.0.1",
                                      port=self.port, out=io.StringIO(), **kw).start()
        return self.srv

    def stop(self):
        if self.srv is not None:
            self.srv.stop()
            self.srv = None

    def camera_conf(self, name: str, files=None, **over) -> Path:
        app = self.tmp / f"app-{name}"
        app.mkdir()
        (app / "stage2.sh").write_bytes(bma.STAGE2.read_bytes())
        os.chmod(app / "stage2.sh", 0o755)
        ov = {"OM_DIR": self.tmp / f"om-{name}", "OM_LOCK": self.tmp / f"lock-{name}", "OM_APP_DIR": app,
              "OM_POLL_INTERVAL": 1, "OM_POLL_MAX": 2, "OM_MIN_AVAIL_KB": 1, "OM_MIN_DIRFREE_KB": 1,
              "OM_RETRY_SLEEP": 1, "OM_HOLD_MAX": 1, **over}
        values = bma.conf_values("127.0.0.1", self.port, self.run_id, files or self.files, bma.STAGE2.read_bytes(),
                                 self.bundle.env_mode, ov)
        (app / "migrate.conf").write_text(bma.render_conf(values, allow_test_keys=True))
        return app

    def record(self, name, ok, **details):
        self.results[name] = {"result": "PASS" if ok else "FAIL", **details}
        print(f"[qemu-validate] {name}: {'PASS' if ok else 'FAIL'}", flush=True)

    # ---- checks ----------------------------------------------------------------------------------
    def check_runtime_patch(self):
        patched = self.files["runtime/busybox"]
        restored = bma.patch_interp(patched, old=bma.NEW_INTERP, new=bma.OLD_INTERP)
        changed = sum(1 for x, y in zip(patched, restored) if x != y)
        self.restored = restored
        self.record("runtime_patch", sha(restored) == bma.RUNTIME_BUSYBOX_SHA256 and len(patched) == len(restored),
                    patched_runtime_sha256=sha(patched), pinned_openipc_busybox_sha256=bma.RUNTIME_BUSYBOX_SHA256,
                    bytes_changed=changed, interp=bma.NEW_INTERP.decode())

    def check_runtime_on_stock_root(self):
        true_rc = self.run(self.rt_bb + ["true"]).returncode
        applets = self.run(self.rt_bb + ["--list"]).stdout.split()
        missing = [x for x in bma.RUNTIME_APPLETS if x not in applets]
        arith = self.run(self.rt_bb + ["sh", "-c", "echo $((6*7))"]).stdout.strip()
        sample = self.tmp / "sample.bin"
        sample.write_bytes(b"\0" + random.Random(7).randbytes(200000) + b"\0")
        digest = self.run(self.rt_bb + ["sha256sum", sample]).stdout.split()[:1]
        orig = self.rtq / "dev/om/busybox.unpatched"
        orig.write_bytes(self.restored)
        os.chmod(orig, 0o755)
        unpatched = self.run([self.qw, "-L", self.rtq, orig, "true"])
        no_musl_loader = not (self.stockq / "lib" / bma.OLD_INTERP.decode().rsplit("/", 1)[1]).exists()
        ok = (true_rc == 0 and not missing and arith == "42" and digest == [sha(sample.read_bytes())]
              and unpatched.returncode != 0 and no_musl_loader)
        self.record("runtime_on_stock_root", ok, true_rc=true_rc, runtime_applets=len(applets),
                    required_applets_missing=missing, arithmetic=arith, sha256sum_matches=digest == [sha(
                        sample.read_bytes())], unpatched_rc=unpatched.returncode,
                    stock_rootfs_has_musl_loader=not no_musl_loader)

    def check_shells_parse(self):
        img = self.tmp / "appfs.sqfs"
        shutil.copyfile(Path(self.args.bundle) / "appfs" / "migration-appfs.squashfs", img)
        runapp = self.tmp / "runapp.sh"
        cat = subprocess.run([str(self.args.unsquashfs), "-cat", str(img), "runapp.sh"], capture_output=True,
                             timeout=300, stdin=subprocess.DEVNULL, check=True)
        runapp.write_bytes(cat.stdout)
        img.unlink()
        conf = Path(self.args.bundle) / "conf" / "migrate.conf"
        rcs = {
            "stock_sh_n_stage2": self.run(self.stock_bb + ["sh", "-n", bma.STAGE2]).returncode,
            "stock_sh_n_runapp": self.run(self.stock_bb + ["sh", "-n", runapp]).returncode,
            "stock_sh_n_conf": self.run(self.stock_bb + ["sh", "-n", conf]).returncode,
            "openipc_sh_n_stage2": self.run(self.rt_bb + ["sh", "-n", bma.STAGE2]).returncode,
            "openipc_sh_n_installer": self.run(self.rt_bb + ["sh", "-n", bma.INSTALLER]).returncode,
            "openipc_sh_n_hook": self.run(self.rt_bb + ["sh", "-n", bma.HOOK]).returncode,
        }
        sourced = self.run(self.stock_bb + ["sh", "-c", '. "$1" && [ -n "$OM_RUN_ID" ] && echo "$OM_PORT $OM_DIR"',
                                            "sh", conf]).stdout.strip()
        launcher = bma.LAUNCHER.format(delay=45).encode()
        ok = (all(rc == 0 for rc in rcs.values()) and sourced == f"{self.bundle.port} {bma.OM_DIR}"
              and runapp.read_bytes().endswith(launcher))
        self.record("shells_parse", ok, **rcs, stock_sh_sources_conf=sourced == f"{self.bundle.port} {bma.OM_DIR}",
                    runapp_ends_with_launcher=runapp.read_bytes().endswith(launcher))

    def check_stock_applets(self):
        lst = set(self.stock_list)
        ok = lst == set(STOCK_APPLETS) and not (lst & set(bma.NON_STOCK_WORDS))
        self.record("stock_applets", ok, stock_applets=len(lst), equals_e2e_list=lst == set(STOCK_APPLETS),
                    non_stock_words_present=sorted(lst & set(bma.NON_STOCK_WORDS)))

    def run_phase1(self, app: Path, timeout=1200):
        env = {"PATH": str(self.stockbin), "OM_CONF": str(app / "migrate.conf")}
        t0 = time.monotonic()
        r = self.run(self.stock_bb + ["sh", app / "stage2.sh", "phase1"], env=env, timeout=timeout)
        return r, round(time.monotonic() - t0, 1)

    def events(self, phase=None):
        return [e for _, p, e in self.srv.events if phase is None or p == phase]

    def check_phase1_standby(self):
        app = self.camera_conf("standby")
        self.serve("standby")
        r, secs = self.run_phase1(app)
        events = self.events()
        ok = r.returncode == 0 and events == ["standby", "standby", "standby-timeout"] and not self.srv.served
        self.record("phase1_standby_stock", ok, rc=r.returncode, events=events, seconds=secs)

    def check_phase1_go(self):
        app = self.camera_conf("go", OM_STOCK_BB="/bin/busybox")
        self.serve("rehearse")
        r, secs = self.run_phase1(app)
        om = self.tmp / "om-go"
        names = ("busybox", "ld-musl.so", "openipc_migrate.sh", "migrate_hook.sh")
        installed = {n: (om / n).exists() and sha((om / n).read_bytes()) == sha(self.files[f"runtime/{n}"])
                     for n in names}
        executable = all(os.access(om / n, os.X_OK) for n in names if (om / n).exists())
        events = self.events()
        ok = (r.returncode == 0 and events == ["go-received", "runtime-exec-failed"] and all(installed.values())
              and executable and self.srv.served == {f"runtime/{n}": 1 for n in names}
              and not list(om.glob("dl.*")))
        self.record("phase1_go_stock", ok, rc=r.returncode, events=events, installed_identical=installed,
                    installed_executable=executable, served=dict(self.srv.served), seconds=secs,
                    note="the ARM runtime cannot be exec'd on this x86 host without binfmt_misc")

    def installer(self, mode: str):
        sb = Sandbox()
        name, dummy = self.start_dummy()
        om = self.tmp / f"om-inst-{mode}"
        om.mkdir()
        try:
            srv = self.serve("write" if mode == "write" else "rehearse")
            env = {"MTD_PROC": sb.proc_mtd, "OM_BB": str(self.bbw), "OM_HOST": "127.0.0.1", "OM_PORT": str(self.port),
                   "OM_RUN_ID": self.run_id, "OM_DIR": str(om), "OM_QUIESCE_PROCS": name,
                   "OM_WATCHDOG_DEV": str(self.tmp / "no-watchdog")}
            cmd = self.rt_bb + ["sh", bma.INSTALLER, "--busybox", self.bbw, "--hook", bma.HOOK,
                                "--firmware", sb.fw_path, "--firmware-sha256", sha(sb.fw),
                                "--uboot", sb.ub_path, "--uboot-sha256", sha(sb.ub),
                                "--env-image", sb.env_path, "--env-sha256", sha(sb.env),
                                "--stage", om / "stage", "--stability-reads", "2"]
            if mode == "write":
                cmd += ["--dry-run-dir", sb.dev]
            else:
                env["MTD_DEV_DIR"] = sb.dev
            t0 = time.monotonic()
            r = self.run(cmd, env=env, timeout=5400)
            secs = round(time.monotonic() - t0, 1)
            flash = sha(sb.read_flash())
            want_backups = {n: sha(sb.original[o:o + s]) for n, (o, s) in MDAT.items()}
            nul_in_backups = all(b"\0" in sb.original[o:o + s] for o, s in MDAT.values())
            state = proc_state(dummy.pid)
            return r, secs, flash, sb, srv, want_backups, nul_in_backups, state
        finally:
            dummy.kill()
            dummy.wait(timeout=30)
            sb.cleanup()

    def check_installer_write(self):
        r, secs, flash, sb, srv, want, nul, state = self.installer("write")
        expected = ["writing-start"] + [f"region-{k}-{x}" for x in REGIONS for k in ("begin", "done")] + ["verified"]
        events = self.events("installer")
        ok = (r.returncode == 0 and flash == sha(sb.expected_after("image")) and srv.backups == want and nul
              and events == expected and state == "T")
        self.record("installer_write_sim", ok, rc=r.returncode, flash_matches_expected=flash == sha(
            sb.expected_after("image")), backups_confirmed=srv.backups == want, backups_contain_nul=nul,
            installer_events=events, stock_app_state_after=state, seconds=secs,
            log_tail=[ln for ln in r.stdout.splitlines() if ln.startswith("[migrate]")][-3:])

    def check_installer_rehearsal(self):
        r, secs, flash, sb, srv, want, nul, state = self.installer("rehearsal")
        ok = r.returncode == 0 and flash == sha(sb.original) and srv.backups == want and state != "T"
        self.record("installer_rehearsal", ok, rc=r.returncode, flash_unchanged=flash == sha(sb.original),
                    backups_confirmed=srv.backups == want, stock_app_state_after=state, seconds=secs,
                    installer_events=self.events("installer"))

    def check_full_chain_write(self):
        sb = Sandbox()
        name, dummy = self.start_dummy()
        try:
            files = dict(self.files)
            files.update({"runtime/busybox": self.bbw.read_bytes(), "payload/firmware.bin": sb.fw,
                          "payload/uboot.bin": sb.ub})
            if self.bundle.env_mode == "image":
                files["payload/env.img"] = sb.env
            app = self.camera_conf("chain", files=files, OM_STOCK_BB="/bin/busybox", OM_SIM_DIR=sb.dev,
                                   OM_SIM_MTD_PROC=sb.proc_mtd, OM_QUIESCE_PROCS=name,
                                   OM_WATCHDOG_DEV=self.tmp / "no-watchdog")
            srv = self.serve("write", bundle=self.make_lab_bundle(files, "chain"))
            r, secs = self.run_phase1(app, timeout=5400)
            flash_ok = sb.read_flash() == sb.expected_after(self.bundle.env_mode)
            want = {n: sha(sb.original[o:o + s]) for n, (o, s) in MDAT.items()}
            state = proc_state(dummy.pid)
        finally:
            dummy.kill()
            dummy.wait(timeout=30)
            sb.cleanup()
        inst = ["writing-start"] + [f"region-{k}-{x}" for x in REGIONS for k in ("begin", "done")] + ["verified"]
        p2 = ["phase2-start", "payloads-verified", "installer-start-write", "installer-rc-0", "migrated"]
        ev1, ev2, evi = self.events("1"), self.events("2"), self.events("installer")
        ok = (r.returncode == 0 and ev1 == ["go-received", "runtime-ready"] and ev2 == p2 and evi == inst
              and flash_ok and srv.backups == want and state == "T" and srv.served == {rel: 1 for rel in files}
              and len(srv.reports) == 1 and srv.refused == 0)
        self.record("full_chain_write", ok, rc=r.returncode, phase1_events=ev1, phase2_events=ev2,
                    installer_events=evi, flash_matches_expected=flash_ok, backups_confirmed=srv.backups == want,
                    stock_app_state_after=state, served=dict(srv.served), log_reports=len(srv.reports),
                    refused=srv.refused, seconds=secs,
                    note="phase1 ran under the stock sh/applets, phase2, installer and hook under the OpenIPC sh")

    def check_raw_post(self):
        self.serve("rehearse")
        raw = self.tmp / "raw.bin"
        raw.write_bytes(b"A" * 1000 + b"\0" + random.Random(9).randbytes(sm.MDAT_BYTES - 1001))
        url = f"http://127.0.0.1:{self.port}/report?run={self.run_id}"
        r = self.run([self.bbw, "wget", "-q", "-T", "30", "-O", "/dev/null", "--post-file", raw, url])
        got = len(self.srv.reports[-1]) if self.srv.reports else None
        self.record("raw_post_truncates", r.returncode == 0 and got == 1000, rc=r.returncode,
                    file_bytes=sm.MDAT_BYTES, received_bytes=got)

    def close(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--stock-rootfs", type=Path, required=True)
    ap.add_argument("--qemu-arm", required=True)
    ap.add_argument("--qemu-lib-path", default="", help="LD_LIBRARY_PATH for a non-static qemu-arm")
    ap.add_argument("--unsquashfs", required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="run a subset of the checks")
    args = ap.parse_args(argv)
    if sm.inside_repo(args.bundle):
        print("validate_online_qemu: the bundle must stay outside the repository", file=sys.stderr)
        return 2
    lab = Lab(args)
    checks = ["runtime_patch", "runtime_on_stock_root", "shells_parse", "stock_applets", "phase1_standby",
              "phase1_go", "installer_write", "installer_rehearsal", "full_chain_write", "raw_post"]
    started = time.time()
    try:
        lab.setup()
        for name in checks:
            if args.only and name not in args.only and name != "runtime_patch":
                continue
            try:
                getattr(lab, f"check_{name}")()
            except Exception as exc:  # recorded as a failure, the remaining checks still run
                lab.record(name, False, error=f"{exc.__class__.__name__}: {str(exc)[:200]}")
    finally:
        lab.close()
    summary = {
        "schema": SCHEMA, "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "camera_contacted": False, "mtd_devices_opened": False, "network": "127.0.0.1 only",
        "emulator": "qemu-arm user mode, -cpu arm926",
        "result": "PASS" if lab.results and all(r["result"] == "PASS" for r in lab.results.values()) else "FAIL",
        "inputs": {"stage2_sha256": sha(bma.STAGE2.read_bytes()), "installer_sha256": sha(bma.INSTALLER.read_bytes()),
                   "hook_sha256": sha(bma.HOOK.read_bytes()), "env_mode": lab.bundle.env_mode},
        "checks": lab.results,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": summary["result"], "checks": {k: v["result"] for k, v in lab.results.items()}}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
