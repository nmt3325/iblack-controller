"""Shared offline helpers for the online-migration tests (no camera, loopback only)."""
from __future__ import annotations

import io
import os
import random
import secrets
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote_from_bytes

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import build_migration_appfs as bma  # noqa: E402
import serve_migration as sm  # noqa: E402

STAGE2 = ROOT / "camera" / "stage2.sh"
HOOK = ROOT / "camera" / "migrate_hook.sh"
INSTALLER = ROOT / "installer" / "om_stockboot.sh"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never use a proxy
# The stock BusyBox 1.18.4 wget sends --post-data form-escaped: %XX for every byte outside [A-Za-z0-9] and
# !&'()*-.=_~ (qemu run of the real applet; quote_from_bytes always keeps A-Za-z0-9 and _.-~ itself).
STOCK_WGET_SAFE = "!&'()*="


def private_tmp(prefix: str) -> Path:
    """A 0700 temp dir whose path only has characters migrate.conf can carry (OM_TEST_TMPDIR: its parent)."""
    base = os.environ.get("OM_TEST_TMPDIR")
    base = Path(base) if base else Path("/dev/shm") if os.access("/dev/shm", os.W_OK) else Path(tempfile.gettempdir())
    path = base / f"{prefix}-{secrets.token_hex(6)}"
    path.mkdir(mode=0o700)
    return path


def stock_wget_form(data: bytes) -> bytes:
    return quote_from_bytes(data, safe=STOCK_WGET_SAFE).encode()


def rbytes(seed: int, n: int) -> bytes:
    return random.Random(seed).randbytes(n)


def synthetic_files(seed: int = 1) -> dict:
    """Random runtime files plus structurally valid (synthetic) payloads: the server validates both."""
    from sb_fixture import firmware, oenv
    files = {rel: rbytes(seed + i, 4096 + i) for i, rel in enumerate(sm.RUNTIME_FILES)}
    files["payload/firmware.bin"] = firmware(seed)
    files["payload/env-openipc.img"] = oenv()
    return files


def make_bundle(out: Path, files: dict, *, host: str = "127.0.0.1", port: int = 8608, run_id: str | None = None,
                stage2: bytes | None = None, overrides=None, allow_test_keys: bool = False,
                conf_files: dict | None = None):
    """Write a bundle serving `files`; migrate.conf pins `conf_files` (default: the same files)."""
    stage2 = STAGE2.read_bytes() if stage2 is None else stage2
    run_id = run_id or secrets.token_hex(16)
    values = bma.conf_values(host, port, run_id, conf_files or files, stage2, overrides)
    conf = bma.render_conf(values, allow_test_keys=allow_test_keys)
    if conf_files is not None:  # the served bundle itself must stay self-consistent
        own = bma.render_conf(bma.conf_values(host, port, run_id, files, stage2, overrides),
                              allow_test_keys=allow_test_keys)
    else:
        own = conf
    bma.assemble_bundle(out, files, own, host=host, port=port, run_id=run_id, stage2=stage2,
                        appfs=b"appfs-placeholder")
    return run_id, conf


def write_action_set(out: Path, action: str, files: dict, envlive: str, uboot: str) -> Path:
    """An action directory as build_stockboot.py writes it (no camera evidence)."""
    import hashlib
    import json
    out.mkdir(mode=0o700)
    for name, data in files.items():
        (out / name).write_bytes(data)
    meta = {"schema": sm.ACTION_SCHEMA, "action": action, "envlive": envlive, "uboot": uboot,
            "files": {n: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for n, d in files.items()}}
    (out / "action.json").write_text(json.dumps(meta))
    return out


def http(method: str, url: str, data: bytes | None = None, timeout: float = 15):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def start_server(bundle_dir: Path, mode: str, *, backup_dir=None, port: int = 0, action_dir=None, **kw):
    out = io.StringIO()
    action_set = sm.load_action_dir(action_dir, mode) if action_dir else None
    srv = sm.MigrationServer(sm.load_bundle(bundle_dir), mode, action_set=action_set, backup_dir=backup_dir,
                             bind="127.0.0.1", port=port, out=out, **kw)
    return srv.start(), out


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
