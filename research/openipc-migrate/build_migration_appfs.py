#!/usr/bin/env python3
"""Build the private migration appfs and LAN bundle for the online stock iBlack -> OpenIPC migration.

Offline only: reads pinned local inputs, never contacts a camera, writes no
update envelope and sends nothing. Delivering the appfs to a camera (the
vendor 0x96 update path) is deliberately NOT part of this repository.

Inputs (all pinned by size + SHA-256):
  --firmware     OpenIPC hi3518ev100 nor-lite-split image (kernel + rootfs)
  --uboot        OpenIPC nor U-Boot
  --stock-appfs  the camera's stock appfs (partition dump or trimmed SquashFS)
  --env-image    private U-Boot env image from make_env.py (0600)  | --env-erase
The OpenIPC BusyBox and musl libc are taken from the firmware's own rootfs.
The BusyBox PT_INTERP is re-pointed from /lib/ld-musl-arm.so.1 to
/dev/om/ld-musl.so so that this runtime can run from RAM next to the stock
uClibc rootfs (phase 2 of camera/stage2.sh).

Output: a NEW private directory OUTSIDE the Git repository (0700 dirs, 0600 files):
  appfs/migration-appfs.squashfs  stock appfs + openipc-migrate/{stage2.sh,migrate.conf}
                                  + a background launcher before the final `exit 0`
                                  of runapp.sh; vendor xz/BCJ profile, owners 500/500
  serve/runtime/                  busybox (interp-patched), ld-musl.so, installer, hook
  serve/payload/                  firmware.bin, uboot.bin [, env.img]
  conf/migrate.conf               copy of the baked config (serve_migration.py checks it)
  bundle.json                     schema, run id, host, port, env mode, sizes + sha256
The run id (a capability token), digests and env contents are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_env  # noqa: E402
from serve_migration import (BUNDLE_SCHEMA, CONF_LINE_RE, CONF_PINS, ENV_FILE, GO_PHRASE,  # noqa: E402
                             PAYLOAD_FILES, REBOOT_PHRASE, RUN_ID_RE, RUNTIME_FILES, WRITE_PHRASE,
                             BundleError, digests, inside_repo, load_bundle, parse_conf)

STAGE2 = HERE / "camera" / "stage2.sh"
HOOK = HERE / "camera" / "migrate_hook.sh"
INSTALLER = HERE / "installer" / "openipc_migrate.sh"

FIRMWARE_SHA256 = "2e268c99608c4dcd4867eda8c35f9393dd4832cf8da05cc31af571bbd3457926"
FIRMWARE_SIZE = 7012352
ROOTFS_OFFSET = 0x1D0000                 # SquashFS inside the pinned nor-lite-split image
UBOOT_SHA256 = make_env.PINNED_UBOOT_SHA256
UBOOT_SIZE = 122972
RUNTIME_BUSYBOX_SHA256 = "9894f631a3f64477904eaa03eadaf218feeb1920d23e881021295a88d7255699"
RUNTIME_LIBC_SHA256 = "35bc5d14c48da902cdfac568e7b574d532ac7a67b11a9462ac6535f042a2f16d"
STOCK_APPFS_SHA256 = "72881a9695bdf46b8ae0e9eb8c63ef49e16ac8d9c78983ec3070109740652366"  # first bytes_used bytes
STOCK_RUNAPP_SHA256 = "80d6e87131e25aa8107cde5166fb3ed09612eb7f18b106ea6fbc34672410170f"
APPFS_PARTITION = 0x280000
BAKFS_HEADER = 8
APPFS_LIMIT = APPFS_PARTITION - BAKFS_HEADER  # 2,621,432 B: the image must also fit bakfs with its header
OM_DIR = "/dev/om"
APP_DIR_NAME = "openipc-migrate"
OM_APP_DIR = "/app/" + APP_DIR_NAME
OLD_INTERP = b"/lib/ld-musl-arm.so.1"
NEW_INTERP = (OM_DIR + "/ld-musl.so").encode()
MKSQUASHFS_OPTIONS = ("-noappend", "-comp", "xz", "-b", "1048576", "-processors", "1", "-no-progress",
                      "-Xbcj", "arm,armthumb", "-no-exports", "-force-uid", "500", "-force-gid", "500")
CHANGED_PATHS = [APP_DIR_NAME, APP_DIR_NAME + "/migrate.conf", APP_DIR_NAME + "/stage2.sh", "runapp.sh"]
# Applets phase2, the installer and the hook call through the RAM BusyBox
# (ash builtins such as echo/printf/test/kill use the same implementations).
RUNTIME_APPLETS = ("awk", "base64", "cat", "chmod", "cmp", "dd", "echo", "grep", "head", "kill", "mkdir",
                   "pidof", "printf", "reboot", "rm", "rmdir", "sh", "sha256sum", "sleep", "sync", "tail",
                   "test", "timeout", "tr", "true", "wc", "wget")
# Commands the stock BusyBox 1.18.4 lacks; phase1 (and the header it sources) must not use them.
NON_STOCK_WORDS = ("base64", "chmod", "head", "pidof", "printf", "sed", "seq", "sha256sum", "timeout",
                   "tr", "wc", "xargs")
PHASE1_END = "# --- phase1 end ---"
RUNAPP_NEEDLE = b"\nexit 0\n"
LAUNCHER = ("\n# OpenIPC online migration agent: inert unless the LAN host answers with GO.\n"
            "(\n"
            "    /bin/busybox sleep {delay}\n"
            "    /bin/sh " + OM_APP_DIR + "/stage2.sh phase1\n"
            ") </dev/null >/dev/null 2>&1 &\n"
            "exit 0\n")

CONF_DEFAULTS = {
    "OM_DIR": OM_DIR, "OM_LOCK": "/dev/openipc-migrate.lock", "OM_APP_DIR": OM_APP_DIR,
    "OM_POLL_INTERVAL": "20", "OM_POLL_MAX": "90", "OM_MIN_AVAIL_KB": "12288", "OM_MIN_DIRFREE_KB": "10240",
    "OM_RETRY_SLEEP": "5", "OM_HOLD_MAX": "0", "OM_STOCK_BB": "/bin/busybox",
}
CONF_KEYS = ("OM_HOST", "OM_PORT", "OM_RUN_ID", *CONF_DEFAULTS, *CONF_PINS,
             "OM_STAGE2_SHA256", "OM_ENV_MODE", "OM_ENV_SHA256")
TEST_ONLY_KEYS = ("OM_SIM_DIR", "OM_SIM_MTD_PROC", "OM_QUIESCE_PROCS", "OM_WATCHDOG_DEV")
NUMERIC_KEYS = ("OM_PORT", "OM_POLL_INTERVAL", "OM_POLL_MAX", "OM_MIN_AVAIL_KB", "OM_MIN_DIRFREE_KB",
                "OM_RETRY_SLEEP", "OM_HOLD_MAX")
PATH_KEYS = ("OM_DIR", "OM_LOCK", "OM_APP_DIR", "OM_STOCK_BB")
HEX_LEN = {"sha1": 40, "md5": 32, "sha256": 64}

PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
DT_NULL, DT_NEEDED, DT_STRTAB = 0, 1, 5
EM_ARM = 40
ET_DYN = 3


class BuildError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- ELF (runtime checks and PT_INTERP patch) -------------------------------
def _vaddr_to_offset(loads, vaddr: int) -> int:
    for p_vaddr, p_offset, p_filesz in loads:
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            return p_offset + vaddr - p_vaddr
    raise BuildError("ELF address is outside every PT_LOAD segment")


def elf_info(data: bytes) -> dict:
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        raise BuildError("not a 32-bit little-endian ELF file")
    e_type, e_machine = struct.unpack_from("<HH", data, 16)
    e_phoff = struct.unpack_from("<I", data, 28)[0]
    e_flags = struct.unpack_from("<I", data, 36)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 42)
    if e_machine != EM_ARM:
        raise BuildError("ELF machine is not ARM")
    if e_phentsize != 32 or e_phnum == 0 or e_phoff + e_phnum * 32 > len(data):
        raise BuildError("ELF program headers are malformed")
    interp = interp_span = dynamic = None
    loads = []
    for i in range(e_phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = struct.unpack_from("<8I", data, e_phoff + 32 * i)
        if p_offset + p_filesz > len(data):
            raise BuildError("ELF segment exceeds the file")
        if p_type == PT_INTERP:
            raw = data[p_offset:p_offset + p_filesz]
            if interp is not None or not raw.endswith(b"\0"):
                raise BuildError("ELF PT_INTERP is malformed")
            interp, interp_span = raw.split(b"\0", 1)[0], (p_offset, p_filesz)
        elif p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
        elif p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
    needed = []
    if dynamic is not None:
        entries = []
        for off in range(dynamic[0], dynamic[0] + dynamic[1] - 7, 8):
            tag, val = struct.unpack_from("<iI", data, off)
            if tag == DT_NULL:
                break
            entries.append((tag, val))
        strtab = [v for t, v in entries if t == DT_STRTAB]
        names = [v for t, v in entries if t == DT_NEEDED]
        if names:
            if len(strtab) != 1:
                raise BuildError("ELF has DT_NEEDED entries but no single DT_STRTAB")
            base = _vaddr_to_offset(loads, strtab[0])
            for v in names:
                end = data.find(b"\0", base + v)
                if end < 0:
                    raise BuildError("ELF DT_NEEDED name is unterminated")
                needed.append(data[base + v:end].decode("ascii", "replace"))
    return {"type": e_type, "flags": e_flags, "interp": interp, "interp_span": interp_span, "needed": needed}


def patch_interp(data: bytes, old: bytes = OLD_INTERP, new: bytes = NEW_INTERP) -> bytes:
    info = elf_info(data)
    if info["interp"] != old:
        raise BuildError("runtime PT_INTERP is not the expected musl loader path")
    off, size = info["interp_span"]
    if len(new) + 1 > size:
        raise BuildError("new interpreter path does not fit into PT_INTERP")
    out = data[:off] + new + b"\0" * (size - len(new)) + data[off + size:]
    if elf_info(out)["interp"] != new or len(out) != len(data):
        raise BuildError("PT_INTERP patch did not verify")
    return out


def check_applets(data: bytes, names=RUNTIME_APPLETS) -> list:
    """Applet names missing from BusyBox's NUL-separated applet name table."""
    return [n for n in names if b"\0" + n.encode() + b"\0" not in data]


