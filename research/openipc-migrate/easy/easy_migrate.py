#!/usr/bin/env python3
"""Moves an iBlack/iErmu camera (Hi3518EV100, stock firmware) to OpenIPC over Wi-Fi, as one resumable command.

It runs the steps unit 2 went through, in order, and keeps in a private work directory what is done, so the same
command goes on where it stopped (after a failure, a power cut or Ctrl-C):

  backup      8 MiB SPI image of the camera: the way back (an optional `backup` hook makes it)
  deliver     the migration hook goes to the stock firmware once (`deliver` hook); the camera then calls in
  diagnose    read-only: flash, env, U-Boot, Wi-Fi and watchdog, checked against the SPI image
  wdtgate     read-only: all camera processes stopped and the watchdog held off for 5 minutes, then resumed
  probe       U-Boot env probe: U-Boot saves an env once and boots stock again; read back afterwards
  trial       one-shot OpenIPC boot from a spare area over Wi-Fi, then stock again; read back afterwards
  final       the migration itself, only with --yes-final; past its point of no return keep the camera powered
  boot        finds the camera on the network and checks SSH, HTTP and RTSP
  owner       your first login (Majestic EULA, root password): always typed by you, never automated
  post_setup  board settings, the update guard and the microphone/speaker fix over SSH (easy/post_setup.py)

    python3 easy/easy_migrate.py --config PRIVATE.json check              # preflight only
    python3 easy/easy_migrate.py --config PRIVATE.json run                # up to the trial; stops before final
    python3 easy/easy_migrate.py --config PRIVATE.json run --yes-final    # final and boot, then waits for you
    python3 easy/easy_migrate.py --config PRIVATE.json owner-done         # after your first login: post_setup
    python3 easy/easy_migrate.py --config PRIVATE.json status
    python3 easy/easy_migrate.py --config PRIVATE.json restore-env        # before final: the vendor env back

run also takes --until STEP (stop after it) and --again STEP (deliver, diagnose, wdtgate, probe or trial while
final has not passed its point of no return; boot or post_setup at any time). The network specifics are shell
hooks in the config (easy/README.md, easy/config.example.json). The camera protocol and every flash rule stay in
serve_migration.py, build_stockboot.py and the camera scripts, which this only drives. Nothing writes U-Boot.

Exit status: 0 done (or --until reached), 1 a step failed (the stock firmware still boots unless the message says
otherwise), 2 configuration or preflight problem, 3 final passed its point of no return without a verified result
(keep the camera powered and run --yes-final again), 4 waiting for you (--yes-final or the first login).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
sys.path.insert(0, str(BASE))
import build_stockboot as bs  # noqa: E402
import make_env  # noqa: E402
import serve_migration as sm  # noqa: E402
import stock_layout as sl  # noqa: E402
sys.path.insert(0, str(HERE))
import post_setup as ps  # noqa: E402

SCHEMA = "iblack-easy-migrate/1"
STEPS = ("backup", "deliver", "diagnose", "wdtgate", "probe", "trial", "final", "boot", "owner", "post_setup")
AGAIN_BEFORE_FINAL = ("deliver", "diagnose", "wdtgate", "probe", "trial")
AGAIN_ANY_TIME = ("boot", "post_setup")
PATH_KEYS = ("bundle", "work", "spi_dump", "firmware", "oipc_rootfs", "auth_runtime", "wifi_vars")
OTHER_KEYS = ("mksquashfs", "camera_ssh", "hooks", "comment")
REQUIRED_HOOKS = ("camera_ap_up", "deliver", "trial_ap_up", "trial_ap_down", "final_ap_up", "camera_address")
OPTIONAL_HOOKS = ("backup", "camera_ap_check")
FIRMWARE_SHA256 = bs.FIRMWARE_SHA256      # the pinned OpenIPC image; the tests swap in their synthetic one
MAX_FINAL_RESTARTS = 3
SERVICE_PORTS = {"ssh": 22, "http": 80, "rtsp": 554}
PING = ("ping", "-c", "1", "-W", "2")
POST_SETUP = HERE / "post_setup.py"
UBOOT_TAIL = 0x20   # unit 2: its SPI image had 0xFF in the last 32 B of the U-Boot partition, the flash had data
UBOOT_PROBLEM = "vendor U-Boot differs from the SPI dump"
PRE_START_FAILURES = ("fetch-failed-", "payload-mismatch-", "no-pin-", "refused-final")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the camera is local: never a proxy

SENSOR_MSG = ("bundle: env-openipc.img has no sensor=ar0141. Without it OpenIPC's load_hisilicon stops before it "
              "loads wdt.ko, and the watchdog the vendor U-Boot leaves running resets the camera about every 40 s "
              "(a reboot loop). Rebuild the env with make_env.py (sensor defaults to ar0141), then the bundle")
UPGRADE_MSG = ("bundle: env-openipc.img has no upgrade variable. An online update (Web UI or sysupgrade) would then "
               "fetch a standard OpenIPC image, which does not fit this camera's fixed slots and bricks it. Rebuild "
               "the env with make_env.py (upgrade defaults to an unreachable URL), then the bundle")
QUIET_HINT = ("the camera has not called in for {mins}. It calls in for 30 min after each boot (and for 60 min "
              "after an action). Check its power and its Wi-Fi; if it stays silent, power-cycle it: the stock "
              "firmware boots and calls in again (nothing is written before an action starts)")
DELIVER_HINT = ("no call from the camera {mins} after the delivery. It restarts and calls in about a minute after it "
                "is up; if it stays silent, power-cycle it (the delivered hook calls in after every boot)")
FINAL_GATE = ("everything before final passed (diagnose, watchdog gate, saveenv probe, one-shot OpenIPC trial). "
              "final writes OpenIPC into the kernel and rootfs areas and then the vendor U-Boot env, never U-Boot "
              "itself. Once it reports its point of no return the camera must stay powered until it reports "
              "verified, and from then on only the SPI image ({dump}) brings the stock firmware back. To migrate "
              "this camera run:  {cmd}")
PONR_TEXT = ("FINAL PASSED ITS POINT OF NO RETURN WITHOUT A VERIFIED RESULT. Keep the camera powered: it holds and "
             "waits for final again. Run:  {cmd}  If that cannot finish, write the SPI image ({dump}) back with a "
             "programmer.")
OWNER_TEXT = ("OpenIPC is up at {ip}. The first login is yours and is never automated: the camera asks its owner "
              "to accept the Majestic EULA in person.\n"
              "  1. ssh root@{ip}\n"
              "  2. read the EULA and type your answer yourself, promptly (on unit 2 the question timed out after "
              "about 6 s; just log in again if it does)\n"
              "  3. set your root password at the passwd prompts that follow\n"
              "then run:  {cmd}")
DONE_TEXT = ("done: the camera runs OpenIPC at {ip}\n"
             "  Web UI http://{ip}/   RTSP rtsp://{ip}:554/stream=0   SSH ssh root@{ip}\n"
             "  Sound is off by default. post_setup fixed the microphone and the speaker; to use them (each takes "
             "effect at once; audio/README.md):\n"
             "    ssh root@{ip} 'cli -s .audio.enabled true'         # the microphone\n"
             "    ssh root@{ip} 'cli -s .audio.outputEnabled true'   # and the speaker (/play_audio)\n"
             "    ssh root@{ip} 'cli -s .audio.outputVolume 40'      # speaker volume 0-100; 50 plays as sent\n"
             "  Never use the Web UI firmware update or auto-update (update/README.md); after a factory reset run "
             "easy/post_setup.py again.")


class Timing:
    """Seconds. The defaults follow the camera scripts and unit 2; the tests shrink them."""
    poll = 1.0
    link_retry = 60       # link check (and camera_ap_up) while waiting
    link_max = 900        # to get the link to the camera before serving
    hint_after = 150      # without a camera poll before the power-cycle hint
    standby_max = 900     # for the camera to call in after the delivery
    start_max = 2100      # for the camera to start an action (it calls in for 30 min after a boot)
    action_max = 2700     # for a started action to finish (the watchdog gate alone holds 5 min)
    armed_grace = 8       # after armed-ACT without rebooting-ACT
    reboot_log_wait = 20  # after rebooting-ACT, for the log upload (or rc 3: it did not reboot)
    trial_report_max = 480
    trial_linger = 15
    final_idle_max = 1800
    final_verified_quiet = 90
    final_retry_start = 600
    address_max = 900
    address_retry = 10
    service_max = 180
    hook_max = 1800


class Stop(Exception):
    """Ends the command with an exit status (see the module docstring)."""

    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


class Server(sm.MigrationServer):
    """serve_migration's server, also noting when the camera last asked for the manifest."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        # Upload files are NAME-TIME-COUNTER.bin with a counter per server; one work directory sees many servers,
        # so the counter goes on from the files there (two collects in one second would otherwise collide).
        if self.backup_dir is not None:
            self.saved = sum(1 for _ in self.backup_dir.glob("*.bin"))
        self._plock = threading.Lock()
        self.polls, self.last_poll, self.born = 0, None, time.monotonic()

    def manifest(self) -> bytes:
        with self._plock:
            self.polls += 1
            self.last_poll = time.monotonic()
        return super().manifest()

    def quiet_for(self) -> float:
        with self._plock:
            return time.monotonic() - (self.born if self.last_poll is None else self.last_poll)

    def event_list(self) -> list:
        with self.lock:
            return list(self.events)

    def seen(self, phase: str, event: str) -> bool:
        return any(p == phase and e == event for _, p, e in self.event_list())

    def uploaded(self, name: str) -> bool:
        with self.lock:
            return name in self.uploads


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for s in self.streams:
            s.write(text)
        return len(text)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def mins(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 60 else f"{seconds:.0f} s"


def address_local(host: str) -> bool:
    """True while this host has `host`, the address the camera calls (baked into its appfs)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
        return True
    except OSError:
        return False


def tcp_open(ip: str, port: int, timeout: float = 3) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_status(ip: str, port: int, path: str) -> str:
    try:
        with _OPENER.open(f"http://{ip}:{port}{path}", timeout=10) as resp:
            return str(resp.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except (OSError, ValueError) as exc:
        return type(exc).__name__


def compose_reference(dump: bytes, uboot: bytes):
    """The SPI image with the camera's own U-Boot partition, if the two differ only as on unit 2: the image has
    0xFF in the last UBOOT_TAIL bytes of the partition, the camera has data there, and all else is equal."""
    n, k = sl.UBOOT_LEN, sl.UBOOT_LEN - UBOOT_TAIL
    if len(dump) != bs.SPI_BYTES or len(uboot) != n or uboot[:k] != dump[:k]:
        return None
    if dump[k:n] != b"\xff" * UBOOT_TAIL or uboot[k:] == dump[k:n]:
        return None
    return uboot + dump[n:]


def write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_config(path) -> dict:
    path = Path(path).absolute()
    try:
        st = path.stat()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop(f"config {path}: {exc}", 2) from None
    if not isinstance(raw, dict):
        raise Stop("config: expected a JSON object", 2)
    problems = []
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        problems.append("the config must be your own file and not writable by others (its hooks run as you)")
    unknown = sorted(set(raw) - set(PATH_KEYS) - set(OTHER_KEYS))
    if unknown:
        problems.append("unknown config keys: " + ", ".join(unknown))
    cfg = {"path": path}
    for key in PATH_KEYS:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            problems.append(f"config needs {key}")
            continue
        p = Path(os.path.expanduser(val))
        cfg[key] = Path(os.path.abspath(p if p.is_absolute() else path.parent / p))
    cfg["mksquashfs"] = str(raw.get("mksquashfs") or "mksquashfs")
    cfg["camera_ssh"] = str(raw.get("camera_ssh") or "ssh root@{ip}")
    if "{ip}" not in cfg["camera_ssh"]:
        problems.append("camera_ssh must contain {ip}")
    hooks = raw.get("hooks", {})
    if not isinstance(hooks, dict):
        problems.append("hooks must be an object")
        hooks = {}
    problems += [f"unknown hook {n}" for n in sorted(set(hooks) - set(REQUIRED_HOOKS) - set(OPTIONAL_HOOKS))]
    cfg["hooks"] = {n: c for n, c in hooks.items()
                    if n in REQUIRED_HOOKS + OPTIONAL_HOOKS and isinstance(c, str) and c.strip()}
    problems += [f"config needs hooks.{n}" for n in REQUIRED_HOOKS if n not in cfg["hooks"]]
    if problems:
        raise Stop("; ".join(problems), 2)
    return cfg


def fresh_state() -> dict:
    return {"schema": SCHEMA, "done": {}, "armed": {}, "deliver_sent": False, "owner_done": False,
            "spi_reference": None,
            "final": {"attempts": 0, "ponr": False, "verified": False, "outcome": None, "action_dir": None}}


class Flow:
    def __init__(self, config, *, out=None, timing=None):
        self.out = out or sys.stdout
        self.T = timing or Timing()
        self.cfg = load_config(config)
        self.work = self.cfg["work"]
        self.uploads, self.statedir = self.work / "uploads", self.work / "state"
        self.bundle = None
        self.state = fresh_state()
        self.step = None
        self.servers = []
        self.final_running = False
        self._lockfd = None
        self._logs, self._logpaths = {}, {}
        self._tag = time.strftime("%Y%m%dT%H%M%S")
        self._seq = 0

    # ---- plumbing ---------------------------------------------------------------------------------------------
    def say(self, msg: str) -> None:
        print(f"[easy {time.strftime('%H:%M:%S')}] {msg}", file=self.out, flush=True)

    def cmd(self, sub: str) -> str:
        return (f"python3 {shlex.quote(str(Path(__file__).resolve()))} "
                f"--config {shlex.quote(str(self.cfg['path']))} {sub}")

    def lock(self) -> None:
        if self._lockfd is not None:
            return
        try:
            for sub in ("", "state", "uploads", "actions", "logs", "tmp"):
                sm.private_dir(self.work / sub if sub else self.work)
        except (ValueError, OSError) as exc:
            raise Stop(f"work: {exc}", 2) from None
        fd = os.open(self.work / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise Stop("another easy_migrate.py is running on this work directory", 2) from None
        self._lockfd = fd
        self.state = self.load_state()

    def close(self) -> None:
        for srv in list(self.servers):
            self.unserve(srv)
        for fh in self._logs.values():
            fh.close()
        self._logs.clear()
        if self._lockfd is not None:
            os.close(self._lockfd)
            self._lockfd = None

    def load_state(self) -> dict:
        st = fresh_state()
        try:
            data = json.loads((self.work / "easy-migrate.json").read_text())
        except FileNotFoundError:
            return st
        except (OSError, ValueError) as exc:
            raise Stop(f"easy-migrate.json: {exc}", 2) from None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise Stop("easy-migrate.json was not written by this tool", 2)
        final = st["final"] | data.get("final", {})
        st.update(data)
        st["final"] = final
        return st

    def save_state(self) -> None:
        write_private(self.work / "easy-migrate.json",
                      (json.dumps(self.state, indent=2, sort_keys=True) + "\n").encode())

    def clear_from(self, step: str) -> None:
        st = self.state
        for s in STEPS[STEPS.index(step):]:
            st["done"].pop(s, None)
            st["armed"].pop(s, None)
        st["final"]["action_dir"] = None
        if step == "deliver":
            st["deliver_sent"] = False
        self.save_state()

    def logfile(self, name: str):
        fh = self._logs.get(name)
        if fh is None:
            path = self.work / "logs" / f"{name}-{self._tag}.log"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fh = self._logs[name] = os.fdopen(fd, "a", encoding="utf-8", errors="replace")
            self._logpaths[name] = path
        return fh

    def hook(self, name: str, *, quiet: bool = False, capture: bool = False):
        """Runs hooks.NAME with /bin/sh; (exit status, stdout if captured) or None when it is not configured."""
        cmd = self.cfg["hooks"].get(name)
        if cmd is None:
            return None
        log = self.logfile(f"hook-{name}")
        log.write(f"--- {now_iso()} step={self.step or '-'}\n")
        log.flush()
        env = dict(os.environ, EASY_STEP=self.step or "", EASY_WORK=str(self.work), EASY_BUNDLE=str(self.cfg["bundle"]),
                   EASY_SPI_DUMP=str(self.cfg["spi_dump"]), EASY_WIFI_VARS=str(self.cfg["wifi_vars"]))
        if self.bundle is not None:
            env.update(EASY_HOST=self.bundle.host, EASY_PORT=str(self.bundle.port))
        if not quiet:
            self.say(f"hook {name}")
        try:
            proc = subprocess.run(["/bin/sh", "-c", cmd], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE if capture else log, stderr=log, env=env, cwd=self.work,
                                  timeout=self.T.hook_max)
            rc, text = proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")
        except subprocess.TimeoutExpired as exc:
            rc, text = 124, (exc.stdout or b"").decode("utf-8", "replace") if capture else ""
        except OSError as exc:
            rc, text = 127, ""
            log.write(f"cannot run: {exc}\n")
        if capture:
            log.write(text)
        log.write(f"--- exit {rc}\n")
        log.flush()
        if rc != 0 and not quiet:
            self.say(f"hook {name} exited {rc} (log: {self._logpaths[f'hook-{name}']})")
        return rc, text

    def bs(self, *args) -> dict:
        """build_stockboot.py with the private state directory; its JSON result."""
        argv = [str(a) for a in args]
        proc = subprocess.run([sys.executable, str(BASE / "build_stockboot.py"), "--state", str(self.statedir), *argv],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=self.work)
        log = self.logfile("build_stockboot")
        log.write(f"--- {now_iso()} {' '.join(argv)} -> {proc.returncode}\n{proc.stdout}{proc.stderr}")
        log.flush()
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines()
            msg = lines[-1].removeprefix("build_stockboot: ") if lines else f"exit {proc.returncode}"
            raise Stop(f"build_stockboot {argv[0]}: {msg}", 1)
        return json.loads(proc.stdout)

    def new_action_dir(self, action: str) -> Path:
        while True:   # unique also across runs started within the same second
            self._seq += 1
            path = self.work / "actions" / f"{action}-{time.strftime('%Y%m%dT%H%M%S')}-{self._seq:02d}"
            if not os.path.lexists(path):
                return path

    # ---- the link to the camera and the server ------------------------------------------------------------
    def link_ok(self) -> bool:
        if not address_local(self.bundle.host):
            return False
        got = self.hook("camera_ap_check", quiet=True)
        return got is None or got[0] == 0

    def ensure_link(self) -> None:
        deadline = time.monotonic() + self.T.link_max
        while not self.link_ok():
            if time.monotonic() >= deadline:
                raise Stop(f"no link to the camera after {mins(self.T.link_max)}: this host needs {self.bundle.host} "
                           f"on the camera's Wi-Fi (hook camera_ap_up; logs in {self.work / 'logs'})", 1)
            self.say("bringing up the link to the camera (hook camera_ap_up)")
            self.hook("camera_ap_up", quiet=True)
            if not self.link_ok():
                time.sleep(max(0.0, min(self.T.link_retry, deadline - time.monotonic())))

    def keep_link(self) -> None:
        if not self.link_ok():
            self.say("the link to the camera is down; bringing it up again (hook camera_ap_up)")
            self.hook("camera_ap_up", quiet=True)

    def serve(self, mode: str, action_set=None, *, write: bool = False, reboot: bool = False) -> Server:
        self.ensure_link()
        wphrase, rphrase, _ = sm.ACTIONS.get(mode, (None, None, ()))
        out = Tee(self.out, self.logfile(f"serve-{mode}"))
        deadline = time.monotonic() + 30
        while True:
            try:
                srv = Server(self.bundle, mode, action_set=action_set, backup_dir=self.uploads,
                             write_confirm=wphrase if write else None, reboot_confirm=rphrase if reboot else None,
                             bind=self.bundle.host, port=self.bundle.port, out=out)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise Stop(f"cannot listen on {self.bundle.host}:{self.bundle.port} ({exc.strerror}); "
                               "is another serve_migration.py running?", 1) from None
                time.sleep(1)
        self.servers.append(srv)
        srv.start()
        kind = ("WRITE: the camera changes its flash" + (" and reboots" if reboot else "")) if write else \
            "read-only" if mode in sm.ACTIONS else "no action"
        srv.say(f"serving {mode} ({kind})")
        return srv

    def unserve(self, srv: Server) -> None:
        if srv in self.servers:
            self.servers.remove(srv)
            srv.stop()

    def watch(self, srv, check, timeout: float, *, hint: str = None, link: bool = True):
        """check() every poll until it returns something other than None; None after `timeout`."""
        t0 = last_link = last_hint = time.monotonic()
        while True:
            got = check()
            if got is not None:
                return got
            now = time.monotonic()
            if now - t0 >= timeout:
                return None
            if link and now - last_link >= self.T.link_retry:
                last_link = now
                self.keep_link()
            if hint and srv.quiet_for() >= self.T.hint_after and now - last_hint >= self.T.hint_after:
                last_hint = now
                self.say(hint.format(mins=mins(srv.quiet_for())))
            time.sleep(self.T.poll)

    def read_only(self, mode: str) -> None:
        """Serves one read-only action (diagnose, wdtgate, collect) and waits for its result."""
        srv = self.serve(mode)
        try:
            if not self.watch(srv, lambda: True if srv.consumed else None, self.T.start_max, hint=QUIET_HINT):
                raise Stop(f"the camera did not start {mode} within {mins(self.T.start_max)}; it runs its stock "
                           "firmware. Power-cycle it and run again", 1)
            rc = self.watch(srv, lambda: srv.action_rc, self.T.action_max)
        finally:
            self.unserve(srv)
        if rc is None:
            raise Stop(f"{mode} started but did not finish within {mins(self.T.action_max)} (read-only, nothing "
                       "written). Power-cycle the camera and run again", 1)
        if rc != 0:
            raise Stop(f"{mode} ended with rc {rc} (read-only, nothing written); logs in {self.work / 'logs'}", 1)

    def arm(self, srv: Server, step: str) -> None:
        """Waits for a write-and-reboot action (probe-arm, trial-arm): started, armed, rebooting."""
        mode = srv.mode
        if not self.watch(srv, lambda: True if srv.consumed else None, self.T.start_max, hint=QUIET_HINT):
            raise Stop(f"the camera did not start {mode} within {mins(self.T.start_max)}; nothing was written. "
                       "Power-cycle it and run again", 1)
        self.state["armed"][step] = now_iso()
        self.save_state()
        armed_at = []

        def check():
            if srv.action_rc is not None:
                return f"rc-{srv.action_rc}"
            if srv.seen("installer", f"rebooting-{mode}"):
                return "rebooting"
            if not armed_at and srv.seen("installer", f"armed-{mode}"):
                armed_at.append(time.monotonic())
            if armed_at and time.monotonic() - armed_at[0] >= self.T.armed_grace:
                return "armed"
            return None

        res = self.watch(srv, check, self.T.action_max)
        if res == "rebooting":   # the log upload comes first; rc 3 means the reboot did not happen
            late = self.watch(srv, lambda: f"rc-{srv.action_rc}" if srv.action_rc is not None else
                              "log" if srv.uploaded(f"log-{mode}") else None, self.T.reboot_log_wait, link=False)
            if late and late.startswith("rc-") and late != "rc-0":
                res = late
        if res in ("rebooting", "armed", "rc-0"):
            self.say(f"{mode}: armed; the camera reboots")
        elif res == "rc-3":
            self.say(f"{mode}: armed and verified, but the camera did not reboot by itself: POWER-CYCLE IT NOW")
        elif res == "rc-1":
            self.state["armed"].pop(step, None)
            self.save_state()
            raise Stop(f"the camera refused {mode} before writing anything (logs in {self.work / 'logs'})", 1)
        elif res is None:
            raise Stop(f"{mode} started but did not report back within {mins(self.T.action_max)}. Power-cycle the "
                       "camera and run again: the result is read back, the step is not armed twice", 1)
        else:
            raise Stop(f"{mode} could not verify its env write ({res}). The vendor U-Boot then boots stock with its "
                       "built-in env; run `restore-env`, then `run` again", 1)

    # ---- checks -----------------------------------------------------------------------------------------------
    def dump_ok(self) -> bool:
        d = self.cfg["spi_dump"]
        return d.is_file() and d.stat().st_size == bs.SPI_BYTES

    def preflight(self) -> list:
        c, problems = self.cfg, []
        problems += [f"{k}: must be outside the Git repository (private data)" for k in PATH_KEYS if sm.inside_repo(c[k])]
        if problems:
            return problems
        try:
            sm.private_dir(c["work"])
        except (ValueError, OSError) as exc:
            problems.append(f"work: {exc}")
        try:
            bundle = sm.load_bundle(c["bundle"])
        except (sm.BundleError, OSError, ValueError, KeyError) as exc:
            problems.append(f"bundle: {exc}")
        else:
            if bundle.files["payload/firmware.bin"].digests["sha256"] != FIRMWARE_SHA256:
                problems.append("bundle: payload/firmware.bin is not the pinned OpenIPC image")
            try:
                oenv = dict(sl.parse_env(bundle.files["payload/env-openipc.img"].data, sl.OIPC_ENV_LEN))
            except sl.LayoutError as exc:
                problems.append(f"bundle: env-openipc.img: {exc}")
            else:
                if oenv.get("sensor") != make_env.DEFAULT_SENSOR:
                    problems.append(SENSOR_MSG)
                if not oenv.get("upgrade"):
                    problems.append(UPGRADE_MSG)
            try:
                ipaddress.IPv4Address(bundle.host)
                self.bundle = bundle
            except ValueError:
                problems.append("bundle: the host the camera calls is not an IPv4 address")
        try:
            if hashlib.sha256(c["firmware"].read_bytes()).hexdigest() != FIRMWARE_SHA256:
                problems.append("firmware: not the pinned OpenIPC image (build_stockboot.FIRMWARE_SHA256)")
        except OSError as exc:
            problems.append(f"firmware: {exc.strerror}")
        try:
            wifi = make_env.parse_private_vars(c["wifi_vars"])
            if not (wifi.get("wlanssid") and wifi.get("wlanpass")):
                problems.append("wifi_vars: needs wlanssid and wlanpass (the Wi-Fi the trial joins)")
        except (make_env.EnvError, OSError, UnicodeDecodeError) as exc:
            problems.append(f"wifi_vars: {exc}")
        if not (c["oipc_rootfs"] / "lib" / "modules").is_dir():
            problems.append("oipc_rootfs: not an unpacked OpenIPC rootfs (no lib/modules)")
        if not c["auth_runtime"].is_file():
            problems.append("auth_runtime: missing")
        if not shutil.which(c["mksquashfs"]):
            problems.append(f"mksquashfs: {c['mksquashfs']} not found or not executable (squashfs-tools)")
        d = c["spi_dump"]
        if os.path.lexists(d):
            if not self.dump_ok():
                problems.append("spi_dump: exists but is not an 8 MiB file (move it away)")
        elif "backup" not in c["hooks"]:
            problems.append("spi_dump: no 8 MiB SPI image and no backup hook to make one")
        try:
            ps.build_script("apply", reboot=True)   # reads every file post_setup puts on the camera
        except (OSError, ValueError) as exc:
            problems.append(f"post_setup: {exc}")
        return problems

    def require_preflight(self) -> None:
        problems = self.preflight()
        for p in problems:
            self.say("preflight: " + p)
        if problems:
            raise Stop(f"{len(problems)} preflight problem(s); fix them and run again", 2)

    def camera_ip(self) -> str:
        try:
            return str(ipaddress.IPv4Address((self.work / "camera-address.txt").read_text().strip()))
        except (OSError, ValueError):
            raise Stop("the camera's address is not known yet: `run --again boot`, or `owner-done --camera-ip IP`",
                       1) from None

    # ---- steps ------------------------------------------------------------------------------------------------
    def step_backup(self, **_) -> None:
        dump = self.cfg["spi_dump"]
        if not self.dump_ok():
            if "backup" not in self.cfg["hooks"]:
                raise Stop(f"no 8 MiB SPI image at {dump} and no backup hook", 1)
            self.ensure_link()
            self.say("reading the camera's SPI flash (hook backup, read-only)")
            if self.hook("backup")[0] != 0 or not self.dump_ok():
                raise Stop(f"the backup hook did not leave an 8 MiB SPI image at {dump}; nothing was written", 1)
        if dump.stat().st_mode & 0o077:
            os.chmod(dump, 0o600)
        self.say(f"SPI image {dump}: 8 MiB, sha256 {hashlib.sha256(dump.read_bytes()).hexdigest()[:16]}...")

    def step_deliver(self, **_) -> None:
        srv = self.serve("standby")
        try:
            if self.state["deliver_sent"]:
                self.say("the hook was delivered before; waiting for the camera to call in "
                         "(`run --again deliver` sends it again)")
            else:
                self.state["deliver_sent"] = True
                self.save_state()
                self.say("sending the migration hook to the stock firmware (hook deliver)")
                if self.hook("deliver")[0] != 0:
                    self.state["deliver_sent"] = False
                    self.save_state()
                    raise Stop("the deliver hook failed (see its log); run again to retry", 1)
            called = self.watch(srv, lambda: True if any(p in ("1", "2") for _, p, _ in srv.event_list()) else None,
                                self.T.standby_max, hint=DELIVER_HINT)
        finally:
            self.unserve(srv)
        if not called:
            raise Stop(f"the camera did not call in within {mins(self.T.standby_max)} after the delivery; "
                       "power-cycle it and run again (the hook is not sent twice)", 1)
        self.say("the camera calls in from its stock firmware")

    def step_diagnose(self, **_) -> None:
        self.say("diagnose (read-only): flash, env, U-Boot, Wi-Fi and watchdog")
        self.read_only("diagnose")
        res = self.bs("diag", "--uploads", self.uploads, "--spi-dump", self.cfg["spi_dump"])
        problems = res.get("problems", [])
        if not res.get("ok") and len(problems) == 1 and problems[0].startswith(UBOOT_PROBLEM):
            ref = self.reference_image()
            if ref is not None:
                res = self.bs("diag", "--uploads", self.uploads, "--spi-dump", ref)
        if not res.get("ok"):
            raise Stop("diagnose found problems (nothing was written): " + "; ".join(res.get("problems", [])), 1)
        for note in res.get("notes", []):
            self.say("note: " + note)
        self.say("diagnose passed")

    def reference_image(self):
        try:
            uboot = bs.newest_upload(self.uploads, "diag-uboot")
        except bs.StateError:
            return None
        ref = compose_reference(self.cfg["spi_dump"].read_bytes(), uboot)
        if ref is None:
            return None
        path = self.work / "spi-reference-8m.bin"
        write_private(path, ref)
        self.state["spi_reference"] = str(path)
        self.save_state()
        self.say(f"the camera's U-Boot differs from the SPI image only in the last {UBOOT_TAIL} bytes of its "
                 f"partition (0xFF in the image). Wrote {path}: the camera's own U-Boot plus the rest of the SPI "
                 "image. Use this file if the camera ever has to be written back over SPI")
        return path

    def step_wdtgate(self, **_) -> None:
        self.say("watchdog gate (read-only): the camera stops all its processes and holds the watchdog off for "
                 "5 min, then resumes")
        self.read_only("wdtgate")
        res = self.bs("wdtgate", "--uploads", self.uploads)
        if not res.get("ok"):
            bad = [k for k, v in res.get("checks", {}).items() if not v]
            raise Stop("the watchdog gate did not pass: " + ", ".join(bad), 1)
        self.say(f"watchdog gate passed (held {res.get('held_seconds')} s)")

    def step_probe(self, **_) -> None:
        if "probe" in self.state["armed"]:
            self.say("the probe was armed before; reading its result (it is not armed twice)")
        else:
            res = self.bs("probe", "--out", self.new_action_dir("probe-arm"))
            aset = sm.load_action_dir(res["out"], "probe-arm")
            self.say("saveenv probe: the camera writes a probe env; at the next boot U-Boot saves it once and "
                     "boots stock")
            srv = self.serve("probe-arm", aset, write=True, reboot=True)
            try:
                self.arm(srv, "probe")
            finally:
                self.unserve(srv)
        self.read_only("collect")
        res = self.bs("collect", "--uploads", self.uploads, "--stage", "probe")
        if not res.get("ok"):
            raise Stop(f"the saveenv probe did not pass ({res.get('reason')}). The camera boots stock; "
                       "`restore-env` writes the vendor env back, `run --again probe` tries again", 1)
        self.say("saveenv probe passed")

    def step_trial(self, **_) -> None:
        if "trial" in self.state["armed"]:
            self.say("the one-shot trial was armed before; reading its result (no new trial)")
        else:
            c = self.cfg
            res = self.bs("trial", "--firmware", c["firmware"], "--oipc-rootfs", c["oipc_rootfs"],
                          "--auth-runtime", c["auth_runtime"], "--wifi-vars", c["wifi_vars"],
                          "--mksquashfs", c["mksquashfs"], "--workdir", self.work / "tmp",
                          "--out", self.new_action_dir("trial-arm"))
            aset = sm.load_action_dir(res["out"], "trial-arm")
            self.say("one-shot trial: OpenIPC boots once from a spare area and joins the trial Wi-Fi, then the "
                     f"camera boots stock again (payload margin {res.get('payload_margin_bytes')} B)")
            srv = self.serve("trial-arm", aset, write=True, reboot=True)
            try:
                self.arm(srv, "trial")
                self.trial_reports(srv)
            finally:
                self.unserve(srv)
        self.read_only("collect")
        res = self.bs("collect", "--uploads", self.uploads, "--stage", "trial")
        if not res.get("ok"):
            bad = [k for k, v in res.get("checks", {}).items() if not v]
            raise Stop(f"the OpenIPC trial did not pass ({', '.join(bad)}; result {res.get('result')}). The camera "
                       "boots stock. After fixing the cause, `run --again trial` arms a new one-shot trial", 1)
        self.say(f"trial passed (result {res.get('result')}, log from {res.get('log_source')})")

    def trial_reports(self, srv: Server) -> None:
        self.say("switching this host to the trial Wi-Fi (hook trial_ap_up); the trial reports there")
        try:
            if self.hook("trial_ap_up", quiet=True)[0] != 0:
                self.say("warning: the trial_ap_up hook failed; the trial returns to stock by itself either way")
            elif not address_local(self.bundle.host):
                self.say(f"warning: {self.bundle.host} is not on this host after trial_ap_up; the trial cannot report")
            ev = self.watch(srv, lambda: next((e for _, p, e in srv.event_list()
                                               if p == "trial" and e != "trial-network-up"), None),
                            self.T.trial_report_max, link=False)
            if ev is None:
                self.say(f"no trial result within {mins(self.T.trial_report_max)}; the camera boots stock by itself "
                         "and its log is read next")
            else:
                self.say(f"the trial reported {ev.removeprefix('trial-')}")
                self.watch(srv, lambda: True if srv.uploaded("trial-log") else None, self.T.trial_linger, link=False)
        finally:
            self.say("switching back (hook trial_ap_down)")
            self.hook("trial_ap_down", quiet=True)

    def final_action(self):
        fs = self.state["final"]
        if fs.get("action_dir"):
            try:
                return sm.load_action_dir(fs["action_dir"], "final")
            except (sm.BundleError, ValueError, OSError):
                pass
        res = self.bs("final", "--firmware", self.cfg["firmware"], "--out", self.new_action_dir("final"))
        aset = sm.load_action_dir(res["out"], "final")
        fs["action_dir"] = res["out"]
        self.save_state()
        return aset

    def step_final(self, *, yes_final: bool = False, **_) -> None:
        fs = self.state["final"]
        dump = self.state.get("spi_reference") or self.cfg["spi_dump"]
        if not yes_final:
            raise Stop(FINAL_GATE.format(dump=dump, cmd=self.cmd("run --yes-final")), 4)
        aset = self.final_action()
        restarts = 0
        while True:
            outcome = self.final_attempt(aset, first=restarts == 0)
            if outcome != "restart" or restarts >= MAX_FINAL_RESTARTS:
                break
            restarts += 1
            self.say(f"final: offering it to the camera again (retry {restarts} of {MAX_FINAL_RESTARTS})")
        if outcome in ("restart", "stalled") and fs["verified"]:
            outcome = "verified-reboot-unconfirmed"
        fs["outcome"] = outcome
        self.save_state()
        if outcome == "rebooting":
            self.say("final verified; the camera reboots into OpenIPC")
        elif outcome == "verified-reboot-unconfirmed":
            self.say("final verified; its reboot was not confirmed: if the camera does not show up within a few "
                     "minutes, power-cycle it")
        elif outcome == "verified-needs-power-cycle":
            self.say("FINAL VERIFIED, but the camera did not reboot by itself: power-cycle it now")
        elif outcome == "stopped-clean":
            raise Stop("the camera refused final before writing anything; the stock firmware is intact (see the "
                       "logs)", 1)
        elif outcome == "not-started":
            raise Stop(f"the camera did not start final within {mins(self.T.start_max)}; nothing was written. "
                       "Power-cycle it and run again", 1)
        elif fs["ponr"]:
            raise Stop(PONR_TEXT.format(cmd=self.cmd("run --yes-final"), dump=dump), 3)
        else:
            raise Stop("final stopped before its point of no return; the stock firmware is intact (see the logs)", 1)

    def final_attempt(self, aset, first: bool) -> str:
        fs = self.state["final"]
        fs["attempts"] += 1
        self.save_state()
        srv = self.serve("final", aset, write=True, reboot=True)
        try:
            def begun():
                if srv.consumed:
                    return "started"
                return next((e for _, p, e in srv.event_list() if p == "2" and e.startswith(PRE_START_FAILURES)), None)

            limit = self.T.start_max if first and not fs["ponr"] else self.T.final_retry_start
            got = self.watch(srv, begun, limit, hint=None if fs["ponr"] else QUIET_HINT)
            if got is None:
                return "restart" if fs["ponr"] else "not-started"
            if got != "started":
                self.say(f"final: the camera reported {got} before starting")
                return "restart"
            self.final_running = True
            self.say("final started on the camera: from its point of no return on, keep it powered")
            mark = {"n": 0, "last": time.monotonic()}

            def progress():
                evs = srv.event_list()
                for _, _, ev in evs[mark["n"]:]:
                    mark["last"] = time.monotonic()
                    if ev == "ponr" and not fs["ponr"]:
                        fs["ponr"] = True
                        self.save_state()
                    elif ev == "final-verified" and not fs["verified"]:
                        fs["verified"] = True
                        self.save_state()
                    elif ev == "rebooting-final":
                        return "rebooting"
                    elif ev == "hold-final-verified-power-cycle":
                        return "verified-needs-power-cycle"
                mark["n"] = len(evs)
                rc = srv.action_rc
                if rc is not None:
                    if rc in (0, 3) and fs["verified"]:
                        return "verified-needs-power-cycle"
                    return "stopped-clean" if rc == 1 and not fs["ponr"] else "restart"
                quiet = time.monotonic() - mark["last"]
                if fs["verified"] and quiet >= self.T.final_verified_quiet:
                    return "verified-reboot-unconfirmed"
                return "stalled" if quiet >= self.T.final_idle_max else None

            outcome = self.watch(srv, progress, float("inf"))
            if outcome == "rebooting":
                late = self.watch(srv, lambda: srv.action_rc if srv.action_rc is not None else
                                  -1 if srv.uploaded("log-final") else None, self.T.reboot_log_wait, link=False)
                if late == 3:
                    outcome = "verified-needs-power-cycle"
            self.final_running = False
            return outcome
        finally:
            self.unserve(srv)

    def find_camera(self) -> str:
        deadline = time.monotonic() + self.T.address_max
        while True:
            rc, out = self.hook("camera_address", quiet=True, capture=True)
            lines = out.strip().splitlines()
            if rc == 0 and lines:
                try:
                    return str(ipaddress.IPv4Address(lines[0].strip()))
                except ValueError:
                    pass
            if time.monotonic() >= deadline:
                raise Stop(f"the camera did not show up within {mins(self.T.address_max)} (hook camera_address). "
                           "If final reported verified, power-cycle the camera and run `run` again", 1)
            time.sleep(self.T.address_retry)

    def ping(self, ip: str):
        if not PING or not shutil.which(PING[0]):
            return None
        try:
            return subprocess.run([*PING, ip], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=15).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return None

    def step_boot(self, **_) -> None:
        self.say("joining the network the camera uses now (hook final_ap_up)")
        if self.hook("final_ap_up", quiet=True)[0] != 0:
            self.say("warning: the final_ap_up hook failed; looking for the camera anyway")
        ip = self.find_camera()
        write_private(self.work / "camera-address.txt", f"{ip}\n".encode())
        self.say(f"camera found at {ip}" + {True: " (answers ping)", False: " (no ping reply)", None: ""}[self.ping(ip)])
        deadline = time.monotonic() + self.T.service_max
        while True:
            ports = {name: tcp_open(ip, port) for name, port in SERVICE_PORTS.items()}
            if ports["ssh"] or ports["http"] or time.monotonic() >= deadline:
                break
            time.sleep(self.T.address_retry)
        pages = {p: http_status(ip, SERVICE_PORTS["http"], p) for p in ("/", "/image.jpg")} if ports["http"] else {}
        self.say("services: " + ", ".join(f"{n} {'open' if ok else 'closed'}" for n, ok in ports.items())
                 + "".join(f", GET {p} {s}" for p, s in pages.items()))
        if not (ports["ssh"] or ports["http"]):
            raise Stop(f"the camera at {ip} answers neither SSH nor HTTP after {mins(self.T.service_max)}; "
                       "`run --again boot` checks again", 1)

    def step_owner(self, **_) -> None:
        if not self.state["owner_done"]:
            raise Stop(OWNER_TEXT.format(ip=self.camera_ip(), cmd=self.cmd("owner-done")), 4)

    def run_post_setup(self, ip: str) -> int:
        ssh = self.cfg["camera_ssh"].replace("{ip}", ip)
        self.say(f"board settings, update guard and audio fix over SSH ({ssh}); ssh may ask for your password")
        return subprocess.run([sys.executable, str(POST_SETUP), "--ssh", ssh, "--reboot"]).returncode

    def step_post_setup(self, **_) -> None:
        rc = self.run_post_setup(self.camera_ip())
        if rc != 0:
            raise Stop(f"post_setup.py exited {rc}; fix the cause and run `run` again", 1)

    # ---- commands ---------------------------------------------------------------------------------------------
    def check(self) -> int:
        problems = self.preflight()
        for p in problems:
            self.say("problem: " + p)
        if problems:
            return 2
        self.say(f"preflight ok: bundle for {self.bundle.host}:{self.bundle.port}, pinned firmware, env-openipc with "
                 f"sensor and upgrade, SPI image {'present' if self.dump_ok() else 'to be made by the backup hook'}, "
                 f"post_setup {ps.VERSION} with all its files")
        return 0

    def apply_again(self, step: str) -> None:
        if step in AGAIN_BEFORE_FINAL:
            if self.state["final"]["ponr"] or "final" in self.state["done"]:
                raise Stop(f"--again {step}: final already passed its point of no return", 2)
            self.clear_from(step)
            self.say(f"--again {step}: {step} and the steps after it run again")
        elif step in AGAIN_ANY_TIME:
            self.state["done"].pop(step, None)
            self.save_state()
            self.say(f"--again {step}: {step} runs again")
        else:
            raise Stop(f"--again {step} is not possible", 2)

    def run(self, *, until: str = None, again: str = None, yes_final: bool = False) -> int:
        self.lock()
        self.require_preflight()
        if again:
            self.apply_again(again)
        if until and until in self.state["done"]:
            self.say(f"{until} is already done")
            return 0
        for step in STEPS:
            if step in self.state["done"]:
                continue
            self.step = step
            self.say(f"== {step} ==")
            getattr(self, f"step_{step}")(yes_final=yes_final)
            self.state["done"][step] = now_iso()
            self.save_state()
            if step == until:
                self.say(f"stopped after {step} (--until)")
                return 0
        self.say(DONE_TEXT.format(ip=self.camera_ip()))
        return 0

    def owner_done(self, camera_ip: str = None) -> int:
        self.lock()
        self.require_preflight()
        if "final" not in self.state["done"]:
            raise Stop("owner-done comes after final", 2)
        if camera_ip:
            try:
                ip = str(ipaddress.IPv4Address(camera_ip))
            except ValueError:
                raise Stop("--camera-ip: not an IPv4 address", 2) from None
            write_private(self.work / "camera-address.txt", f"{ip}\n".encode())
            self.state["done"].setdefault("boot", now_iso())
        elif "boot" not in self.state["done"]:
            raise Stop("the camera was not found yet: `run` (boot step), or `owner-done --camera-ip IP`", 2)
        self.state["owner_done"] = True
        self.save_state()
        return self.run()

    def restore_env(self) -> int:
        self.lock()
        self.require_preflight()
        if self.state["final"]["ponr"] or "final" in self.state["done"]:
            raise Stop("restore-env is for before final only", 2)
        if "diagnose" not in self.state["done"]:
            raise Stop("restore-env needs a passed diagnose: it writes back the vendor env read then", 2)
        self.step = "restore-env"
        res = self.bs("restore", "--out", self.new_action_dir("restore-env"))
        srv = self.serve("restore-env", sm.load_action_dir(res["out"], "restore-env"), write=True)
        try:
            if not self.watch(srv, lambda: True if srv.consumed else None, self.T.start_max, hint=QUIET_HINT):
                raise Stop("the camera did not start restore-env; nothing was written", 1)
            rc = self.watch(srv, lambda: srv.action_rc, self.T.action_max)
            restored = srv.seen("installer", "env-restored")
        finally:
            self.unserve(srv)
        if rc != 0:
            raise Stop(f"restore-env ended with {'no result' if rc is None else f'rc {rc}'}; see the logs", 1)
        self.clear_from("diagnose")
        self.say(("vendor env restored and verified" if restored else "the vendor env was the stock one already")
                 + "; `run` starts again from diagnose")
        return 0

    def status(self) -> int:
        st = self.load_state()
        for s in STEPS:
            self.say(f"{s:<11} {st['done'].get(s, '-')}")
        fs = st["final"]
        self.say(f"final: {fs['attempts']} attempt(s), point of no return {'PASSED' if fs['ponr'] else 'not reached'}, "
                 f"verified {'yes' if fs['verified'] else 'no'}, outcome {fs['outcome'] or '-'}")
        if st["armed"]:
            self.say("armed (one-shot, read back next): " + ", ".join(sorted(st["armed"])))
        if st.get("spi_reference"):
            self.say(f"SPI recovery image: {st['spi_reference']}")
        try:
            self.say(f"camera address: {(self.work / 'camera-address.txt').read_text().strip()}")
        except OSError:
            pass
        nxt = next((s for s in STEPS if s not in st["done"]), None)
        self.say(f"next: {nxt}" if nxt else "all steps done")
        return 0


def execute(flow: Flow, args) -> int:
    try:
        if args.cmd == "check":
            return flow.check()
        if args.cmd == "status":
            return flow.status()
        if args.cmd == "restore-env":
            return flow.restore_env()
        if args.cmd == "owner-done":
            return flow.owner_done(args.camera_ip)
        return flow.run(until=args.until, again=args.again, yes_final=args.yes_final)
    except Stop as exc:
        flow.say(("WAITING FOR YOU: " if exc.code == 4 else "STOPPED: ") + str(exc))
        return exc.code
    except KeyboardInterrupt:
        fs = flow.state["final"]
        if flow.final_running or (fs["ponr"] and not fs["verified"]):
            flow.say("interrupted while final runs: KEEP THE CAMERA POWERED and run `run --yes-final` again")
            return 3
        flow.say("interrupted; the same command goes on from here")
        return 130
    finally:
        flow.close()


def main(argv=None, *, out=None, timing=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, required=True, help="private JSON config (see easy/config.example.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="preflight only")
    p = sub.add_parser("run", help="run the steps that are not done yet")
    p.add_argument("--yes-final", action="store_true", help="allow the migration step itself")
    p.add_argument("--until", choices=STEPS, help="stop after this step")
    p.add_argument("--again", choices=AGAIN_BEFORE_FINAL + AGAIN_ANY_TIME, help="run this step again")
    p = sub.add_parser("owner-done", help="after your first login on the camera: go on with post_setup")
    p.add_argument("--camera-ip", help="the camera's IPv4 address, if the boot step did not find it")
    sub.add_parser("restore-env", help="before final: write back the vendor env that diagnose read")
    sub.add_parser("status", help="show what is done")
    args = ap.parse_args(argv)
    os.umask(0o077)
    try:
        flow = Flow(args.config, out=out, timing=timing)
    except Stop as exc:
        print(f"easy_migrate: {exc}", file=sys.stderr)
        return exc.code
    return execute(flow, args)


if __name__ == "__main__":
    sys.exit(main())
