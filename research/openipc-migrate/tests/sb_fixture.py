"""Synthetic camera for the stock-U-Boot plan tests: 8 MiB flash split into the stock MTD files, env
images built with stock_layout.py, synthetic firmware/trial images, and a stand-in for the OpenIPC
runtime BusyBox (host BusyBox + crc32/flash_eraseall shims). Loopback/offline only."""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
import stock_layout as sl  # noqa: E402

INSTALLER = ROOT / "installer" / "om_stockboot.sh"
BUSYBOX = "/usr/bin/busybox" if os.access("/usr/bin/busybox", os.X_OK) else shutil.which("busybox")
PHRASES = {"probe-arm": ("yes-arm-saveenv-probe", "yes-reboot-for-saveenv-probe"),
           "trial-arm": ("yes-arm-one-shot-openipc-trial", "yes-reboot-into-openipc-trial"),
           "restore-env": ("yes-restore-stock-uboot-env", None),
           "final": ("yes-migrate-this-camera-to-openipc", "yes-reboot-into-openipc")}
CRC32_PY = ('import sys, zlib\nd = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else sys.stdin.buffer.read()\n'
            'print("%08x" % (zlib.crc32(d) & 0xffffffff))\n')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rbytes(seed: int, n: int) -> bytes:
    return random.Random(seed).randbytes(n)


def uimage(data: bytes, load: int = 0x80008000) -> bytes:
    hdr = struct.pack(">IIIIIIIBBBB32s", 0x27051956, 0, 0, len(data), load, load, zlib.crc32(data) & 0xFFFFFFFF,
                      5, 2, 2, 0, b"Linux-3.0.8-synthetic")
    return hdr[:4] + struct.pack(">I", zlib.crc32(hdr) & 0xFFFFFFFF) + hdr[8:] + data


def squashfs_blob(seed: int, used: int) -> bytes:
    body = bytearray(rbytes(seed, used))
    body[:4] = b"hsqs"
    body[40:48] = struct.pack("<Q", used)
    return bytes(body)


def ff(n: int) -> bytes:
    return b"\xff" * n


def firmware(seed: int = 7) -> bytes:
    k = uimage(rbytes(seed, 1_864_668))
    fw = k + ff(sl.FW_KERNEL_LEN - len(k)) + squashfs_blob(seed + 1, 4_000_000)
    return fw + ff(sl.FW_LEN - len(fw))


def trial_image(seed: int = 11) -> bytes:
    k = uimage(rbytes(seed, 1_800_000))
    return k + ff(sl.TK_LEN - len(k)) + squashfs_blob(seed + 1, 500_000) + ff(sl.TP_LEN - 500_000)


E0_ENTRIES = [("bootdelay", "1"), ("baudrate", "115200"), ("ethaddr", "00:00:5e:00:53:01"),
              ("ipaddr", "192.0.2.10"), ("serverip", "192.0.2.1"), ("netmask", "255.255.255.0"),
              ("bootfile", "uImage"), ("bootcmd", sl.STOCK_BOOTCMD), ("stdin", "serial"), ("stdout", "serial"),
              ("stderr", "serial"), ("verify", "n"), ("ver", "U-Boot 2010.06 (synthetic)"),
              ("bootargs", "mem=42M lpj=1093632 root=1f01 rootfstype=squashfs mtdparts=hi_sfc:2048K(boot),"
                           "768K(rootfs),2560K(appfs),128K(mdat1),128K(mdat2),2560K(bakfs)")]


def ub_setenv_raw(buf: bytearray, name: str, value) -> None:
    """common/cmd_nvedit.c _do_setenv of U-Boot 2010.06 on the RAM copy of the env data (no CRC): the old
    definition is cut out and the new one appended; bytes behind the new end marker stay as they were."""
    key, env, found = name.encode() + b"=", 0, None
    while buf[env]:
        nxt = buf.index(0, env)
        if buf[env:nxt].startswith(key):
            found = (env, nxt)
            break
        env = nxt + 1
    if found:
        env, nxt = found[0], found[1] + 1
        if buf[nxt] == 0:  # it was the last definition
            if env > 0:
                env -= 1
            else:
                buf[env] = 0
        else:
            while True:
                buf[env] = buf[nxt]
                nxt += 1
                if buf[env] == 0 and buf[nxt] == 0:
                    break
                env += 1
        env += 1
        buf[env] = 0
    if value is None:
        return
    env = 0
    while buf[env] or buf[env + 1]:
        env += 1
    if env > 0:
        env += 1
    item = key + value.encode()
    if len(item) + 2 > len(buf) - env:
        raise AssertionError(f"U-Boot: environment overflow, {name} deleted")
    buf[env:env + len(item)] = item
    buf[env + len(item):env + len(item) + 2] = b"\0\0"