def check_runtime(busybox: bytes, libc: bytes) -> dict:
    bb, lc = elf_info(busybox), elf_info(libc)
    if bb["type"] != ET_DYN or bb["interp"] != OLD_INTERP or bb["needed"] != ["libc.so"]:
        raise BuildError("runtime BusyBox must be a PIE using only libc.so via /lib/ld-musl-arm.so.1")
    if lc["type"] != ET_DYN or lc["interp"] is not None or lc["needed"]:
        raise BuildError("runtime libc.so must be a self-contained musl loader")
    for info in (bb, lc):
        if info["flags"] & 0xFF000000 != 0x05000000:
            raise BuildError("runtime is not ARM EABI5")
    missing = check_applets(busybox)
    if missing:
        raise BuildError("runtime BusyBox lacks applets: " + " ".join(missing))
    return {"busybox_needed": bb["needed"], "eabi_flags": f"0x{bb['flags']:08x}",
            "applets_checked": len(RUNTIME_APPLETS)}


# ---- inputs -------------------------------------------------------------------
def read_input(path, limit: int) -> bytes:
    p = Path(path)
    try:
        st = os.stat(p)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise BuildError(f"{p.name}: not a regular file of at most {limit} B")
        return p.read_bytes()
    except OSError as exc:
        raise BuildError(f"{p.name}: {exc.__class__.__name__}") from None


