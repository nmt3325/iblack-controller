#!/usr/bin/env python3
"""Exercise the stock-U-Boot online migration with the REAL camera binaries under qemu-arm (offline).

Never contacts a camera and never opens /dev/mtd*: flash access goes to the simulated partitions of
tests/sb_fixture.Camera, HTTP only to 127.0.0.1 (a MigrationServer, and a capture server for the stock wget
--post-data probe), and every mount happens inside an unprivileged user+mount namespace. Checks:
  runtime_patch          the bundle BusyBox differs from the pinned OpenIPC one only in PT_INTERP
  runtime_on_stock_root  the patched musl BusyBox starts next to the stock uClibc rootfs with its loader at
                         /dev/om/ld-musl.so (the unpatched one cannot), and its crc32, od, tail, base64 and
                         sha256sum give exactly the values om_stockboot.sh and the hook rely on
  shells_parse           stock BusyBox 1.18.4 sh parses stage2.sh, the patched runapp.sh, migrate.conf,
                         trial.sh and trial_dhcp.sh; the OpenIPC sh parses stage2.sh, om_stockboot.sh and the
                         hook; runapp.sh carries the trial branch before the stock app and ends with the agent
  stock_applets          the stock applet list equals the one the E2E test allows in phase1 and trial.sh; the
                         stock dd fails on operands beyond if/of/bs/count/skip/seek and grep on -x, the stock sh's
                         "1<>" lays a log over the tl template (trial.sh's checkpoint) without truncating it, the
                         stock wget --post-data form-escapes the log exactly as tests/om_testlib.py models it and
                         serve_migration.py decodes that body back to the log, and the options trial.sh gives the
                         applets the E2E test stubs (udhcpc, wget, mount) parse -- in empty user namespaces
  etc_overlay_mount      the /etc overlay of trial.sh (cp -a, mkdir, cp, mount -o bind) with the stock applets
  runapp_trial_branch    tests/test_build_migration_appfs.py runapp cases with the stock sh as an extra shell:
                         normal boots fall through, a trial boot execs trial.sh, a missing payload reboots
  e2e_real_binaries      tests/test_e2e_online.py with the stock BusyBox for phase1 and trial.sh and the
                         patched OpenIPC BusyBox for phase2, om_stockboot.sh and the hook: diagnose,
                         probe-arm, collect, trial-arm, the trial boot, collect, final, and the refusal cases

Inputs (private, never committed): --bundle (from build_migration_appfs.py), --stock-rootfs (extracted
stock rootfs tree), --qemu-arm (+ --qemu-lib-path), --unsquashfs, --mksquashfs. Writes a sanitized JSON
summary (no run id, host, env, payload or backup digests, no logs) to --out-json; exit 0 only if every check
passed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import os
import random
import re
import secrets
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import build_migration_appfs as bma  # noqa: E402
import serve_migration as sm  # noqa: E402
import stock_layout as sl  # noqa: E402
import test_e2e_online as e2e  # noqa: E402  (only for its applet lists)
from om_testlib import stock_wget_form  # noqa: E402

SCHEMA = "iblack-openipc-migrate-online-qemu/2"
STOCK_DIRS = ("bin", "sbin", "lib", "usr")  # no proc/dev/sys/etc: qemu -L would shadow the host ones
TRIAL = HERE / "camera" / "trial.sh"
TRIAL_DHCP = HERE / "camera" / "trial_dhcp.sh"
# Runs a unittest module (optionally only tests whose name contains one of the patterns) and prints one
# JSON line with result and duration per test; failure details go to stderr, never into the summary.
DRIVER = r'''
import json, sys, time, unittest
mod, pats = sys.argv[1], sys.argv[2:]
def flat(s):
    for t in s:
        yield from (flat(t) if isinstance(t, unittest.TestSuite) else [t])
tests = [t for t in flat(unittest.defaultTestLoader.loadTestsFromName(mod))
         if not pats or any(p in t.id().rsplit(".", 1)[1] for p in pats)]
res = {}
class Result(unittest.TestResult):
    def startTest(self, t):
        self.t0 = time.monotonic(); super().startTest(t)
        print("[qemu-validate]   start " + t.id().rsplit(".", 1)[1], file=sys.stderr, flush=True)
    def rec(self, t, r, info=""):
        res[t.id().rsplit(".", 1)[1]] = {"result": r, "seconds": round(time.monotonic() - self.t0, 1)}
        if info:
            print(f"[qemu-validate] {t.id()} {r}:\n{info}", file=sys.stderr, flush=True)
    def addSuccess(self, t): self.rec(t, "PASS")
    def addFailure(self, t, e): self.rec(t, "FAIL", self._exc_info_to_string(e, t))
    def addError(self, t, e): self.rec(t, "ERROR", self._exc_info_to_string(e, t))
    def addSkip(self, t, why): self.rec(t, "SKIP", why)
unittest.TestSuite(tests).run(Result())
print("OM-RESULTS " + json.dumps(res, sort_keys=True))
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_exec(path: Path, text: str) -> Path:
    path.write_text(text)
    os.chmod(path, 0o755)
    return path


class Lab:
    def __init__(self, args):
        self.args = args
        base = Path("/dev/shm" if os.access("/dev/shm", os.W_OK) else "/tmp")
        self.tmp = base / f"om-qemu-{secrets.token_hex(6)}"
        self.tmp.mkdir(mode=0o700)
        self.testtmp = self.tmp / "t"       # private_tmp() of the unit tests run here (om_testlib)
        self.testtmp.mkdir(mode=0o700)
        self.bundle = sm.load_bundle(args.bundle)
        self.files = {rel: f.data for rel, f in self.bundle.files.items()}
        self.results = {}

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
        for name in ("busybox", "ld-musl.so"):
            write_exec(self.rtq / "dev/om" / name, "")
            (self.rtq / "dev/om" / name).write_bytes(self.files[f"runtime/{name}"])
        lib = f"LD_LIBRARY_PATH='{a.qemu_lib_path}' " if a.qemu_lib_path else ""
        self.qw = write_exec(self.tmp / "qemu-arm.sh",
                             f"#!/bin/sh\n{lib}exec '{a.qemu_arm}' -U LD_LIBRARY_PATH -cpu arm926 \"$@\"\n")
        # Launchers the x86 host can exec (no binfmt_misc needed): the real ARM BusyBox binaries.
        sq = self.stockq
        self.stock_bbw = write_exec(self.tmp / "stock-busybox",
                                    f"#!/bin/sh\nexec '{self.qw}' -L '{sq}' '{sq}/bin/busybox' \"$@\"\n")
        self.rt_bbw = write_exec(self.tmp / "rt-busybox",
                                 f"#!/bin/sh\nexec '{self.qw}' -L '{self.rtq}' '{self.rtq}/dev/om/busybox' \"$@\"\n")
        self.stock_bb, self.rt_bb = [str(self.stock_bbw)], [str(self.rt_bbw)]
        self.stock_list = self.run(self.stock_bb + ["--list"]).stdout.split()

    def run(self, cmd, env=None, timeout=600, **kw):
        env = {"PATH": "/usr/bin:/bin", **(env or {})}
        return subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=self.tmp, **kw)

    def record(self, name, ok, **details):
        self.results[name] = {"result": "PASS" if ok else "FAIL", **details}
        print(f"[qemu-validate] {name}: {'PASS' if ok else 'FAIL'}", flush=True)

    def unittests(self, module: str, patterns=(), extra_env=None, timeout=3 * 3600) -> tuple:
        env = {**os.environ, "OM_E2E_STOCK_BB": str(self.stock_bbw), "OM_MKSQUASHFS": str(self.args.mksquashfs),
               "OM_UNSQUASHFS": str(self.args.unsquashfs), "OM_TEST_TMPDIR": str(self.testtmp), **(extra_env or {})}
        t0 = time.monotonic()
        p = subprocess.Popen([sys.executable, "-c", DRIVER, module, *patterns], cwd=HERE, env=env, text=True,
                             stdout=subprocess.PIPE, stderr=None, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
        except BaseException:      # timeout, Ctrl-C, SIGTERM: no orphaned test, server or qemu-arm process
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            raise
        line = next((ln for ln in out.splitlines() if ln.startswith("OM-RESULTS ")), None)
        res = json.loads(line[len("OM-RESULTS "):]) if line else {}
        return p.returncode, res, round(time.monotonic() - t0, 1)

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
        rt = self.rt_bb
        true_rc = self.run(rt + ["true"]).returncode
        applets = self.run(rt + ["--list"]).stdout.split()
        missing = [x for x in bma.RUNTIME_APPLETS if x not in applets]
        arith = self.run(rt + ["sh", "-c", "echo $((6*7))"]).stdout.strip()
        sample = self.tmp / "sample.bin"
        data = b"\0\0\x11\x22" + random.Random(7).randbytes(200000) + b"\0"
        sample.write_bytes(data)
        facts = {
            "sha256sum": self.run(rt + ["sha256sum", sample]).stdout.split()[:1] == [sha(data)],
            "crc32_file": self.run(rt + ["crc32", sample]).stdout.split()[:1] == ["%08x" % zlib.crc32(data)],
            # om_stockboot.sh env_crc_ok: CRC of everything after the 4-byte header vs. the header via od
            "crc32_tail_pipe": self.run(rt + ["sh", "-c", '"$0" tail -c +5 "$1" | "$0" crc32', self.rt_bbw, sample]
                                        ).stdout.split()[:1] == ["%08x" % zlib.crc32(data[4:])],
            "od_tx4": self.run(rt + ["od", "-An", "-tx4", "-N4", sample]).stdout.strip() ==
            "%08x" % struct.unpack("<I", data[:4])[0],
            "base64_binary": base64.b64decode(self.run(rt + ["base64", sample]).stdout) == data,
        }
        orig = self.rtq / "dev/om/busybox.unpatched"
        orig.write_bytes(self.restored)
        os.chmod(orig, 0o755)
        unpatched = self.run([self.qw, "-L", self.rtq, orig, "true"])
        no_musl_loader = not (self.stockq / "lib" / bma.OLD_INTERP.decode().rsplit("/", 1)[1]).exists()
        ok = (true_rc == 0 and not missing and arith == "42" and all(facts.values())
              and unpatched.returncode != 0 and no_musl_loader)
        self.record("runtime_on_stock_root", ok, true_rc=true_rc, runtime_applets=len(applets),
                    required_applets_missing=missing, arithmetic=arith, applet_results_match=facts,
                    unpatched_rc=unpatched.returncode, stock_rootfs_has_musl_loader=not no_musl_loader)

    def check_shells_parse(self):
        img = self.tmp / "appfs.sqfs"
        shutil.copyfile(Path(self.args.bundle) / "appfs" / "migration-appfs.squashfs", img)
        runapp = self.tmp / "runapp.sh"
        cat = subprocess.run([str(self.args.unsquashfs), "-cat", str(img), "runapp.sh"], capture_output=True,
                             timeout=300, stdin=subprocess.DEVNULL, check=True)
        runapp.write_bytes(cat.stdout)
        img.unlink()
        conf = Path(self.args.bundle) / "conf" / "migrate.conf"
        st, rt = self.stock_bb, self.rt_bb
        rcs = {
            "stock_sh_n_stage2": self.run(st + ["sh", "-n", bma.STAGE2]).returncode,
            "stock_sh_n_runapp": self.run(st + ["sh", "-n", runapp]).returncode,
            "stock_sh_n_conf": self.run(st + ["sh", "-n", conf]).returncode,
            "stock_sh_n_trial": self.run(st + ["sh", "-n", TRIAL]).returncode,
            "stock_sh_n_trial_dhcp": self.run(st + ["sh", "-n", TRIAL_DHCP]).returncode,
            "openipc_sh_n_stage2": self.run(rt + ["sh", "-n", bma.STAGE2]).returncode,
            "openipc_sh_n_installer": self.run(rt + ["sh", "-n", bma.INSTALLER]).returncode,
            "openipc_sh_n_hook": self.run(rt + ["sh", "-n", bma.HOOK]).returncode,
        }
        sourced = self.run(st + ["sh", "-c", '. "$1" && [ -n "$OM_RUN_ID" ] && echo "$OM_PORT $OM_DIR"',
                                 "sh", conf]).stdout.strip()
        text = runapp.read_bytes()
        head, tail = bma.LAUNCHER.split("{delay}")
        launcher = re.search(re.escape(head.encode()) + rb"\d+" + re.escape(tail.encode()) + rb"\Z", text)
        branch = bma.TRIAL_BRANCH.encode() + bma.TRIAL_NEEDLE    # patch_runapp: needle -> branch + needle
        ok = (all(rc == 0 for rc in rcs.values()) and sourced == f"{self.bundle.port} {bma.OM_DIR}"
              and launcher is not None and text.count(branch) == 1)
        self.record("shells_parse", ok, **rcs, stock_sh_sources_conf=sourced == f"{self.bundle.port} {bma.OM_DIR}",
                    runapp_ends_with_launcher=launcher is not None,
                    runapp_trial_branch_before_stock_app=text.count(branch) == 1)

    def check_stock_applets(self):
        lst = set(self.stock_list)
        facts, note = self.stock_option_facts()
        ok = lst == set(e2e.STOCK_APPLETS) and not (lst & set(bma.NON_STOCK_WORDS)) and all(facts.values())
        self.record("stock_applets", ok, stock_applets=len(lst), equals_e2e_list=lst == set(e2e.STOCK_APPLETS),
                    non_stock_words_present=sorted(lst & set(bma.NON_STOCK_WORDS)), stock_options=facts,
                    namespace_probes=note)

    def stock_option_facts(self) -> tuple:
        """What phase1 and trial.sh rely on, from the real stock applets: dd takes bma.STOCK_DD_OPERANDS and nothing
        else (trial.sh once padded its tl checkpoint with conv=sync and never wrote it); "dd ... 1<>tl.img" under
        the stock sh keeps the rest of the template; grep has no -x (bma.STOCK_MISSING_OPTIONS; trial.sh once
        matched the manifest with -qx) but anchors work; wget --post-data form-escapes the log (the trial-log
        upload once arrived as %XX text the host could not parse); the options trial.sh passes to applets the E2E
        test stubs or blocks parse. Those run in empty user namespaces: no host interface, route or mount is
        touched."""
        d = self.tmp / "stock-opts"
        d.mkdir()
        src, dst, log, tl, man = d / "src", d / "dst", d / "log", d / "tl.img", d / "manifest"
        src.write_bytes(bytes(range(256)) * 16)
        sb = self.stock_bb

        def dd(*extra, **more):
            dst.write_bytes(b"")
            ops = {"if": src, "of": dst, "bs": 512, "count": 1, **more}
            return self.run(sb + ["dd", *(f"{k}={v}" for k, v in ops.items()), *extra]).returncode
        facts = {"dd_rejects_" + op.replace("=", "_"): dd(op) != 0
                 for op in ("conv=notrunc", "conv=sync", "conv=fsync", "ibs=512", "obs=512", "iflag=fullblock",
                            "oflag=sync")}
        facts["dd_takes_" + "_".join(bma.STOCK_DD_OPERANDS)] = (
            set(bma.STOCK_DD_OPERANDS) == {"if", "of", "bs", "count", "skip", "seek"}
            and dd(skip=1, seek=1) == 0 and dst.read_bytes() == bytes(512) + src.read_bytes()[512:1024])
        blank, rng = sl.tl_blank(), random.Random(11)
        for name, text in (("short_log", b"omt result=completed\n" * 40),
                           ("long_log", bytes(rng.choice(b"abcdef\n") for _ in range(70000)))):
            log.write_bytes(text)
            tl.write_bytes(blank)
            r = self.run(sb + ["sh", "-c", f"'{self.stock_bbw}' dd if='{log}' bs=61440 count=1 "
                                           f"2>/dev/null 1<>'{tl}'"])
            head = text[:sl.TL_TEXT_LEN]
            facts["dd_overlay_keeps_template_" + name] = (r.returncode == 0
                                                          and tl.read_bytes() == head + blank[len(head):])
        man.write_text("mode=trial\nrun=0123abcd\nxrun=0123abcdx\n")
        g = self.run(sb + ["grep", "-qx", "run=0123abcd", man])
        facts["grep_rejects_x"] = set(bma.STOCK_MISSING_OPTIONS) == {"grep"} and g.returncode != 0 and \
            "invalid option" in g.stdout + g.stderr
        facts["grep_anchored_pattern_matches_the_line_only"] = (
            self.run(sb + ["grep", "-q", "^run=0123abcd$", man]).returncode == 0
            and self.run(sb + ["grep", "-q", "^run=0123abc$", man]).returncode == 1)
        facts.update(self.wget_post_data_facts())
        unshare = shutil.which("unshare")
        if not unshare or any(self.run([unshare, ns, "true"]).returncode for ns in ("-rn", "-rm")):
            return facts, "skipped: unprivileged unshare -rn/-rm unavailable"
        probes = {"udhcpc": ("-rn", ["udhcpc", "-i", "om-none0", "-s", d / "none.sh", "-t", "5", "-T", "3", "-n",
                                     "-q"]),
                  "wget_post_data": ("-rn", ["wget", "-q", "-T", "5", "-O", "/dev/null", "--post-data", "a=b",
                                             "http://127.0.0.1:9/x"]),
                  "mount_t_o": ("-rm", ["mount", "-t", "squashfs", "-o", "ro", d / "none.img", d / "none-dir"]),
                  "control_udhcpc_Z": ("-rn", ["udhcpc", "-Z"])}
        for name, (ns, cmd) in probes.items():
            r = self.run([unshare, ns] + sb + cmd, timeout=120)
            rejected = re.search(r"invalid option|unrecognized option", r.stdout + r.stderr) is not None
            facts["options_parse_" + name if not name.startswith("control") else name + "_is_rejected"] = (
                r.returncode != 0 and rejected == name.startswith("control"))
        return facts, "empty user+net namespace (udhcpc, wget), user+mount namespace (mount)"

    def wget_post_data_facts(self) -> dict:
        """trial.sh: wget -q -T 30 -O /dev/null --post-data "$(cat "$LOG")" URL, to a loopback capture server."""
        text = "omt nonce=ab12\n[ 1.50] x: 100% +y/z [ok] ~!*'()&=_-.\t\n" + "omt result=completed\n" * 3
        got = {}

        class Capture(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got["body"] = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                got["ctype"] = self.headers.get("Content-Type")
                self.send_response(200)
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"ok\n")

            def log_message(self, *args):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = self.run(self.stock_bb + ["wget", "-q", "-T", "30", "-O", "/dev/null", "--post-data", text,
                                          f"http://127.0.0.1:{srv.server_address[1]}/upload/trial-log"], timeout=120)
        finally:
            srv.shutdown()
            srv.server_close()
        body = got.get("body", b"")
        return {"wget_post_data_sent": r.returncode == 0 and bool(body),
                "wget_post_data_is_form_escaped_as_om_testlib_models_it": (
                    body == stock_wget_form(text.encode()) and got.get("ctype") == "application/x-www-form-urlencoded"),
                "serve_migration_decodes_the_stock_post_data": sm.decode_post_data(body) == text.encode()}

    def check_etc_overlay_mount(self):
        """trial.sh: cp -a /etc W/etc && mkdir -p W/etc/mediatek && cp DAT ... && mount -o bind W/etc /etc."""
        unshare = shutil.which("unshare")
        root = self.tmp / "ovl"
        etc, target, work = root / "etc", root / "target", root / "w"
        (etc / "init.d").mkdir(parents=True)
        (etc / "passwd").write_text("root:x:0:0::/root:/bin/sh\n")
        (etc / "init.d" / "rcS").write_text("#!/bin/sh\n")
        os.symlink("/proc/mounts", etc / "mtab")
        target.mkdir()
        (target / "stock-only").write_text("stock\n")
        work.mkdir()
        dat = root / "MT7601USTA.dat"
        dat.write_text(e2e.DAT)
        bb = f"'{self.stock_bbw}'"
        script = (f"{bb} cp -a '{etc}' '{work}/etc' && {bb} mkdir -p '{work}/etc/mediatek' && "
                  f"{bb} cp '{dat}' '{work}/etc/mediatek/MT7601USTA.dat' && {bb} mount -o bind '{work}/etc' '{target}'"
                  f" && {bb} cat '{target}/mediatek/MT7601USTA.dat' && {bb} ls -l '{target}/mtab' && "
                  f"{bb} ls '{target}'")                   # the stock BusyBox has no readlink applet
        if not unshare or self.run([unshare, "-rm", "true"]).returncode != 0:
            self.results["etc_overlay_mount"] = {"result": "SKIP", "reason": "unprivileged unshare -rm unavailable"}
            print("[qemu-validate] etc_overlay_mount: SKIP", flush=True)
            return
        r = self.run([unshare, "-rm", "sh", "-c", script])
        out = r.stdout.splitlines()
        link = any(ln.endswith(" -> /proc/mounts") for ln in out)
        ok = (r.returncode == 0 and "\n".join(out[:len(e2e.DAT.splitlines())]) + "\n" == e2e.DAT
              and link and "stock-only" not in out and "passwd" in out
              and not (target / "mediatek").exists())  # the bind mount ended with the namespace
        self.record("etc_overlay_mount", ok, rc=r.returncode, dat_visible_through_bind=e2e.DAT.splitlines()[0] in out,
                    symlink_kept=link, stock_files_hidden="stock-only" not in out, listed="passwd" in out,
                    note="unprivileged user+mount namespace (unshare -rm); host /etc untouched")

    def check_runapp_trial_branch(self):
        rc, res, secs = self.unittests("tests.test_build_migration_appfs", ("trial_b", "normal_boot"))
        ok = rc == 0 and len(res) >= 4 and all(v["result"] == "PASS" for v in res.values())
        self.record("runapp_trial_branch", ok, tests=res, seconds=secs,
                    shells=["host sh", "host BusyBox sh", "stock BusyBox 1.18.4 sh (qemu)"])

    def check_phase1_capacity(self):
        rc, res, secs = self.unittests("tests.test_phase1_capacity")
        self.record("phase1_capacity", rc == 0 and len(res) >= 13 and all(v["result"] == "PASS" for v in res.values()),
                    tests=res, seconds=secs)

    def check_e2e_real_binaries(self):
        rc, res, secs = self.unittests("tests.test_e2e_online", (), {"OM_E2E_RUNTIME_BB": str(self.rt_bbw),
                                                                       "OM_E2E_RUN_TIMEOUT": "5400"})
        ok = rc == 0 and len(res) >= 12 and all(v["result"] == "PASS" for v in res.values())
        self.record("e2e_real_binaries", ok, tests=res, seconds=secs,
                    note="phase1 and trial.sh under the stock BusyBox 1.18.4, phase2/om_stockboot.sh/hook under "
                         "the patched OpenIPC BusyBox (both qemu-arm); flash = simulated partitions")

    def close(self):
        """Kill what still runs from the lab directory (e.g. a test's stand-in app), then remove it."""
        mine = str(self.tmp) + "/"
        for d in Path("/proc").iterdir():
            if not d.name.isdigit() or int(d.name) == os.getpid():
                continue
            try:
                argv = (d / "cmdline").read_bytes().split(b"\0")
                if d.stat().st_uid == os.getuid() and any(a.startswith(mine.encode()) for a in argv):
                    os.kill(int(d.name), signal.SIGKILL)
            except OSError:
                continue
        shutil.rmtree(self.tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--stock-rootfs", type=Path, required=True)
    ap.add_argument("--qemu-arm", required=True)
    ap.add_argument("--qemu-lib-path", default="", help="LD_LIBRARY_PATH for a non-static qemu-arm")
    ap.add_argument("--unsquashfs", type=Path, required=True)
    ap.add_argument("--mksquashfs", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="run a subset of the checks")
    args = ap.parse_args(argv)
    if sm.inside_repo(args.bundle):
        print("validate_online_qemu: the bundle must stay outside the repository", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    lab = Lab(args)
    checks = ["runtime_patch", "runtime_on_stock_root", "shells_parse", "stock_applets", "etc_overlay_mount",
              "runapp_trial_branch", "phase1_capacity", "e2e_real_binaries"]
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
    failed = [k for k, v in lab.results.items() if v["result"] == "FAIL"]
    summary = {
        "schema": SCHEMA, "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "camera_contacted": False, "mtd_devices_opened": False, "network": "127.0.0.1 only",
        "emulator": "qemu-arm user mode, -cpu arm926",
        "result": "PASS" if lab.results and not failed else "FAIL",
        "inputs": {"stage2_sha256": sha(bma.STAGE2.read_bytes()), "installer_sha256": sha(bma.INSTALLER.read_bytes()),
                   "hook_sha256": sha(bma.HOOK.read_bytes()), "trial_sha256": sha(TRIAL.read_bytes()),
                   "trial_dhcp_sha256": sha(TRIAL_DHCP.read_bytes())},
        "checks": lab.results,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"result": summary["result"], "checks": {k: v["result"] for k, v in lab.results.items()}}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
