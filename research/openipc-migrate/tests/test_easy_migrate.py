"""easy/easy_migrate.py offline: the real serve_migration server on loopback, a scripted camera that speaks its
protocol, build_stockboot replaced by a fake that writes valid synthetic action sets. No camera, no flash."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import io
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock
import sys
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from om_testlib import (ROOT, free_port, make_bundle, private_tmp, rbytes, rmtree, stock_wget_form,  # noqa: E402
                        synthetic_files, write_action_set)
import sb_fixture as fx  # noqa: E402
from sb_fixture import sl  # noqa: E402

sys.path.insert(0, str(ROOT / "easy"))
import build_stockboot as bs  # noqa: E402
import easy_migrate as em  # noqa: E402
import make_env  # noqa: E402

E0 = sl.build_env(fx.E0_ENTRIES)
UBOOT, ENVLIVE = "a" * 64, hashlib.sha256(E0).hexdigest()
OENV = sl.build_env([("bootcmd", sl.FINAL_BOOTCMD), ("mtdparts", sl.OIPC_ENV_LAYOUT["mtdparts"]),
                     ("wlandev", "mt7601u-hi3518ev100-camhi"), ("wlanssid", "dummy-ssid-for-test"),
                     ("wlanpass", "dummy-pass-for-test"), ("sensor", make_env.DEFAULT_SENSOR),
                     ("upgrade", make_env.NO_ONLINE_UPDATE_URL)], sl.OIPC_ENV_LEN)
TRIAL_IMG = fx.trial_image()
K = sl.UBOOT_LEN - em.UBOOT_TAIL
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OK = {"ok": True, "result": "completed", "log_source": "trial-log", "checks": {"result": True}}
FAILED = {"ok": False, "result": "failed", "reason": "synthetic", "checks": {"result": False}}


def fast_timing() -> em.Timing:
    t = em.Timing()
    for k, v in dict(poll=0.02, link_retry=0.5, link_max=3, hint_after=30, standby_max=10, start_max=10,
                     action_max=10, armed_grace=0.3, reboot_log_wait=1, trial_report_max=5, trial_linger=0.2,
                     final_idle_max=1.5, final_verified_quiet=1, final_retry_start=3, address_max=3,
                     address_retry=0.1, service_max=2, hook_max=30).items():
        setattr(t, k, v)
    return t


class FakeBS:
    """build_stockboot.py: diag compares the camera's U-Boot with the given SPI image, the builders write valid
    synthetic action sets, collect answers per stage."""

    def __init__(self):
        self.calls = []
        self.collect = {"probe": OK, "trial": OK}

    def __call__(self, *args):
        args = [str(a) for a in args]
        cmd, opt = args[0], dict(zip(args[1::2], args[2::2]))
        self.calls.append(cmd)
        if cmd == "diag":
            uboot = bs.newest_upload(Path(opt["--uploads"]), "diag-uboot")
            if uboot != Path(opt["--spi-dump"]).read_bytes()[:sl.UBOOT_LEN]:
                return {"ok": False, "problems": [em.UBOOT_PROBLEM + " (synthetic)"]}
            return {"ok": True, "problems": [], "notes": ["synthetic diagnose"]}
        if cmd == "wdtgate":
            return {"ok": True, "checks": {"held": True}, "held_seconds": 300}
        if cmd == "collect":
            return self.collect[opt["--stage"]]
        action, files = {"probe": ("probe-arm", {"env-probe.img": sl.build_probe(E0)[0]}),
                         "trial": ("trial-arm", {"trial.img": TRIAL_IMG, "env-armed.img": sl.build_trial(E0)[0]}),
                         "final": ("final", {"env-final.img": sl.build_final(E0)[0]}),
                         "restore": ("restore-env", {"env-base.img": E0})}[cmd]
        out = write_action_set(Path(opt["--out"]), action, files, ENVLIVE, UBOOT)
        return {"out": str(out), "payload_margin_bytes": 1234}


class FakeCamera(threading.Thread):
    """The stock firmware with the delivered hook, then the installer, the trial kernel and the final run."""

    def __init__(self, port, run_id, work, uboot, final_script=("ok",)):
        super().__init__(daemon=True)
        self.base, self.run_id, self.work, self.uboot = f"http://127.0.0.1:{port}", run_id, Path(work), uboot
        self.final_script = list(final_script)
        self.halt = threading.Event()
        self.actions, self.errors, self.seqs = [], [], set()

    def stop(self):
        self.halt.set()
        self.join(15)

    def req(self, path, data=None, **query):
        url = f"{self.base}{path}?{urlencode(dict(run=self.run_id, **query))}"
        try:
            with _OPENER.open(urllib.request.Request(url, data=data), timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError:
            return None, b""

    def must(self, path, data=None, **query):
        code, body = self.req(path, data, **query)
        if code != 200:
            raise RuntimeError(f"{path} {query}: {code} {body[:60]!r}")
        return body

    def event(self, phase, event):
        self.must("/status", phase=phase, event=event)

    def upload(self, name, data):
        self.must(f"/upload/{name}", base64.encodebytes(data))

    def run(self):
        try:
            while not (self.work / "delivered").exists():
                if self.halt.wait(0.02):
                    return
            last = 0.0
            while not self.halt.is_set():
                code, body = self.req("/manifest")
                if code == 200:
                    m = dict(line.split("=", 1) for line in body.decode().splitlines() if "=" in line)
                    if m.get("mode") == "go" and m.get("seq") not in self.seqs:
                        self.seqs.add(m["seq"])
                        self.actions.append(m["action"])
                        if getattr(self, "act_" + m["action"].replace("-", "_"))(m) == "gone":
                            return
                        continue
                    if time.monotonic() - last >= 0.5:
                        self.req("/status", phase="1", event="hook-standby")
                        last = time.monotonic()
                self.halt.wait(0.05)
        except Exception as exc:  # noqa: BLE001 -- reported by the test
            self.errors.append(f"{type(exc).__name__}: {exc}")

    def fetch_action_files(self, m):
        for key, want in m.items():
            if key.startswith("f.") and hashlib.sha256(self.must(f"/action/{key[2:]}")).hexdigest() != want:
                raise RuntimeError(f"{key}: digest differs from the manifest")

    def act_diagnose(self, m):
        self.event("2", "action-start-diagnose")
        self.upload("diag-info", b"== synthetic diagnose\n")
        self.upload("diag-blocks", b"mtd0 boot 0x40000\n")
        self.upload("diag-env", E0)
        self.upload("diag-uboot", self.uboot)
        self.event("installer", "diagnose-done")
        self.event("2", "action-rc-diagnose-0")

    def act_wdtgate(self, m):
        self.event("2", "action-start-wdtgate")
        self.upload("wdtgate-info", b"held 300 s\n")
        self.event("2", "action-rc-wdtgate-0")

    def act_collect(self, m):
        self.event("2", "action-start-collect")
        self.upload("collect-info", b"synthetic collect\n")
        self.upload("collect-tl", bytes(sl.TL_LEN))
        self.upload("collect-env", E0)
        self.event("2", "action-rc-collect-0")

    def arm(self, m):
        action = m["action"]
        self.event("2", f"action-start-{action}")
        self.fetch_action_files(m)
        self.event("installer", f"armed-{action}")
        self.event("installer", f"rebooting-{action}")
        self.upload(f"log-{action}", f"{action}: armed, rebooting\n".encode())
        self.halt.wait(0.2)

    def act_probe_arm(self, m):
        self.arm(m)

    def act_trial_arm(self, m):
        self.arm(m)
        t0 = time.monotonic()
        while not (self.work / "trial-ap").exists():   # the trial joins the trial Wi-Fi (hook trial_ap_up)
            if self.halt.wait(0.02) or time.monotonic() - t0 > 10:
                return
        self.must("/manifest")
        self.event("trial", "trial-network-up")
        self.must("/upload/trial-log", stock_wget_form(b"trial: OpenIPC booted\nwlan0 up\n"))
        self.event("trial", "trial-completed")

    def act_restore_env(self, m):
        self.event("2", "action-start-restore-env")
        self.fetch_action_files(m)
        self.event("installer", "env-restored")
        self.event("2", "action-rc-restore-env-0")

    def act_final(self, m):
        self.event("2", "action-start-final")
        self.must("/payload/firmware.bin")
        self.must("/payload/env-openipc.img")
        self.fetch_action_files(m)
        self.event("installer", "final-pre-ponr")
        self.upload("mdat1", rbytes(1, 0x20000))
        self.upload("mdat2", rbytes(2, 0x20000))
        self.event("installer", "ponr")
        how = self.final_script.pop(0) if self.final_script else "ok"
        if how == "stall":
            return "gone"
        if how == "dirty":
            self.event("installer", "stopped-dirty")
            self.event("installer", "hold-dirty-retry-final")
            self.event("2", "action-rc-final-2")
            return None
        self.event("installer", "final-verified")
        self.event("installer", "rebooting-final")
        self.upload("log-final", b"final: verified, rebooting\n")
        return "gone"


class _Web(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.paths.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class Base(unittest.TestCase):
    oenv = OENV

    def setUp(self):
        old = os.umask(0o077)
        os.umask(old)
        self.addCleanup(os.umask, old)
        self.tmp = private_tmp("em")
        self.addCleanup(rmtree, self.tmp)
        files = synthetic_files(3)
        files["payload/env-openipc.img"] = self.oenv
        files.update({rel: src.read_bytes() for rel, src in em.BUNDLE_SCRIPTS.items()})   # as from this checkout
        self.port = free_port()
        self.run_id, _ = make_bundle(self.tmp / "bundle", files, port=self.port)
        fw = files["payload/firmware.bin"]
        (self.tmp / "firmware.bin").write_bytes(fw)
        dump = bytearray(rbytes(9, bs.SPI_BYTES))
        dump[K:sl.UBOOT_LEN] = b"\xff" * em.UBOOT_TAIL
        self.dump = bytes(dump)
        self.cam_uboot = self.dump[:K] + b"\x5a" * em.UBOOT_TAIL   # unit 2: data where the SPI image has 0xFF
        (self.tmp / "spi.bin").write_bytes(self.dump)
        (self.tmp / "rootfs" / "lib" / "modules").mkdir(parents=True)
        (self.tmp / "auth.bin").write_bytes(b"auth-runtime")
        self.wifi = self.tmp / "wifi.vars"
        self.wifi.write_text("wlanssid=trial-ssid-for-test\nwlanpass=trial-pass-for-test\n")
        self.wifi.chmod(0o600)
        self.work = self.tmp / "work"
        self.cfg = self.tmp / "easy.json"
        self.config = {
            "comment": "offline test", "bundle": "bundle", "work": str(self.work), "spi_dump": "spi.bin",
            "firmware": "firmware.bin", "oipc_rootfs": "rootfs", "auth_runtime": "auth.bin", "wifi_vars": "wifi.vars",
            "mksquashfs": "true",
            "hooks": {n: f'echo "{n} $EASY_STEP" >> "$EASY_WORK/hooks.txt"' + extra for n, extra in {
                "camera_ap_up": "", "deliver": '; touch "$EASY_WORK/delivered"',
                "trial_ap_up": '; touch "$EASY_WORK/trial-ap"', "trial_ap_down": '; rm -f "$EASY_WORK/trial-ap"',
                "final_ap_up": "", "camera_address": "; echo 127.0.0.1"}.items()}}
        self.write_config()
        self.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Web)
        self.web.paths = []
        threading.Thread(target=self.web.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        self.addCleanup(self.web.server_close)
        self.addCleanup(self.web.shutdown)
        self.fbs = FakeBS()
        self.post = []
        for p in (mock.patch.object(em, "FIRMWARE_SHA256", hashlib.sha256(fw).hexdigest()),
                  mock.patch.object(em, "PING", None),
                  mock.patch.object(em, "SERVICE_PORTS", {"ssh": free_port(), "http": self.web.server_address[1],
                                                          "rtsp": free_port()}),
                  mock.patch.object(em.Flow, "bs", self.fbs),
                  mock.patch.object(em.Flow, "run_post_setup", lambda flow, ip: self.post.append(ip) or 0)):
            p.start()
            self.addCleanup(p.stop)
        self.out = ""

    def write_config(self, mode=0o600):
        self.cfg.write_text(json.dumps(self.config))
        self.cfg.chmod(mode)

    def camera(self, final_script=("ok",)) -> FakeCamera:
        cam = FakeCamera(self.port, self.run_id, self.work, self.cam_uboot, final_script)
        cam.start()
        self.addCleanup(cam.stop)
        return cam

    def em(self, *argv) -> int:
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = em.main(["--config", str(self.cfg), *argv], out=buf, timing=fast_timing())
        self.out = buf.getvalue() + err.getvalue()
        return rc

    def state(self) -> dict:
        return json.loads((self.work / "easy-migrate.json").read_text())

    def seed(self, upto: str, **extra) -> None:
        """A work directory as a run up to `upto` leaves it (the camera already has the hook)."""
        st = em.fresh_state()
        st["done"] = {s: "2000-01-01T00:00:00" for s in em.STEPS[:em.STEPS.index(upto) + 1]}
        st["deliver_sent"] = True
        st.update(extra)
        self.work.mkdir(mode=0o700, exist_ok=True)
        (self.work / "easy-migrate.json").write_text(json.dumps(st))
        (self.work / "delivered").touch()

    def hooks(self) -> list:
        try:
            return (self.work / "hooks.txt").read_text().splitlines()
        except FileNotFoundError:
            return []


class FullRun(Base):
    def test_stock_to_openipc_in_three_commands(self):
        cam = self.camera()
        rc = self.em("run")
        self.assertEqual(rc, 4, self.out)
        self.assertIn("everything before final passed", self.out)
        self.assertIn("run --yes-final", self.out)
        self.assertEqual(cam.actions, ["diagnose", "wdtgate", "probe-arm", "collect", "trial-arm", "collect"])
        self.assertEqual(self.fbs.calls, ["diag", "diag", "wdtgate", "probe", "collect", "trial", "collect"])
        self.assertEqual(self.hooks(), ["deliver deliver", "trial_ap_up trial", "trial_ap_down trial"])
        st = self.state()
        self.assertEqual(sorted(st["done"]), sorted(em.STEPS[:6]))
        self.assertEqual(sorted(st["armed"]), ["probe", "trial"])
        # unit 2's case: the SPI image has 0xFF where the camera's U-Boot has data -> a private reference image
        ref = self.work / "spi-reference-8m.bin"
        self.assertEqual(st["spi_reference"], str(ref))
        self.assertEqual(ref.read_bytes(), self.cam_uboot + self.dump[sl.UBOOT_LEN:])
        self.assertEqual(ref.stat().st_mode & 0o777, 0o600)
        self.assertIn(str(ref), self.out)
        self.assertEqual((self.work / "easy-migrate.json").stat().st_mode & 0o777, 0o600)

        rc = self.em("run", "--yes-final")
        self.assertEqual(rc, 4, self.out)
        self.assertEqual(cam.actions[-1], "final")
        fs = self.state()["final"]
        self.assertEqual((fs["attempts"], fs["ponr"], fs["verified"], fs["outcome"]), (1, True, True, "rebooting"))
        self.assertEqual((self.work / "camera-address.txt").read_text(), "127.0.0.1\n")
        self.assertIn("ssh root@127.0.0.1", self.out)
        self.assertIn("owner-done", self.out)
        self.assertIn("EULA", self.out)
        self.assertEqual(sorted(self.web.paths), ["/", "/image.jpg"])
        self.assertEqual(self.hooks()[3:], ["final_ap_up boot", "camera_address boot"])
        self.assertEqual(self.post, [])

        self.assertEqual(self.em("owner-done"), 0, self.out)
        self.assertEqual(self.post, ["127.0.0.1"])
        self.assertIn("done: the camera runs OpenIPC at 127.0.0.1", self.out)
        self.assertIn("ssh root@127.0.0.1 'cli -s .audio.outputEnabled true'", self.out)
        self.assertIn("Hold the reset button for 3-30 s", self.out)
        self.assertEqual(self.em("run"), 0, self.out)
        self.assertEqual(self.post, ["127.0.0.1"])
        self.assertEqual(self.em("status"), 0, self.out)
        self.assertIn("all steps done", self.out)
        self.assertEqual(cam.errors, [])


class OneShot(Base):
    def test_trial_is_armed_once_and_read_back(self):
        self.seed("probe")
        cam = self.camera()
        self.fbs.collect["trial"] = FAILED
        self.assertEqual(self.em("run"), 1, self.out)
        self.assertIn("--again trial", self.out)
        st = self.state()
        self.assertIn("trial", st["armed"])
        self.assertNotIn("trial", st["done"])
        self.assertEqual(self.em("run"), 1, self.out)   # read back again, never armed twice
        self.assertIn("armed before", self.out)
        self.assertEqual(self.fbs.calls.count("trial"), 1)
        self.assertEqual(cam.actions, ["trial-arm", "collect", "collect"])
        self.fbs.collect["trial"] = OK
        self.assertEqual(self.em("run", "--again", "trial"), 4, self.out)
        self.assertEqual(self.fbs.calls.count("trial"), 2)
        self.assertEqual(cam.actions, ["trial-arm", "collect", "collect", "trial-arm", "collect"])
        self.assertEqual(self.hooks().count("trial_ap_up trial"), 2)
        self.assertEqual(cam.errors, [])


class Final(Base):
    def test_stall_after_point_of_no_return(self):
        self.seed("trial")
        cam = self.camera(final_script=("stall",))
        self.assertEqual(self.em("run", "--yes-final"), 3, self.out)
        self.assertIn("POINT OF NO RETURN", self.out)
        fs = self.state()["final"]
        self.assertEqual((fs["ponr"], fs["verified"], fs["outcome"]), (True, False, "stalled"))
        self.assertNotIn("final", self.state()["done"])
        self.assertEqual(self.em("run", "--again", "probe"), 2, self.out)
        self.assertEqual(self.em("restore-env"), 2, self.out)
        self.assertEqual(self.em("status"), 0)
        self.assertIn("point of no return PASSED", self.out)
        cam.stop()
        self.assertEqual(cam.errors, [])
        # the camera holds and waits for final again: the same action set is offered again
        cam2 = self.camera()
        self.assertEqual(self.em("run", "--yes-final"), 4, self.out)
        fs = self.state()["final"]
        self.assertEqual((fs["attempts"], fs["verified"], fs["outcome"]), (2, True, "rebooting"))
        self.assertEqual(self.fbs.calls.count("final"), 1)
        self.assertEqual(cam2.actions, ["final"])
        self.assertEqual(cam2.errors, [])

    def test_dirty_stop_then_retry(self):
        self.seed("trial")
        cam = self.camera(final_script=("dirty", "ok"))
        self.assertEqual(self.em("run", "--yes-final"), 4, self.out)
        fs = self.state()["final"]
        self.assertEqual((fs["attempts"], fs["ponr"], fs["verified"], fs["outcome"]), (2, True, True, "rebooting"))
        self.assertEqual(cam.actions, ["final", "final"])
        self.assertIn("retry 1 of", self.out)
        self.assertEqual(cam.errors, [])

    def test_final_needs_yes(self):
        self.seed("trial")
        cam = self.camera()
        self.assertEqual(self.em("run"), 4, self.out)
        self.assertEqual(self.fbs.calls, [])
        self.assertEqual(cam.actions, [])
        self.assertEqual(self.state()["final"]["attempts"], 0)


class RestoreEnv(Base):
    def test_restore_env_before_final(self):
        self.seed("deliver")
        self.assertEqual(self.em("restore-env"), 2, self.out)
        self.assertIn("passed diagnose", self.out)
        self.seed("wdtgate")
        cam = self.camera()
        self.assertEqual(self.em("restore-env"), 0, self.out)
        self.assertIn("vendor env restored", self.out)
        self.assertEqual(sorted(self.state()["done"]), ["backup", "deliver"])
        self.assertEqual(cam.actions, ["restore-env"])
        self.assertEqual(self.fbs.calls, ["restore"])
        self.assertEqual(cam.errors, [])


class Preflight(Base):
    def test_check_ok(self):
        self.assertEqual(self.em("check"), 0, self.out)
        self.assertIn("preflight ok", self.out)
        self.assertIn("with camera scripts of this checkout", self.out)
        self.assertIn(f"post_setup {em.ps.VERSION} with all its files", self.out)

    def test_post_setup_files(self):
        # the last step runs post_setup.py: a file it puts on the camera (here the audio fix) is missing
        gone = self.tmp / "no-libmajaudiofix.so"
        payloads = tuple((n, gone if n == "audiofix" else p) for n, p in em.ps.PAYLOADS)
        with mock.patch.object(em.ps, "PAYLOADS", payloads):
            self.assertEqual(self.em("check"), 2, self.out)
            self.assertIn(f"problem: post_setup: [Errno 2] No such file or directory: '{gone}'", self.out)
            self.assertEqual(self.em("run"), 2, self.out)
            self.assertIn("1 preflight problem(s)", self.out)
        self.assertEqual(self.hooks(), [])
        self.assertEqual(self.em("check"), 0, self.out)

    def test_wifi_vars_must_be_private(self):
        self.wifi.chmod(0o644)
        self.assertEqual(self.em("check"), 2, self.out)
        self.assertIn("chmod 600", self.out)
        self.assertEqual(self.em("run"), 2, self.out)

    def test_dump_and_firmware(self):
        (self.tmp / "spi.bin").write_bytes(b"short")
        (self.tmp / "firmware.bin").write_bytes(b"other")
        self.assertEqual(self.em("check"), 2, self.out)
        self.assertIn("not an 8 MiB file", self.out)
        self.assertIn("firmware: not the pinned", self.out)


class OldEnv(Base):
    oenv = fx.oenv()   # an env-openipc.img without sensor and upgrade (as before make_env's defaults)

    def test_sensor_and_upgrade_required(self):
        self.assertEqual(self.em("check"), 2, self.out)
        self.assertIn("sensor=ar0141", self.out)
        self.assertIn("40 s", self.out)
        self.assertIn("no upgrade variable", self.out)


class OldBundle(Base):
    """A bundle built from older camera scripts (one from before the watchdog stop of the final, say)."""

    def rebuild(self, scripts: dict, stage2: bytes = None) -> None:
        files = {rel: f.data for rel, f in em.sm.load_bundle(self.tmp / "bundle").files.items()}
        files.update(scripts)
        rmtree(self.tmp / "bundle")
        self.run_id, _ = make_bundle(self.tmp / "bundle", files, port=self.port, stage2=stage2)

    def test_refused_before_delivery(self):
        self.rebuild({"runtime/migrate_hook.sh": b"#!/bin/sh\n# older hook\n"}, stage2=b"#!/bin/sh\n# older\n")
        self.assertEqual(self.em("check"), 2, self.out)
        self.assertIn("problem: bundle: built from older camera scripts (migrate_hook.sh, stage2.sh)", self.out)
        self.assertEqual(self.em("run"), 2, self.out)
        self.assertIn("1 preflight problem(s)", self.out)
        self.assertEqual(self.hooks(), [])

    def test_kept_once_delivered_never_sent_again(self):
        self.rebuild({"runtime/om_stockboot.sh": b"#!/bin/sh\n# older installer\n"})
        self.seed("trial")   # the camera already has the hook from this bundle
        cam = self.camera()
        self.assertEqual(self.em("check"), 0, self.out)
        self.assertIn("note: the bundle was built from older camera scripts (om_stockboot.sh)", self.out)
        self.assertIn("with older camera scripts (see the note)", self.out)
        self.assertEqual(self.em("run"), 4, self.out)   # on to final, which waits for --yes-final
        self.assertIn("note: the bundle was built from older camera scripts", self.out)
        self.assertEqual(cam.actions, [])
        self.assertEqual(self.em("run", "--again", "deliver"), 2, self.out)
        self.assertIn("STOPPED: bundle: built from older camera scripts (om_stockboot.sh)", self.out)
        self.assertEqual([h for h in self.hooks() if h.startswith("deliver ")], [])
        self.assertFalse(self.state()["deliver_sent"])


class Config(Base):
    def test_group_writable_config(self):
        self.write_config(0o664)
        self.assertEqual(self.em("check"), 2)
        self.assertIn("not writable by others", self.out)

    def test_missing_hook_and_unknown_key(self):
        del self.config["hooks"]["deliver"]
        self.config["colour"] = "blue"
        self.write_config()
        self.assertEqual(self.em("check"), 2)
        self.assertIn("hooks.deliver", self.out)
        self.assertIn("unknown config keys: colour", self.out)

    def test_work_inside_repository_refused(self):
        self.config["work"] = str(ROOT / "work-should-not-exist")
        self.write_config()
        self.assertEqual(self.em("check"), 2)
        self.assertIn("outside the Git repository", self.out)
        self.assertFalse((ROOT / "work-should-not-exist").exists())


class Reference(unittest.TestCase):
    def test_compose_reference(self):
        dump = bytearray(rbytes(5, bs.SPI_BYTES))
        dump[K:sl.UBOOT_LEN] = b"\xff" * em.UBOOT_TAIL
        dump = bytes(dump)
        cam = dump[:K] + b"\x01" * em.UBOOT_TAIL
        self.assertEqual(em.compose_reference(dump, cam), cam + dump[sl.UBOOT_LEN:])
        self.assertIsNone(em.compose_reference(dump, dump[:sl.UBOOT_LEN]))          # nothing to fix
        self.assertIsNone(em.compose_reference(dump, b"\x00" + cam[1:]))             # differs elsewhere
        other = dump[:K] + b"\x00" * em.UBOOT_TAIL + dump[sl.UBOOT_LEN:]
        self.assertIsNone(em.compose_reference(other, cam))                          # no 0xFF tail in the image
        self.assertIsNone(em.compose_reference(dump[:-1], cam))


class Example(unittest.TestCase):
    def test_config_example_is_valid(self):
        tmp = private_tmp("em-example")
        self.addCleanup(rmtree, tmp)
        cfg = tmp / "easy.json"
        cfg.write_bytes((ROOT / "easy" / "config.example.json").read_bytes())
        os.chmod(cfg, 0o600)
        c = em.load_config(cfg)
        self.assertEqual(set(c["hooks"]), set(em.REQUIRED_HOOKS + em.OPTIONAL_HOOKS))
        for key in em.PATH_KEYS:
            self.assertTrue(c[key].is_relative_to(tmp), key)   # relative to the copy, so outside the repository
        self.assertEqual(c["camera_ssh"], "ssh root@{ip}")


if __name__ == "__main__":
    unittest.main()