def read_pinned(path, size: int, digest: str, label: str) -> bytes:
    data = read_input(path, size)
    if len(data) != size or sha256(data) != digest:
        raise BuildError(f"{label} differs from the pinned size/SHA-256")
    return data


def load_env_image(path) -> bytes:
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077:
        raise BuildError("env image must be a private regular file (mode 0600): it carries Wi-Fi secrets")
    data = read_input(path, make_env.ENV_SIZE)
    try:
        env = make_env.parse_env_image(data)
    except make_env.EnvError as exc:
        raise BuildError(f"env image rejected: {exc}") from None
    for key in ("bootcmd", "mtdparts", "wlandev"):
        if not env.get(key):
            raise BuildError(f"env image lacks {key}")
    if "7168k@0x50000(firmware)" not in env["mtdparts"]:
        raise BuildError("env image mtdparts does not describe the 7168k@0x50000 firmware partition")
    return data


def squashfs_profile(img: bytes) -> dict:
    if len(img) < 106 or img[:4] != b"hsqs":
        raise BuildError("not a SquashFS image")
    flags = struct.unpack_from("<H", img, 24)[0]
    return {"version": struct.unpack_from("<HH", img, 28), "compression": struct.unpack_from("<H", img, 20)[0],
            "block_size": struct.unpack_from("<I", img, 12)[0], "flags": flags,
            "comp_options": struct.unpack_from("<II", img, 98) if flags & 0x400 else None,
            "bytes_used": struct.unpack_from("<Q", img, 40)[0]}