def uboot_saved(env_img: bytes) -> bytes:
    """What the vendor U-Boot 2010.06 leaves in the env block after booting env_img: main_loop() re-sets "ver"
    (CONFIG_VERSION_VARIABLE), then the bootcmd runs up to its first saveenv. Every setenv is applied byte by
    byte like common/cmd_nvedit.c, so remnants of the old definitions stay behind the end marker, and the
    result is cross-checked against the list model of stock_layout."""
    entries = sl.parse_env(env_img)
    ver = dict(entries).get("ver", "U-Boot 2010.06 (synthetic)")
    buf = bytearray(env_img[4:])
    ub_setenv_raw(buf, "ver", ver)
    model = sl.UBootModel(entries)
    model.setenv("ver", ver)
    for argv in model.run(dict(entries)["bootcmd"]):
        if argv[0] == "saveenv":
            break
        if argv[0] == "setenv":
            ub_setenv_raw(buf, argv[1], " ".join(argv[2:]) if len(argv) > 2 else None)
    else:
        raise AssertionError("the bootcmd never reaches saveenv")
    img = struct.pack("<I", zlib.crc32(bytes(buf)) & 0xFFFFFFFF) + bytes(buf)
    if sl.parse_env(img) != model.saved[0]:
        raise AssertionError("byte-level setenv and the stock_layout model disagree")
    return img


def oenv() -> bytes:
    return sl.build_env([("bootcmd", sl.FINAL_BOOTCMD), ("mtdparts", sl.OIPC_ENV_LAYOUT["mtdparts"]),
                         ("wlandev", "mt7601u-hi3518ev100-camhi"), ("wlanssid", "dummy-ssid-for-test"),
                         ("wlanpass", "dummy-pass-for-test")], sl.OIPC_ENV_LEN)


class Camera:
    """Sim flash + payload + manifest for one om_stockboot.sh run."""

    def __init__(self, root: Path, seed: int = 3):
        self.root = Path(root)
        self.sim, self.stage, self.pay = self.root / "sim", self.root / "stage", self.root / "payload"
        for d in (self.sim, self.stage, self.pay):
            d.mkdir(parents=True, exist_ok=True)
        self.e0 = sl.build_env(E0_ENTRIES)
        flash = bytearray(rbytes(seed, sl.FLASH_BYTES))
        flash[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN] = self.e0
        flash[0x60000:0x80000] = ff(0x20000)
        self.original = bytes(flash)
        self.write_flash(self.original)
        (self.sim / "proc_mtd").write_text("dev:    size   erasesize  name\n" + "".join(
            'mtd%d: %08x %08x "%s"\n' % (i, s, sl.ERASE, n) for i, (n, _, s) in enumerate(sl.STOCK_PARTS)))
        self.bb = self.root / "bb"
        crc = self.root / "crc32.py"
        crc.write_text(CRC32_PY)
        self.bb.write_text(
            "#!/bin/sh\n# stand-in for the OpenIPC runtime BusyBox (offline tests)\n"
            f'case "${{1:-}}" in\n  --list) {BUSYBOX} --list; echo crc32; echo flash_eraseall; exit 0;;\n'
            f'  crc32) shift; exec python3 {crc} "$@";;\n'
            '  flash_eraseall|reboot|poweroff|halt|devmem) echo "$1 must not run offline" >&2; exit 99;;\n'
            '  sync) exit 0;;\nesac\n'
            f'exec {BUSYBOX} "$@"\n')
        self.bb.chmod(0o755)

    def write_flash(self, data: bytes) -> None:
        for i, (_, off, size) in enumerate(sl.STOCK_PARTS):
            (self.sim / f"mtd{i}").write_bytes(data[off:off + size])

    def flash(self) -> bytes:
        return b"".join((self.sim / f"mtd{i}").read_bytes() for i in range(6))

    def senv(self) -> bytes:
        return self.flash()[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN]

    def state(self) -> str:
        p = self.stage / "flash.state"
        return p.read_text().strip() if p.exists() else "clean"

    def payload(self, files: dict, envlive: bytes = None, uboot: bytes = None) -> Path:
        for p in self.pay.iterdir():
            p.unlink()
        lines = [f"uboot={sha(uboot if uboot is not None else self.flash()[:sl.UBOOT_LEN])}",
                 f"envlive={sha(envlive if envlive is not None else self.senv())}"]
        for name, data in files.items():
            (self.pay / name).write_bytes(data)
            lines.append(f"f.{name}={sha(data)}")
        man = self.root / "manifest"
        man.write_text("\n".join(lines) + "\n")
        return man

    def run(self, action: str, *, write: bool = True, reboot: bool = False, extra=(), env=None, fault: str = None,
            timeout: int = 900):
        man = self.root / "manifest"
        argv = [str(self.bb), "sh", str(INSTALLER), action, "--busybox", str(self.bb), "--stage", str(self.stage),
                "--payload", str(self.pay), "--manifest", str(man), "--sim-dir", str(self.sim), *extra]
        e = {"PATH": "/usr/bin:/bin", "HOME": str(self.root)}
        if action in PHRASES and write:
            argv.append("--allow-write")
            e["OM_WRITE_CONFIRM"] = PHRASES[action][0]
            if reboot and PHRASES[action][1]:
                argv.append("--allow-reboot")
                e["OM_REBOOT_CONFIRM"] = PHRASES[action][1]
        if fault:
            e["OM_SIM_FAULT"] = fault
        e.update(env or {})
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=e, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
