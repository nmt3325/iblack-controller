"""Shared offline helpers for the online-migration tests (no camera, loopback only)."""
from __future__ import annotations

import io
import json
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


# ---- the Hi3518EV100 watchdog (an ARM SP805 at 0x20040000) behind OM_DEVMEM ----------------------------------
SP805_PY = r"""
import json, os, sys
BASE, KEY = 0x20040000, 0x1ACCE551
IDS = {0xFE0: 0x05, 0xFE4: 0x18, 0xFE8: 0x14, 0xFEC: 0x00, 0xFF0: 0x0D, 0xFF4: 0xF0, 0xFF8: 0x05, 0xFFC: 0xB1}
a = sys.argv[1:]
with open(STATE) as f:
    st = json.load(f)
if len(a) not in (2, 3) or a[1] != "32":
    sys.exit("devmem: only 32-bit accesses are modelled")
off = int(a[0], 0) - BASE
if not 0 <= off < 0x1000:
    sys.exit("devmem: %s is outside the SP805" % a[0])
if len(a) == 2:
    if st["mode"] == "noread":
        sys.exit("devmem: /dev/mem: Permission denied")
    if off == 0x004 and st["ctrl"] & 1:
        st["value"] = max(st["value"] - 4096, 0)   # counting down while enabled
    regs = {0x000: st["load"], 0x004: st["value"], 0x008: st["ctrl"], 0x010: st["ris"], 0xC00: int(st["locked"])}
    regs.update(IDS)
    if off not in regs:
        sys.exit("devmem: register 0x%03X is not modelled" % off)
    print("0x%08X" % regs[off])
else:
    v = int(a[2], 0) & 0xFFFFFFFF
    st["writes"].append([off, v])
    if off == 0xC00:
        st["locked"] = v != KEY
    elif st["locked"]:
        pass                                       # write-locked: ignored, as on the SoC
    elif off == 0x008:
        if st["mode"] != "stuck":
            st["ctrl"] = v & 3
    elif off == 0x00C:
        st["value"], st["ris"] = st["load"], 0     # interrupt cleared, counter reloaded
    else:
        sys.exit("devmem: register 0x%03X is not written by the hook" % off)
with open(STATE + ".new", "w") as f:
    json.dump(st, f)
os.replace(STATE + ".new", STATE)
"""


class FakeSP805:
    """A file-backed SP805 for migrate_hook.sh: OM_DEVMEM=self.script answers like BusyBox devmem ADDR 32 [VALUE].
    mode "stuck": CTRL writes are ignored; "noread": every read fails (no /dev/mem access)."""

    def __init__(self, d: Path, *, ctrl: int = 3, load: int = 0x05F5E100, mode: str = ""):
        self.state, self.script = d / "sp805.json", d / "devmem-sp805"
        self.set(ctrl=ctrl, load=load, value=load, ris=0, locked=True, mode=mode, writes=[])
        self.script.write_text(f"#!{sys.executable}\nSTATE = {str(self.state)!r}\n" + SP805_PY)
        self.script.chmod(0o755)

    def get(self) -> dict:
        return json.loads(self.state.read_text())

    def set(self, **kv) -> None:
        st = self.get() if self.state.exists() else {}
        st.update(kv)
        self.state.write_text(json.dumps(st))