def load_stock_appfs(path) -> bytes:
    raw = read_input(path, APPFS_PARTITION)
    used = squashfs_profile(raw)["bytes_used"]
    if not 106 < used <= len(raw):
        raise BuildError("stock appfs bytes_used is out of range")
    img = raw[:used]
    if sha256(img) != STOCK_APPFS_SHA256:
        raise BuildError("stock appfs differs from the pinned image")
    return img


def validate_host(host: str) -> str:
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        raise BuildError("--host must be a dotted IPv4 address of the LAN host") from None
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved \
            or ip == ipaddress.IPv4Address("255.255.255.255"):
        raise BuildError("--host must be a unicast LAN address the camera can reach")
    return str(ip)


def check_scripts(stage2: bytes, installer: bytes) -> None:
    s2, inst = stage2.decode("ascii"), installer.decode("ascii")
    for name, want in (("GO_PHRASE", GO_PHRASE), ("WRITE_PHRASE", WRITE_PHRASE), ("REBOOT_PHRASE", REBOOT_PHRASE)):
        if f'\n{name}="{want}"\n' not in s2:
            raise BuildError(f"stage2.sh {name} differs from serve_migration.py")
    for name, want in (("CONFIRM_PHRASE", WRITE_PHRASE), ("REBOOT_PHRASE", REBOOT_PHRASE)):
        if f'\n{name}="{want}"\n' not in inst:
            raise BuildError(f"installer {name} differs from serve_migration.py")
    if s2.count(PHASE1_END) != 1:
        raise BuildError("stage2.sh phase1 end marker missing")
    # The stock BusyBox 1.18.4 sh was built without $((...)) support and parses every function of
    # stage2.sh before it reaches the dispatch, so arithmetic expansion is banned in the whole file
    # (qemu run of the real stock sh: "you disabled math support for $((arith)) syntax").
    if "$((" in "\n".join(line for line in s2.splitlines() if not line.lstrip().startswith("#")):
        raise BuildError("stage2.sh uses $((...)); the stock shell cannot even parse it")
    # Everything up to the end of phase1 runs under the stock BusyBox 1.18.4 sh.
    code = "\n".join(line for line in s2.split(PHASE1_END, 1)[0].splitlines()
                     if not line.lstrip().startswith("#"))
    hit = re.search(r"(?<![\w./$-])(%s)(?![\w./-])" % "|".join(NON_STOCK_WORDS), code)
    if hit:
        raise BuildError(f"stage2.sh phase1 uses '{hit.group(1)}', which the stock BusyBox lacks")


# ---- migrate.conf -------------------------------------------------------------
def conf_values(host: str, port: int, run_id: str, files: dict, stage2: bytes, env_mode: str,
                overrides=None) -> dict:
    if env_mode not in ("image", "erase"):
        raise BuildError("env mode must be image or erase")
    expected = set(RUNTIME_FILES + PAYLOAD_FILES) | ({ENV_FILE} if env_mode == "image" else set())
    if set(files) != expected:
        raise BuildError("served file set differs from the protocol")
    dig = {rel: digests(data) for rel, data in files.items()}
    values = {"OM_HOST": host, "OM_PORT": str(port), "OM_RUN_ID": run_id, **CONF_DEFAULTS}
    values.update({key: dig[rel][kind] for key, (rel, kind) in CONF_PINS.items()})
    values.update({"OM_STAGE2_SHA256": sha256(stage2), "OM_ENV_MODE": env_mode,
                   "OM_ENV_SHA256": dig[ENV_FILE]["sha256"] if env_mode == "image" else ""})
    values.update({k: str(v) for k, v in (overrides or {}).items()})
    return values


