#!/usr/bin/env python3
"""LAN-side server for the stock-U-Boot OpenIPC migration of the Wi-Fi-only iBlack.

It only answers requests from camera/stage2.sh, camera/migrate_hook.sh and the
one-shot camera/trial.sh and never opens a connection to a camera. Runtime and
payload files come from a bundle written by build_migration_appfs.py; per-action
files (env images, trial image) from an --action-dir written by
build_stockboot.py. At start-up every file is re-hashed against its metadata,
the bundle against the pins baked into the camera's migrate.conf, and every
action file is re-validated with stock_layout.py; everything is then held in
memory, so later on-disk changes are never served.

One mode per process (= one camera action; it is offered once under a random
seq and withdrawn as soon as the camera reports that it started it):
  standby / abort   the camera keeps polling / stops polling on stock firmware
  diagnose          read-only: device info, per-block hashes, vendor env
  collect           read-only: trial-log mailbox, vendor env, block hashes
  wdtgate           flash untouched: every process stopped and the SP805 watchdog
                    off for minutes, then resumed (proves the watchdog fix)
  probe-arm         vendor env <- saveenv probe (needs --write-confirm)
  trial-arm         bakfs <- trial image, vendor env <- armed env (one-shot
                    OpenIPC kernel trial); also receives the trial's reports
  restore-env       vendor env <- the env read by diagnose
  final             OpenIPC env + firmware + overlay, vendor env last
--reboot-confirm additionally lets probe-arm/trial-arm/final reboot the camera.
Uploads are stored 0600 in --backup-dir. Run ids, hashes, file contents and
request URLs are never printed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_layout as sl  # noqa: E402

GO_PHRASE = "yes-stage-openipc-migration"
# action -> (write phrase, reboot phrase, files fetched by the camera)
ACTIONS = {
    "diagnose": (None, None, ()),
    "collect": (None, None, ()),
    "wdtgate": (None, None, ()),
    "probe-arm": ("yes-arm-saveenv-probe", "yes-reboot-for-saveenv-probe", ("env-probe.img",)),
    "trial-arm": ("yes-arm-one-shot-openipc-trial", "yes-reboot-into-openipc-trial", ("trial.img", "env-armed.img")),
    "restore-env": ("yes-restore-stock-uboot-env", None, ("env-base.img",)),
    "final": ("yes-migrate-this-camera-to-openipc", "yes-reboot-into-openipc", ("env-final.img",)),
}
MODES = ("standby", "abort", *ACTIONS)
BUNDLE_SCHEMA = "iblack-openipc-migrate-bundle/2"
ACTION_SCHEMA = "iblack-openipc-stockboot-action/1"
RUNTIME_FILES = ("runtime/busybox", "runtime/ld-musl.so", "runtime/om_stockboot.sh", "runtime/migrate_hook.sh")
PAYLOAD_FILES = ("payload/firmware.bin", "payload/env-openipc.img")
# migrate.conf key -> (served file, digest) that the camera checks it against.
CONF_PINS = {
    "OM_RT_BB_SHA1": ("runtime/busybox", "sha1"),
    "OM_RT_BB_MD5": ("runtime/busybox", "md5"),
    "OM_RT_BB_SHA256": ("runtime/busybox", "sha256"),
    "OM_RT_LD_SHA1": ("runtime/ld-musl.so", "sha1"),
    "OM_RT_LD_MD5": ("runtime/ld-musl.so", "md5"),
    "OM_RT_LD_SHA256": ("runtime/ld-musl.so", "sha256"),
    "OM_INSTALLER_SHA1": ("runtime/om_stockboot.sh", "sha1"),
    "OM_INSTALLER_MD5": ("runtime/om_stockboot.sh", "md5"),
    "OM_INSTALLER_SHA256": ("runtime/om_stockboot.sh", "sha256"),
    "OM_HOOK_SHA1": ("runtime/migrate_hook.sh", "sha1"),
    "OM_HOOK_MD5": ("runtime/migrate_hook.sh", "md5"),
    "OM_HOOK_SHA256": ("runtime/migrate_hook.sh", "sha256"),
    "OM_FW_SHA256": ("payload/firmware.bin", "sha256"),
    "OM_OENV_SHA256": ("payload/env-openipc.img", "sha256"),
}
MAX_FILE_BYTES = 8 * 1024 * 1024
DRAIN_MAX = 1024 * 1024  # a refused upload body is read (and dropped) up to this before the reply
# upload name -> (mode that accepts it, exact size or None, max decoded bytes, encoding: b64 or form)
UPLOADS = {
    "diag-info": ("diagnose", None, 512 * 1024, "b64"), "diag-blocks": ("diagnose", None, 64 * 1024, "b64"),
    "diag-env": ("diagnose", sl.SENV_LEN, sl.SENV_LEN, "b64"),
    "diag-uboot": ("diagnose", sl.UBOOT_LEN, sl.UBOOT_LEN, "b64"),
    "wdtgate-info": ("wdtgate", None, 16 * 1024, "b64"),
    "collect-info": ("collect", None, 16 * 1024, "b64"), "collect-tl": ("collect", sl.TL_LEN, sl.TL_LEN, "b64"),
    "collect-env": ("collect", sl.SENV_LEN, sl.SENV_LEN, "b64"),
    "mdat1": ("final", 0x20000, 0x20000, "b64"), "mdat2": ("final", 0x20000, 0x20000, "b64"),
    "trial-log": ("trial-arm", None, sl.TL_TEXT_LEN, "form"),
    **{f"log-{a}": (a, None, 256 * 1024, "b64") for a in ACTIONS},
}
# trial.sh has only the stock BusyBox 1.18.4 wget, which sends --post-data as application/x-www-form-urlencoded
# with every byte outside [A-Za-z0-9] and !&'()*-.=_~ as %XX (qemu run of the real applet). A log always holds
# spaces and newlines, so a body of only those characters and %XX is that form; any other body is taken as is.
FORM_BODY_RE = re.compile(rb"(?:[A-Za-z0-9!&'()*.=_~-]|%[0-9A-F]{2})+")
RUN_ID_RE = re.compile(r"[0-9a-f]{16,64}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
EVENT_RE = re.compile(r"[A-Za-z0-9._-]{1,96}")
PHASES = ("1", "2", "installer", "trial")
CONF_LINE_RE = re.compile(r"([A-Z][A-Z0-9_]*)='([A-Za-z0-9_.:/ -]*)'")
REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES = {
    "standby-timeout": "camera stopped polling; stock firmware continues",
    "aborted": "camera acknowledged abort; stock firmware continues",
    "idle-exit": "camera stopped polling (no new action); stock firmware continues",
    "diagnose-done": "read-only diagnose finished; next: build_stockboot.py diag",
    "collect-done": "read-only collect finished; next: build_stockboot.py collect",
    "wdtgate-quiesced": "every camera process stopped and the watchdog off: holding (flash untouched)",
    "wdtgate-done": "watchdog gate held and resumed; next: build_stockboot.py wdtgate",
    "rebooting-probe-arm": "camera reboots into the saveenv probe (stock comes back); then run collect",
    "rebooting-trial-arm": "camera reboots into the one-shot OpenIPC trial; wait for trial reports, then collect",
    "trial-network-up": "trial kernel reached the LAN host over Wi-Fi",
    "trial-completed": "trial finished; the camera reboots into stock",
    "env-restored": "vendor env restored and verified",
    "ponr": "FINAL PASSED ITS POINT OF NO RETURN: keep the camera powered",
    "final-verified": "all regions written and verified; the vendor env now boots OpenIPC",
    "stopped-dirty": "FLASH PARTLY REWRITTEN: keep power on; run final again or recover over SPI",
    "stopped-env-unverified": "vendor env unverified: the vendor default env boots stock; run collect",
    "hold-dirty-retry-final": "camera holds in a partial state: restart this server in final mode to retry",
    "hold-final-verified-power-cycle": "migration verified; power-cycle the camera to boot OpenIPC",
}


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class BundleFile:
    data: bytes
    digests: dict


@dataclass(frozen=True)
class Bundle:
    root: Path
    run_id: str
    host: str
    port: int
    files: dict
    conf: dict


@dataclass(frozen=True)
class ActionSet:
    action: str
    files: dict      # name -> bytes
    envlive: str
    uboot: str


def digests(data: bytes) -> dict:
    return {"size": len(data), "sha1": hashlib.sha1(data).hexdigest(),
            "md5": hashlib.md5(data).hexdigest(), "sha256": hashlib.sha256(data).hexdigest()}


def inside_repo(path) -> bool:
    try:
        Path(path).resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def read_regular(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise BundleError(f"{Path(path).name}: not a bounded regular file")
        return fh.read(limit + 1)


def parse_conf(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = CONF_LINE_RE.fullmatch(line)
        if not m or m.group(1) in out:
            raise BundleError("migrate.conf has an unexpected or duplicate line")
        out[m.group(1)] = m.group(2)
    return out


def check_oenv(img: bytes) -> dict:
    """The OpenIPC userland env (0x60000) must describe the final layout and carry the Wi-Fi keys."""
    try:
        env = dict(sl.parse_env(img, sl.OIPC_ENV_LEN))
    except sl.LayoutError as exc:
        raise BundleError(f"env-openipc.img: {exc}") from None
    if env.get("mtdparts") != sl.OIPC_ENV_LAYOUT["mtdparts"] or env.get("bootcmd") != sl.FINAL_BOOTCMD:
        raise BundleError("env-openipc.img does not describe the stock-U-Boot final layout "
                          "(make_env.py --layout stockboot)")
    if not all(env.get(k) for k in ("wlandev", "wlanssid", "wlanpass")):
        raise BundleError("env-openipc.img lacks wlandev/wlanssid/wlanpass (the unit is Wi-Fi only)")
    return env


def check_firmware(fw: bytes) -> dict:
    try:
        return sl.plan(fw)["firmware"]
    except sl.LayoutError as exc:
        raise BundleError(f"firmware.bin: {exc}") from None


def load_bundle(root) -> Bundle:
    root = Path(root)
    if inside_repo(root):
        raise BundleError("refusing a bundle inside the Git repository (it holds private payloads)")
    try:
        meta = json.loads(read_regular(root / "bundle.json", 1 << 20))
        conf = parse_conf(read_regular(root / "conf" / "migrate.conf", 1 << 16).decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"unreadable bundle metadata ({exc.__class__.__name__})") from None
    if not isinstance(meta, dict) or meta.get("schema") != BUNDLE_SCHEMA:
        raise BundleError("unknown bundle schema")
    run_id, port, host = meta.get("run_id"), meta.get("port"), meta.get("host")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise BundleError("bundle run_id must be 16-64 lowercase hex characters")
    if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(host, str):
        raise BundleError("bundle host/port are invalid")
    expected = list(RUNTIME_FILES + PAYLOAD_FILES)
    listed = meta.get("files")
    if not isinstance(listed, dict) or sorted(listed) != sorted(expected):
        raise BundleError("bundle file list differs from the protocol")
    files = {}
    for rel in expected:
        try:
            data = read_regular(root / "serve" / rel)
        except OSError as exc:
            raise BundleError(f"{rel}: {exc.__class__.__name__}") from None
        d = digests(data)
        if d["size"] != listed[rel].get("size") or d["sha256"] != listed[rel].get("sha256"):
            raise BundleError(f"{rel}: differs from bundle.json")
        files[rel] = BundleFile(data, d)
    check_firmware(files["payload/firmware.bin"].data)
    check_oenv(files["payload/env-openipc.img"].data)
    checks = {key: files[rel].digests[kind] for key, (rel, kind) in CONF_PINS.items()}
    checks.update({"OM_RUN_ID": run_id, "OM_PORT": str(port), "OM_HOST": host,
                   "OM_STAGE2_SHA256": meta.get("stage2_sha256")})
    for key, want in checks.items():
        if conf.get(key) != want:
            raise BundleError(f"migrate.conf {key} does not match the served bundle")
    return Bundle(root.resolve(), run_id, host, port, files, conf)


def validate_action_file(name: str, data: bytes) -> None:
    try:
        if name == "trial.img":
            if len(data) != sl.TRIAL_LEN:
                raise sl.LayoutError("trial.img must be exactly the bakfs size")
            k = sl.uimage_info(data)
            if k["total"] > sl.TK_LEN or data[sl.TK_LEN:sl.TK_LEN + 4] != b"hsqs":
                raise sl.LayoutError("trial.img: kernel too large or no SquashFS payload at 0x1d0000")
            if struct.unpack_from("<Q", data, sl.TK_LEN + 40)[0] > sl.TP_LEN:
                raise sl.LayoutError("trial payload exceeds the tp partition")
            return
        env = dict(sl.parse_env(data))
        want = {"env-probe.img": sl.probe_bootcmd(sl.STOCK_BOOTCMD),
                "env-armed.img": sl.trial_bootcmd(sl.STOCK_BOOTCMD),
                "env-base.img": sl.STOCK_BOOTCMD, "env-final.img": sl.FINAL_BOOTCMD}[name]
        if env.get("bootcmd") != want:
            raise sl.LayoutError(f"{name} does not carry the planned bootcmd")
        if name == "env-final.img" and (env.get("bootargs") != sl.FINAL_BOOTARGS or env.get("verify") != "y"):
            raise sl.LayoutError("env-final.img lacks the final bootargs or verify=y")
        if name != "env-armed.img" and any(sl.TRIAL_MARK in f"{k}={v}" for k, v in env.items()):
            raise sl.LayoutError(f"{name} carries the trial marker")
    except sl.LayoutError as exc:
        raise BundleError(f"action file {name}: {exc}") from None


def load_action_dir(path, action: str) -> ActionSet:
    path = Path(path)
    if inside_repo(path):
        raise BundleError("refusing an action directory inside the Git repository")
    try:
        meta = json.loads(read_regular(path / "action.json", 1 << 16))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"unreadable action.json ({exc.__class__.__name__})") from None
    if not isinstance(meta, dict) or meta.get("schema") != ACTION_SCHEMA or meta.get("action") != action:
        raise BundleError(f"action directory is not a {action} set from build_stockboot.py")
    listed = meta.get("files")
    if not isinstance(listed, dict) or sorted(listed) != sorted(ACTIONS[action][2]):
        raise BundleError("action file list differs from the protocol")
    for key in ("envlive", "uboot"):
        if not isinstance(meta.get(key), str) or not HEX64_RE.fullmatch(meta[key]):
            raise BundleError(f"action.json {key} must be a sha256 digest")
    files = {}
    for name, want in listed.items():
        try:
            data = read_regular(path / name)
        except OSError as exc:
            raise BundleError(f"{name}: {exc.__class__.__name__}") from None
        if (not isinstance(want, dict) or len(data) != want.get("size")
                or hashlib.sha256(data).hexdigest() != want.get("sha256")):
            raise BundleError(f"{name}: differs from action.json")
        validate_action_file(name, data)
        files[name] = data
    return ActionSet(action, files, meta["envlive"], meta["uboot"])


def private_dir(path) -> Path:
    path = Path(path)
    if inside_repo(path):
        raise ValueError("refusing a backup directory inside the Git repository")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("backup directory must be a 0700 directory owned by the current user")
    return path.resolve()


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def decode_post_data(body: bytes) -> bytes:
    """A wget --post-data body as the camera script had it (see FORM_BODY_RE)."""
    return unquote_to_bytes(body) if FORM_BODY_RE.fullmatch(body) else body


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "openipc-migrate"
    sys_version = ""
    timeout = 60

    def log_message(self, fmt, *args):  # request lines carry the run id
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _refuse(self, code: int, body: bytes) -> None:
        """Replies to a POST whose body was not read yet: closing on unread data resets the connection, and the
        client may then never see the status, so up to DRAIN_MAX of the body is read and dropped first."""
        try:
            left = min(max(int(self.headers.get("Content-Length", "0")), 0), DRAIN_MAX)
        except ValueError:
            left = 0
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        self._send(code, body)

    def _authorized(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)
        app = self.server.app
        run = query.get("run", [])
        if len(run) != 1 or not hmac.compare_digest(run[0].encode("utf-8", "replace"), app.bundle.run_id.encode()):
            app.note_refused()
            self._send(403, b"forbidden\n")
            return None
        return parts.path, query

    def do_GET(self):
        got = self._authorized()
        if got is None:
            return
        path, query = got
        app = self.server.app
        if path == "/manifest":
            self._send(200, app.manifest())
        elif path == "/status":
            phase, event = query.get("phase", [""])[0], query.get("event", [""])[0]
            if phase not in PHASES or not EVENT_RE.fullmatch(event):
                self._send(400, b"bad event\n")
                return
            app.record(phase, event)
            self._send(200, b"ok\n")
        else:
            data = app.servable(path[1:])
            if data is None:
                self._send(404 if not app.go else 403, b"not served in this mode\n")
                return
            app.count_served(path[1:], len(data))
            self._send(200, data, "application/octet-stream")

    def do_POST(self):
        got = self._authorized()
        if got is None:
            return
        path, _ = got
        app = self.server.app
        name = path[len("/upload/"):] if path.startswith("/upload/") else None
        spec = UPLOADS.get(name)
        if spec is None:
            self._refuse(404, b"not found\n")
            return
        mode, exact, limit, enc = spec
        if mode != app.mode:
            self._refuse(403, b"upload not accepted in this mode\n")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(411, b"length required\n")
            return
        # BusyBox base64 wraps its output every 76 characters (57 input bytes).
        if not 0 < length <= (3 * limit if enc == "form" else limit * 4 // 3 + limit // 57 + 4096):
            self._refuse(413, b"bad length\n")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._send(400, b"short body\n")
            return
        if app.fail_uploads:  # offline fault injection only
            self._send(500, b"upload rejected\n")
            return
        if enc == "form":
            data = decode_post_data(body)
        else:
            try:
                data = base64.b64decode(b"".join(body.split()), validate=True)
            except (binascii.Error, ValueError):
                self._send(400, b"bad base64\n")
                return
        if (exact is not None and len(data) != exact) or len(data) > limit or not data:
            self._send(422, b"unexpected size\n")
            return
        digest = app.save_upload(name, data)
        self._send(200, (digest + "\n").encode() if enc == "b64" else b"ok\n")


class MigrationServer:
    """One camera action per process: fixed mode, in-memory files, private upload dir."""

    def __init__(self, bundle: Bundle, mode: str, *, action_set: ActionSet = None, backup_dir=None,
                 write_confirm=None, reboot_confirm=None, bind: str = "127.0.0.1", port: int = 0, out=None):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        wphrase, rphrase, needed = ACTIONS.get(mode, (None, None, ()))
        if write_confirm is not None and write_confirm != wphrase:
            raise ValueError(f"--write-confirm does not match mode {mode}"
                             + (f" (expected {wphrase})" if wphrase else ""))
        if wphrase and write_confirm is None:
            raise ValueError(f"mode {mode} writes flash: pass --write-confirm {wphrase}")
        if reboot_confirm is not None and (rphrase is None or reboot_confirm != rphrase):
            raise ValueError(f"--reboot-confirm does not match mode {mode}"
                             + (f" (expected {rphrase})" if rphrase else ""))
        if needed and (action_set is None or action_set.action != mode):
            raise ValueError(f"mode {mode} needs --action-dir from build_stockboot.py {mode}")
        if not needed and action_set is not None:
            raise ValueError(f"mode {mode} takes no --action-dir")
        self.bundle, self.mode, self.action_set = bundle, mode, action_set
        self.write, self.reboot = write_confirm is not None, reboot_confirm is not None
        self.seq = secrets.token_hex(8)
        self.consumed = False
        if self.go and backup_dir is None:
            raise ValueError("--backup-dir is required in every action mode")
        # Optional standby/abort event persistence does not permit files or uploads.
        self.backup_dir = private_dir(backup_dir) if backup_dir is not None else None
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.events, self.served, self.uploads = [], {}, {}
        self.refused = self.saved = 0
        self.action_rc = None
        self.fail_uploads = False
        self.httpd = _HTTPServer((bind, port), _Handler)
        self.httpd.app = self
        self._thread = None

    @property
    def go(self) -> bool:
        return self.mode in ACTIONS

    @property
    def address(self):
        return self.httpd.server_address[0], self.httpd.server_address[1]

    def say(self, msg: str) -> None:
        print(f"[serve {time.strftime('%H:%M:%S')}] {msg}", file=self.out, flush=True)

    def manifest(self) -> bytes:
        lines = [f"run={self.bundle.run_id}"]
        with self.lock:
            consumed = self.consumed
        if not self.go or consumed:
            lines.append(f"mode={'abort' if self.mode == 'abort' else 'standby'}")
        else:
            lines += ["mode=go", f"go={GO_PHRASE}", f"action={self.mode}", f"seq={self.seq}"]
            if self.action_set is not None:
                lines += [f"uboot={self.action_set.uboot}", f"envlive={self.action_set.envlive}"]
                lines += [f"f.{n}={hashlib.sha256(d).hexdigest()}" for n, d in sorted(self.action_set.files.items())]
            wphrase, rphrase, _ = ACTIONS[self.mode]
            if self.write:
                lines.append(f"write={wphrase}")
                if self.reboot:
                    lines.append(f"reboot={rphrase}")
        return ("\n".join(lines) + "\n").encode()

    def servable(self, rel: str):
        if not self.go:
            return None
        if rel in RUNTIME_FILES or (self.mode == "final" and rel in PAYLOAD_FILES):
            return self.bundle.files[rel].data
        if rel.startswith("action/") and self.action_set is not None:
            return self.action_set.files.get(rel[len("action/"):])
        return None

    def note_refused(self) -> None:
        with self.lock:
            self.refused += 1
        self.say("refused a request without the expected run id")

    def record(self, phase: str, event: str) -> None:
        with self.lock:
            self.events.append((time.time(), phase, event))
            if phase == "2" and event == f"action-start-{self.mode}" and not self.consumed:
                self.consumed = True
                withdrawn = True
            else:
                withdrawn = False
            m = re.fullmatch(r"action-rc-(.+)-(\d+)", event)
            if phase == "2" and m and m.group(1) == self.mode:
                self.action_rc = int(m.group(2))
        note = NOTES.get(event) or ("FINAL INCOMPLETE: keep power on; run final again or recover over SPI"
                                    if event.startswith("final-degraded") else None)
        self.say(f"phase={phase} event={event}" + (f" -- {note}" if note else ""))
        if withdrawn:
            self.say(f"{self.mode} started on the camera; action withdrawn (one-shot)")
        if self.backup_dir is not None:
            self._append_event(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {phase} {event}\n")

    def _append_event(self, line: str) -> None:
        with self.lock:
            fd = os.open(self.backup_dir / "events.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "a") as fh:
                fh.write(line)

    def count_served(self, rel: str, size: int) -> None:
        with self.lock:
            self.served[rel] = self.served.get(rel, 0) + 1
        self.say(f"served {rel} ({size} B)")

    def save_upload(self, name: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            self.saved += 1
            fname = f"{name}-{time.strftime('%Y%m%dT%H%M%S')}-{self.saved:03d}.bin"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.backup_dir / fname, flags, 0o600)
            with os.fdopen(fd, "wb") as fh:  # durable before the camera is told to go on
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            self.uploads.setdefault(name, []).append(digest)
        self.say(f"upload {name}: {len(data)} B stored privately (0600)")
        return digest

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True, help="output directory of build_migration_appfs.py")
    ap.add_argument("--mode", choices=MODES, default="standby")
    ap.add_argument("--action-dir", type=Path, help="output of build_stockboot.py for this mode")
    ap.add_argument("--bind", help="listen address (default: the OM_HOST baked into the appfs)")
    ap.add_argument("--port", type=int, help="listen port (default: the OM_PORT baked into the appfs)")
    ap.add_argument("--backup-dir", type=Path, help="private directory for uploads (diag, collect, mdat, logs)")
    ap.add_argument("--write-confirm", metavar="PHRASE")
    ap.add_argument("--reboot-confirm", metavar="PHRASE")
    args = ap.parse_args(argv)
    os.umask(0o077)
    try:
        bundle = load_bundle(args.bundle)
        action_set = load_action_dir(args.action_dir, args.mode) if args.action_dir else None
        bind = args.bind or bundle.host
        port = bundle.port if args.port is None else args.port
        srv = MigrationServer(bundle, args.mode, action_set=action_set, backup_dir=args.backup_dir,
                              write_confirm=args.write_confirm, reboot_confirm=args.reboot_confirm,
                              bind=bind, port=port)
    except (BundleError, ValueError, OSError) as exc:
        print(f"serve_migration: {exc}", file=sys.stderr)
        return 2
    host, port = srv.address
    srv.say(f"mode={args.mode} listening on {host}:{port}; Ctrl-C stops")
    if (host, port) != (bundle.host, bundle.port):
        srv.say(f"note: the camera calls {bundle.host}:{bundle.port} (baked into its appfs)")
    if srv.write:
        srv.say(f"WRITE MODE {args.mode}: the camera will modify its flash" + (" and reboot" if srv.reboot else ""))
    try:
        srv.httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
