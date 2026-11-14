#!/usr/bin/env python3
"""LAN-side server for the online stock iBlack -> OpenIPC migration.

It only answers requests from camera/stage2.sh and camera/migrate_hook.sh and
never opens a connection to a camera. Everything it serves comes from a bundle
written by build_migration_appfs.py. At start-up each file is re-hashed against
bundle.json AND against the pins in the bundled copy of migrate.conf (the values
baked into the camera's appfs), then held in memory, so later on-disk changes
are never served.

Modes are fixed for the lifetime of the process:
  standby   manifest mode=standby: the camera keeps polling on stock firmware.
            Runtime, payload and upload endpoints stay closed.
  abort     manifest mode=abort: phase1 stops polling; stock firmware continues.
  rehearse  GO phrase only: the camera fetches the RAM runtime and payloads and
            runs the installer read-only (stability check, private backup,
            export to --backup-dir, write plan), then resumes the stock app.
  write     GO + write phrase (needs --write-confirm): the camera rewrites its
            flash. --reboot-confirm also lets it reboot into OpenIPC once every
            region verified; otherwise it holds and reports until power-cycled.
Run ids, hashes, backup bytes and request URLs are never printed.
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
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

GO_PHRASE = "yes-stage-openipc-migration"
WRITE_PHRASE = "yes-migrate-this-camera-to-openipc"
REBOOT_PHRASE = "yes-reboot-into-openipc"
MODES = ("standby", "abort", "rehearse", "write")
BUNDLE_SCHEMA = "iblack-openipc-migrate-bundle/1"
RUNTIME_FILES = ("runtime/busybox", "runtime/ld-musl.so",
                 "runtime/openipc_migrate.sh", "runtime/migrate_hook.sh")
PAYLOAD_FILES = ("payload/firmware.bin", "payload/uboot.bin")
ENV_FILE = "payload/env.img"
# migrate.conf key -> (served file, digest) that the camera checks it against.
CONF_PINS = {
    "OM_RT_BB_SHA1": ("runtime/busybox", "sha1"),
    "OM_RT_BB_MD5": ("runtime/busybox", "md5"),
    "OM_RT_BB_SHA256": ("runtime/busybox", "sha256"),
    "OM_RT_LD_SHA1": ("runtime/ld-musl.so", "sha1"),
    "OM_RT_LD_MD5": ("runtime/ld-musl.so", "md5"),
    "OM_RT_LD_SHA256": ("runtime/ld-musl.so", "sha256"),
    "OM_INSTALLER_SHA1": ("runtime/openipc_migrate.sh", "sha1"),
    "OM_INSTALLER_MD5": ("runtime/openipc_migrate.sh", "md5"),
    "OM_INSTALLER_SHA256": ("runtime/openipc_migrate.sh", "sha256"),
    "OM_HOOK_SHA1": ("runtime/migrate_hook.sh", "sha1"),
    "OM_HOOK_MD5": ("runtime/migrate_hook.sh", "md5"),
    "OM_HOOK_SHA256": ("runtime/migrate_hook.sh", "sha256"),
    "OM_FW_SHA256": ("payload/firmware.bin", "sha256"),
    "OM_UBOOT_SHA256": ("payload/uboot.bin", "sha256"),
}
MAX_FILE_BYTES = 8 * 1024 * 1024
MDAT_BYTES = 0x20000
MAX_UPLOAD_BYTES = 256 * 1024
MAX_REPORT_BYTES = 256 * 1024
RUN_ID_RE = re.compile(r"[0-9a-f]{16,64}")
EVENT_RE = re.compile(r"[A-Za-z0-9._-]{1,96}")
PHASES = ("1", "2", "installer")
CONF_LINE_RE = re.compile(r"([A-Z][A-Z0-9_]*)='([A-Za-z0-9_.:/ -]*)'")
REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES = {
    "standby-timeout": "camera stopped polling; stock firmware continues",
    "aborted": "camera acknowledged abort; stock firmware continues",
    "rehearsal-ok": "rehearsal finished, stock app resumed, nothing was written",
    "migrated-reboot-withheld": "all regions written and verified; power-cycle the camera to boot OpenIPC",
    "migrated-reboot-failed": "verified but the reboot failed; power-cycle the camera",
    "partial-state": "PARTIAL STATE: flash partly rewritten; keep power on, prepare UART/SPI recovery",
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
    env_mode: str
    files: dict
    conf: dict


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
    run_id, env_mode, port, host = meta.get("run_id"), meta.get("env_mode"), meta.get("port"), meta.get("host")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise BundleError("bundle run_id must be 16-64 lowercase hex characters")
    if env_mode not in ("image", "erase"):
        raise BundleError("bundle env_mode must be image or erase")
    if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(host, str):
        raise BundleError("bundle host/port are invalid")
    expected = list(RUNTIME_FILES + PAYLOAD_FILES) + ([ENV_FILE] if env_mode == "image" else [])
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
    checks = {key: files[rel].digests[kind] for key, (rel, kind) in CONF_PINS.items()}
    checks.update({"OM_RUN_ID": run_id, "OM_ENV_MODE": env_mode, "OM_PORT": str(port), "OM_HOST": host,
                   "OM_ENV_SHA256": files[ENV_FILE].digests["sha256"] if env_mode == "image" else "",
                   "OM_STAGE2_SHA256": meta.get("stage2_sha256")})
    for key, want in checks.items():
        if conf.get(key) != want:
            raise BundleError(f"migrate.conf {key} does not match the served bundle")
    return Bundle(root.resolve(), run_id, host, port, env_mode, files, conf)


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
        elif path[1:] in app.bundle.files:
            if not app.serving:
                self._send(403, b"no GO in this mode\n")
                return
            app.count_served(path[1:])
            self._send(200, app.bundle.files[path[1:]].data, "application/octet-stream")
        else:
            self._send(404, b"not found\n")

    def do_POST(self):
        got = self._authorized()
        if got is None:
            return
        path, _ = got
        app = self.server.app
        if path not in ("/upload/mdat1", "/upload/mdat2", "/report"):
            self._send(404, b"not found\n")
            return
        if not app.serving:
            self._send(403, b"no GO in this mode\n")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(411, b"length required\n")
            return
        if not 0 < length <= (MAX_REPORT_BYTES if path == "/report" else MAX_UPLOAD_BYTES):
            self._send(413, b"bad length\n")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._send(400, b"short body\n")
            return
        if path == "/report":
            app.save_report(body)
            self._send(200, b"ok\n")
            return
        if app.fail_uploads:  # offline fault injection only
            self._send(500, b"upload rejected\n")
            return
        try:
            data = base64.b64decode(b"".join(body.split()), validate=True)
        except (binascii.Error, ValueError):
            self._send(400, b"bad base64\n")
            return
        if len(data) != MDAT_BYTES:
            self._send(422, b"backup must be exactly 128 KiB\n")
            return
        digest = app.save_backup(path.rsplit("/", 1)[1], data)
        self._send(200, (digest + "\n").encode())


class MigrationServer:
    """One migration run: fixed mode, in-memory bundle, private backup dir."""

    def __init__(self, bundle: Bundle, mode: str, *, backup_dir=None, write_confirm=None,
                 reboot_confirm=None, bind: str = "127.0.0.1", port: int = 0, out=None):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if (mode == "write") != (write_confirm is not None) or write_confirm not in (None, WRITE_PHRASE):
            raise ValueError(f"write mode needs --write-confirm {WRITE_PHRASE} (and only write mode takes it)")
        if reboot_confirm is not None and (mode != "write" or reboot_confirm != REBOOT_PHRASE):
            raise ValueError(f"--reboot-confirm {REBOOT_PHRASE} is only valid in write mode")
        self.bundle, self.mode, self.reboot = bundle, mode, reboot_confirm is not None
        self.backup_dir = None
        if mode in ("rehearse", "write"):
            if backup_dir is None:
                raise ValueError("--backup-dir is required in rehearse and write modes")
            self.backup_dir = private_dir(backup_dir)
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.events, self.served, self.backups, self.reports = [], {}, {}, []
        self.refused = self.saved = 0
        self.fail_uploads = False
        self.httpd = _HTTPServer((bind, port), _Handler)
        self.httpd.app = self
        self._thread = None

    @property
    def serving(self) -> bool:
        return self.mode in ("rehearse", "write")

    @property
    def address(self):
        return self.httpd.server_address[0], self.httpd.server_address[1]

    def say(self, msg: str) -> None:
        print(f"[serve {time.strftime('%H:%M:%S')}] {msg}", file=self.out, flush=True)

    def manifest(self) -> bytes:
        lines = [f"run={self.bundle.run_id}"]
        if self.mode in ("standby", "abort"):
            lines.append(f"mode={self.mode}")
        else:
            lines += ["mode=go", f"go={GO_PHRASE}"]
            if self.mode == "write":
                lines.append(f"write={WRITE_PHRASE}")
                if self.reboot:
                    lines.append(f"reboot={REBOOT_PHRASE}")
        return ("\n".join(lines) + "\n").encode()

    def note_refused(self) -> None:
        with self.lock:
            self.refused += 1
        self.say("refused a request without the expected run id")

    def record(self, phase: str, event: str) -> None:
        with self.lock:
            self.events.append((time.time(), phase, event))
        note = NOTES.get(event)
        self.say(f"phase={phase} event={event}" + (f" -- {note}" if note else ""))

    def count_served(self, rel: str) -> None:
        with self.lock:
            self.served[rel] = self.served.get(rel, 0) + 1
        self.say(f"served {rel} ({len(self.bundle.files[rel].data)} B)")

    def _save(self, stem: str, suffix: str, data: bytes) -> None:
        with self.lock:
            self.saved += 1
            name = f"{stem}-{time.strftime('%Y%m%dT%H%M%S')}-{self.saved:03d}{suffix}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.backup_dir / name, flags, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())

    def save_backup(self, name: str, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self._save(name, ".bin", data)  # durable before the camera is told to go on
        with self.lock:
            self.backups[name] = digest
        self.say(f"backup {name}: {len(data)} B stored privately (0600); confirming to the camera")
        return digest

    def save_report(self, data: bytes) -> None:
        self._save("migrate", ".log", data)
        with self.lock:
            self.reports.append(data)
        self.say(f"installer log received ({len(data)} B), stored privately")

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
    ap.add_argument("--bind", help="listen address (default: the OM_HOST baked into the appfs)")
    ap.add_argument("--port", type=int, help="listen port (default: the OM_PORT baked into the appfs)")
    ap.add_argument("--backup-dir", type=Path, help="private directory for mdat backups and installer logs")
    ap.add_argument("--write-confirm", metavar="PHRASE")
    ap.add_argument("--reboot-confirm", metavar="PHRASE")
    args = ap.parse_args(argv)
    os.umask(0o077)
    try:
        bundle = load_bundle(args.bundle)
        bind = args.bind or bundle.host
        port = bundle.port if args.port is None else args.port
        srv = MigrationServer(bundle, args.mode, backup_dir=args.backup_dir, write_confirm=args.write_confirm,
                              reboot_confirm=args.reboot_confirm, bind=bind, port=port)
    except (BundleError, ValueError, OSError) as exc:
        print(f"serve_migration: {exc}", file=sys.stderr)
        return 2
    host, port = srv.address
    srv.say(f"mode={args.mode} env={bundle.env_mode} listening on {host}:{port}; Ctrl-C stops")
    if (host, port) != (bundle.host, bundle.port):
        srv.say(f"note: the camera calls {bundle.host}:{bundle.port} (baked into its appfs)")
    if args.mode == "write":
        srv.say("WRITE MODE: a camera reaching phase2 rewrites its whole 8 MiB flash"
                + (" and reboots" if srv.reboot else ""))
    try:
        srv.httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