def render_conf(values: dict, allow_test_keys: bool = False) -> str:
    allowed = set(CONF_KEYS) | (set(TEST_ONLY_KEYS) if allow_test_keys else set())
    unknown, missing = sorted(set(values) - allowed), sorted(set(CONF_KEYS) - set(values))
    if unknown or missing:
        raise BuildError(f"migrate.conf keys: unknown {unknown}, missing {missing}")
    if not RUN_ID_RE.fullmatch(values["OM_RUN_ID"]):
        raise BuildError("run id must be 16-64 lowercase hex characters")
    for key in NUMERIC_KEYS:
        if not re.fullmatch(r"[0-9]{1,9}", values[key]):
            raise BuildError(f"{key} must be a decimal number")
    if not 0 < int(values["OM_PORT"]) < 65536 or int(values["OM_POLL_INTERVAL"]) < 1 or int(values["OM_POLL_MAX"]) < 1:
        raise BuildError("port, poll interval and poll count must be positive (port < 65536)")
    for key in PATH_KEYS:
        if not values[key].startswith("/") or ".." in values[key].split("/"):
            raise BuildError(f"{key} must be an absolute path")
    for key, (_, kind) in CONF_PINS.items():
        if not re.fullmatch("[0-9a-f]{%d}" % HEX_LEN[kind], values[key]):
            raise BuildError(f"{key} is not a {kind} digest")
    if not re.fullmatch("[0-9a-f]{64}", values["OM_STAGE2_SHA256"]):
        raise BuildError("OM_STAGE2_SHA256 is not a sha256 digest")
    env_ok = {"image": re.fullmatch("[0-9a-f]{64}", values["OM_ENV_SHA256"]),
              "erase": values["OM_ENV_SHA256"] == ""}.get(values["OM_ENV_MODE"])
    if not env_ok:
        raise BuildError("OM_ENV_MODE/OM_ENV_SHA256 are inconsistent")
    if not allow_test_keys and (values["OM_DIR"] != OM_DIR or values["OM_APP_DIR"] != OM_APP_DIR):
        raise BuildError(f"OM_DIR/OM_APP_DIR are fixed to {OM_DIR} and {OM_APP_DIR} "
                         "(the runtime PT_INTERP and the runapp.sh launcher point there)")
    order = list(CONF_KEYS) + [k for k in TEST_ONLY_KEYS if k in values]
    lines = ["# migrate.conf -- generated by build_migration_appfs.py, sourced by stage2.sh.",
             "# Carries the run id (a capability token): private, never commit."]
    for key in order:
        line = f"{key}='{values[key]}'"
        if not CONF_LINE_RE.fullmatch(line):
            raise BuildError(f"{key} has characters migrate.conf cannot carry")
        lines.append(line)
    text = "\n".join(lines) + "\n"
    if parse_conf(text) != {k: values[k] for k in order}:
        raise BuildError("migrate.conf does not round-trip")
    return text


# ---- appfs ----------------------------------------------------------------------
def patch_runapp(data: bytes, delay: int) -> bytes:
    if sha256(data) != STOCK_RUNAPP_SHA256:
        raise BuildError("runapp.sh differs from the pinned stock script")
    if data.count(RUNAPP_NEEDLE) != 1 or not data.endswith(RUNAPP_NEEDLE):
        raise BuildError("runapp.sh must end with its only `exit 0`")
    if not 0 <= int(delay) <= 3600:
        raise BuildError("start delay must be 0..3600 s")
    return data[:-len(RUNAPP_NEEDLE)] + LAUNCHER.format(delay=int(delay)).encode()


def snapshot(tree: Path) -> dict:
    result = {}
    for p in [tree] + sorted(tree.rglob("*")):
        st = p.lstat()
        name = "." if p == tree else p.relative_to(tree).as_posix()
        item = {"mode": stat.S_IMODE(st.st_mode), "type": stat.S_IFMT(st.st_mode)}
        if stat.S_ISLNK(st.st_mode):
            item["target"] = os.readlink(p)
        elif stat.S_ISREG(st.st_mode):
            item["sha256"], item["size"] = sha256(p.read_bytes()), st.st_size
        result[name] = item
    return result


def prepare_tree(base: Path, src: Path, stage2: bytes, conf: bytes, delay: int) -> list:
    """Copy the stock tree and add the agent; return the changed paths (must be CHANGED_PATHS)."""
    shutil.copytree(base, src, symlinks=True, copy_function=shutil.copy2)
    runapp = src / "runapp.sh"
    if runapp.is_symlink() or not runapp.is_file():
        raise BuildError("stock appfs has no regular runapp.sh")
    mode = stat.S_IMODE(runapp.stat().st_mode)
    patched = patch_runapp(runapp.read_bytes(), delay)
    os.chmod(runapp, mode | 0o200)
    runapp.write_bytes(patched)
    os.chmod(runapp, mode)
    app = src / APP_DIR_NAME
    if os.path.lexists(app):
        raise BuildError(f"stock appfs already contains {APP_DIR_NAME}")
    app.mkdir()
    os.chmod(app, 0o755)
    for name, data, fmode in (("stage2.sh", stage2, 0o755), ("migrate.conf", conf, 0o644)):
        (app / name).write_bytes(data)
        os.chmod(app / name, fmode)
    before, after = snapshot(base), snapshot(src)
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


LISTING_RE = re.compile(r"([-dlcbps])([-rwxsStT]{9}) (\d+)/(\d+) +(\S+) "
                        r"\d{4}-\d\d-\d\d \d\d:\d\d (squashfs-root(?:/.*)?)")
TYPE_CHARS = {stat.S_IFDIR: "d", stat.S_IFREG: "-", stat.S_IFLNK: "l", stat.S_IFCHR: "c",
              stat.S_IFBLK: "b", stat.S_IFIFO: "p", stat.S_IFSOCK: "s"}


def perm_bits(perms: str) -> int:
    mode = sum(1 << (8 - i) for i, ch in enumerate(perms) if ch in "rwxst")
    for i, bit in ((2, 0o4000), (5, 0o2000), (8, 0o1000)):
        if perms[i] in "sStT":
            mode |= bit
    return mode


def parse_listing(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = LISTING_RE.fullmatch(line)
        if not m:
            continue
        kind, perms, uid, gid, size, path = m.groups()
        target = None
        if kind == "l":
            path, target = path.split(" -> ", 1)
        rel = "." if path == "squashfs-root" else path[len("squashfs-root/"):]
        out[rel] = {"type": kind, "mode": perm_bits(perms), "owner": f"{uid}/{gid}",
                    "size": size if kind in "-l" else None, "target": target}
    return out


def tree_matches_listing(snap: dict, listing: dict) -> bool:
    if set(snap) != set(listing):
        return False
    for rel, item in snap.items():
        meta = listing[rel]
        if TYPE_CHARS.get(item["type"]) != meta["type"] or item["mode"] != meta["mode"]:
            return False
        if meta["type"] == "-" and int(meta["size"]) != item["size"]:
            return False
        if meta["type"] == "l" and meta["target"] != item["target"]:
            return False
    return True


def run_tool(cmd, timeout: int, umask: int = -1) -> subprocess.CompletedProcess:
    name = Path(str(cmd[0])).name
    try:
        r = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, umask=umask)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"{name} could not run ({exc.__class__.__name__})") from None
    if r.returncode != 0:
        raise BuildError(f"{name} exited {r.returncode}: {r.stderr.decode(errors='replace')[-300:].strip()}")
    return r


def listing_of(unsquashfs, img: Path) -> dict:
    return parse_listing(run_tool([unsquashfs, "-llnumeric", img], 120).stdout.decode("utf-8", "replace"))


def extract_runtime(firmware: bytes, unsquashfs, work: Path):
    used = squashfs_profile(firmware[ROOTFS_OFFSET:ROOTFS_OFFSET + 128])["bytes_used"]
    if ROOTFS_OFFSET + used > len(firmware):
        raise BuildError("OpenIPC rootfs exceeds the firmware image")
    img = work / "openipc-rootfs.squashfs"
    img.write_bytes(firmware[ROOTFS_OFFSET:ROOTFS_OFFSET + used])
    out = [run_tool([unsquashfs, "-cat", img, name], 120).stdout for name in ("bin/busybox", "lib/libc.so")]
    img.unlink()
    if sha256(out[0]) != RUNTIME_BUSYBOX_SHA256 or sha256(out[1]) != RUNTIME_LIBC_SHA256:
        raise BuildError("OpenIPC rootfs BusyBox/libc differ from the pinned runtime")
    return out[0], out[1]


def build_appfs(stock: bytes, stage2: bytes, conf: bytes, delay: int, mksquashfs, unsquashfs, work: Path):
    src_img = work / "stock-appfs.squashfs"
    src_img.write_bytes(stock)
    original = listing_of(unsquashfs, src_img)
    if not original or {m["owner"] for m in original.values()} != {"500/500"}:
        raise BuildError("stock appfs listing is empty or not uniformly owned by 500/500")
    base, src, rt = work / "stock-tree", work / "migration-tree", work / "roundtrip"
    run_tool([unsquashfs, "-processors", "1", "-no-progress", "-d", base, src_img], 300, umask=0)
    if not tree_matches_listing(snapshot(base), original):
        raise BuildError("extracted stock tree differs from its SquashFS listing")
    changed = prepare_tree(base, src, stage2, conf, delay)
    if changed != CHANGED_PATHS:
        raise BuildError(f"unexpected change set {changed}")
    for script in (src / "runapp.sh", src / APP_DIR_NAME / "stage2.sh"):
        if shutil.which("sh"):
            run_tool(["sh", "-n", script], 30)
    img = work / "migration-appfs.squashfs"
    run_tool([mksquashfs, src, img, *MKSQUASHFS_OPTIONS], 900)
    image = img.read_bytes()
    want, got = squashfs_profile(stock), squashfs_profile(image)
    if {k: v for k, v in got.items() if k != "bytes_used"} != {k: v for k, v in want.items() if k != "bytes_used"}:
        raise BuildError("rebuilt appfs does not match the vendor SquashFS profile (xz, BCJ arm+armthumb, 1 MiB)")
    if len(image) > APPFS_LIMIT:
        raise BuildError(f"migration appfs is {len(image)} B > {APPFS_LIMIT} B (appfs/bakfs limit)")
    run_tool([unsquashfs, "-processors", "1", "-no-progress", "-d", rt, img], 300, umask=0)
    if snapshot(rt) != snapshot(src):
        raise BuildError("rebuilt appfs failed the content/type/mode round trip")
    rebuilt = listing_of(unsquashfs, img)
    if set(rebuilt) != set(original) | set(CHANGED_PATHS) or {m["owner"] for m in rebuilt.values()} != {"500/500"}:
        raise BuildError("rebuilt appfs entries/owners differ from the stock image")
    for rel, meta in original.items():
        new = dict(rebuilt[rel])
        if rel == "runapp.sh":
            new["size"] = meta["size"]  # the only stock file whose content changes
        if new != meta:
            raise BuildError(f"stock entry metadata changed: {rel}")
    return image, {"changed_paths": changed, "stock_entries": len(original), "profile_matches_vendor": True}


# ---- bundle -------------------------------------------------------------------------
def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def assemble_bundle(out, files: dict, conf_text: str, *, host: str, port: int, run_id: str, env_mode: str,
                    stage2: bytes, appfs: bytes, extra=None) -> Path:
    out = Path(out).absolute()
    if inside_repo(out):
        raise BuildError("--out must be outside the Git repository (the bundle holds private payloads)")
    if os.path.lexists(out):
        raise BuildError("--out already exists; refusing to overwrite")
    if not out.parent.is_dir():
        raise BuildError("--out parent directory does not exist")
    partial = out.parent / f".{out.name}.partial-{secrets.token_hex(4)}"
    old_umask = os.umask(0o077)
    try:
        partial.mkdir(mode=0o700)
        for rel, data in files.items():
            _write_private(partial / "serve" / rel, data)
        _write_private(partial / "conf" / "migrate.conf", conf_text.encode("ascii"))
        _write_private(partial / "appfs" / "migration-appfs.squashfs", appfs)
        meta = {"schema": BUNDLE_SCHEMA, "run_id": run_id, "host": host, "port": port, "env_mode": env_mode,
                "stage2_sha256": sha256(stage2),
                "files": {rel: {"size": len(d), "sha256": sha256(d)} for rel, d in sorted(files.items())},
                "appfs": {"file": "appfs/migration-appfs.squashfs", "size": len(appfs), "sha256": sha256(appfs),
                          "limit_bytes": APPFS_LIMIT, "margin_bytes": APPFS_LIMIT - len(appfs)},
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "private": "holds payloads and the run id; never commit or share",
                "update_envelope_created": False, **(extra or {})}
        _write_private(partial / "bundle.json", (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode())
        os.rename(partial, out)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    finally:
        os.umask(old_umask)
    return out


def build(args) -> dict:
    out = Path(args.out).absolute()
    if inside_repo(out) or os.path.lexists(out):
        raise BuildError("--out must be a new directory outside the Git repository")
    host = validate_host(args.host)
    if not 0 < args.port < 65536:
        raise BuildError("--port must be 1..65535")
    run_id = args.run_id or secrets.token_hex(16)
    if not RUN_ID_RE.fullmatch(run_id):
        raise BuildError("--run-id must be 16-64 lowercase hex characters")
    tools = []
    for opt, name in ((args.mksquashfs, "mksquashfs"), (args.unsquashfs, "unsquashfs")):
        path = opt or shutil.which(name)
        if not path or not os.access(path, os.X_OK):
            raise BuildError(f"{name} not found (pass --{name})")
        tools.append(path)
    mksquashfs, unsquashfs = tools
    firmware = read_pinned(args.firmware, FIRMWARE_SIZE, FIRMWARE_SHA256, "OpenIPC firmware")
    uboot = read_pinned(args.uboot, UBOOT_SIZE, UBOOT_SHA256, "OpenIPC U-Boot")
    env_mode = "image" if args.env_image else "erase"
    env_img = load_env_image(args.env_image) if args.env_image else None
    stock = load_stock_appfs(args.stock_appfs)
    stage2, hook, installer = STAGE2.read_bytes(), HOOK.read_bytes(), INSTALLER.read_bytes()
    check_scripts(stage2, installer)
    with tempfile.TemporaryDirectory(prefix="om-build-", dir=args.workdir) as tmp:
        work = Path(tmp)
        busybox_raw, libc = extract_runtime(firmware, unsquashfs, work)
        runtime = check_runtime(busybox_raw, libc)
        files = {"runtime/busybox": patch_interp(busybox_raw), "runtime/ld-musl.so": libc,
                 "runtime/openipc_migrate.sh": installer, "runtime/migrate_hook.sh": hook,
                 "payload/firmware.bin": firmware, "payload/uboot.bin": uboot}
        if env_img is not None:
            files[ENV_FILE] = env_img
        overrides = {"OM_POLL_INTERVAL": args.poll_interval, "OM_POLL_MAX": args.poll_max}
        conf_text = render_conf(conf_values(host, args.port, run_id, files, stage2, env_mode, overrides))
        appfs, tree = build_appfs(stock, stage2, conf_text.encode("ascii"), args.start_delay,
                                  mksquashfs, unsquashfs, work)
    out = assemble_bundle(out, files, conf_text, host=host, port=args.port, run_id=run_id, env_mode=env_mode,
                          stage2=stage2, appfs=appfs, extra={"runtime": runtime, "appfs_tree": tree})
    load_bundle(out)  # the server must accept exactly what was written
    return {"result": "PASS", "out": str(out), "env_mode": env_mode, "camera_calls": f"{host}:{args.port}",
            "start_delay_s": args.start_delay, "poll_window_s": args.poll_interval * args.poll_max,
            "appfs_bytes": len(appfs), "appfs_limit_bytes": APPFS_LIMIT, "appfs_margin_bytes": APPFS_LIMIT - len(appfs),
            "changed_paths": tree["changed_paths"], "served_bytes": {rel: len(d) for rel, d in sorted(files.items())},
            "runtime_interp": NEW_INTERP.decode(), "run_id": "stored in bundle.json (not printed)",
            "update_envelope_created": False, "camera_contacted": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--firmware", type=Path, required=True)
    ap.add_argument("--uboot", type=Path, required=True)
    env = ap.add_mutually_exclusive_group(required=True)
    env.add_argument("--env-image", type=Path, help="private env.img from make_env.py (keeps Wi-Fi settings)")
    env.add_argument("--env-erase", action="store_true", help="erase the env sector (U-Boot defaults, no wlandev)")
    ap.add_argument("--stock-appfs", type=Path, required=True)
    ap.add_argument("--host", required=True, help="IPv4 address of the LAN host running serve_migration.py")
    ap.add_argument("--port", type=int, default=8608)
    ap.add_argument("--run-id", help="16-64 lowercase hex characters (default: 128 random bits)")
    ap.add_argument("--start-delay", type=int, default=45, help="seconds after runapp.sh before phase1 starts")
    ap.add_argument("--poll-interval", type=int, default=20)
    ap.add_argument("--poll-max", type=int, default=90)
    ap.add_argument("--mksquashfs")
    ap.add_argument("--unsquashfs")
    ap.add_argument("--workdir", help="parent for the temporary build tree (default: system temp)")
    ap.add_argument("--out", type=Path, required=True, help="new private output directory outside the repository")
    args = ap.parse_args(argv)
    try:
        if not 0 <= args.start_delay <= 3600 or not 1 <= args.poll_interval <= 3600 or not 1 <= args.poll_max <= 10000:
            raise BuildError("--start-delay 0..3600, --poll-interval 1..3600, --poll-max 1..10000")
        summary = build(args)
    except (BuildError, BundleError, OSError, UnicodeDecodeError) as exc:
        print(f"build_migration_appfs: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
