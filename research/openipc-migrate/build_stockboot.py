#!/usr/bin/env python3
"""Host-side state machine of the stock-U-Boot OpenIPC migration (Wi-Fi-only iBlack).

Offline only: it reads the private uploads that serve_migration.py stored in
--uploads and writes private action sets (0700/0600, outside the repository)
that serve_migration.py --action-dir serves. It never contacts a camera.
Every step refuses to run before the step it depends on succeeded:

  diag     newest diagnose uploads (diag-info, diag-blocks, diag-env) vs the
           SPI dump: per-block stability over 3 passes, weak block x16,
           vendor U-Boot x3, vendor env (CRC, stock bootcmd, no marker), flash
           table, MT7601 USB id, watchdog. Stores env-base.img (E0) + diag.json.
  probe    action set probe-arm: env-probe.img built from E0.
  collect  newest collect uploads after a reboot. --stage probe: did the vendor
           U-Boot run the probe env and save the stock env? --stage trial:
           parse the trial log (tl mailbox and/or trial-log upload), check the
           vendor env is stock-equivalent and U-Boot unchanged.
  trial    action set trial-arm: trial.img (OpenIPC kernel + Wi-Fi trial
           payload SquashFS <= 704 KiB) and env-armed.img built from the env
           the probe left behind. The payload carries the Wi-Fi settings: the
           whole state directory is private.
  final    action set final: env-final.img built from E0 (needs a passed trial).
  restore  action set restore-env: env-base.img = E0.
  status   sanitized overview of the state directory.
  plan     capacity report without any camera state (no --state needed): layouts, kernel/rootfs slot
           margins, U-Boot command lengths, and the size of a trial payload built with the real
           cfg80211.ko/mt7601sta.ko of the OpenIPC rootfs and placeholder Wi-Fi settings of maximal length.
Printed summaries carry no hashes, env values, SSIDs or passwords.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_env  # noqa: E402
import stock_layout as sl  # noqa: E402
from serve_migration import ACTION_SCHEMA, ACTIONS, inside_repo, validate_action_file, BundleError  # noqa: E402

TRIAL_SH, TRIAL_DHCP = HERE / "camera" / "trial.sh", HERE / "camera" / "trial_dhcp.sh"
FIRMWARE_SHA256 = "2e268c99608c4dcd4867eda8c35f9393dd4832cf8da05cc31af571bbd3457926"
SPI_BYTES = sl.FLASH_BYTES
NBLOCKS = sl.FLASH_BYTES // sl.ERASE
STOCK_MTD = ['mtd%d: %08x %08x "%s"' % (i, s, sl.ERASE, n) for i, (n, _, s) in enumerate(sl.STOCK_PARTS)]
MT7601_USB = "148f:7601"
TRIAL_MKSQUASHFS = ("-noappend", "-comp", "xz", "-b", "131072", "-Xbcj", "arm", "-all-root", "-no-progress",
                    "-processors", "1", "-no-exports", "-no-xattrs")
DAT_KEYS = {"AuthMode": "WPA2PSK", "EncrypType": "AES"}
FLAG_RE = re.compile(r"^omt ([a-z0-9_]+)=(.*)$", re.M)


class StateError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def private_dir(path: Path, create: bool = True) -> Path:
    path = Path(path).absolute()
    if inside_repo(path):
        raise StateError(f"{path.name}: must be outside the Git repository (private data)")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise StateError(f"{path.name}: must be a 0700 directory owned by the current user")
    return path


def write_private(path: Path, data: bytes, overwrite: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | (os.O_TRUNC if overwrite else os.O_EXCL)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def save_json(state: Path, name: str, obj: dict) -> None:
    write_private(state / name, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode(), overwrite=True)


def load_json(state: Path, name: str, need: str) -> dict:
    try:
        return json.loads((state / name).read_text())
    except (OSError, json.JSONDecodeError):
        raise StateError(f"{name} missing: run `{need}` first") from None


def newest_upload(uploads: Path, name: str) -> bytes:
    pat = re.compile(rf"{re.escape(name)}-\d{{8}}T\d{{6}}-\d{{3}}\.bin")
    hits = sorted(p for p in uploads.glob(f"{name}-*.bin") if pat.fullmatch(p.name))
    if not hits:
        raise StateError(f"no {name} upload in --uploads")
    return hits[-1].read_bytes()


def sections(text: str) -> dict:
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("== "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def parse_blocks(text: str) -> dict:
    b, w, u, e = {}, [], [], []
    for line in text.splitlines():
        f = line.split()
        if len(f) == 4 and f[0] == "b":
            b.setdefault(int(f[2]), []).append(f[3])
        elif len(f) == 3 and f[0] in "wue":
            {"w": w, "u": u, "e": e}[f[0]].append(f[2])
    if sorted(b) != list(range(NBLOCKS)) or any(len(v) != 3 for v in b.values()) or len(w) != 16 or len(u) != 3:
        raise StateError("diag-blocks is incomplete (need 3 passes over 128 blocks, 16 weak reads, 3 U-Boot reads)")
    return {"b": b, "w": w, "u": u, "e": e}


def part_of(block: int) -> str:
    return sl.part_at(block * sl.ERASE, [(n, o, s) for n, o, s in sl.STOCK_PARTS])[0]


# ---- diag ------------------------------------------------------------------------------------
def cmd_diag(args, state: Path) -> dict:
    uploads = private_dir(args.uploads, create=False)
    dump = Path(args.spi_dump).read_bytes()
    if len(dump) != SPI_BYTES:
        raise StateError("SPI dump must be exactly 8 MiB")
    info = sections(newest_upload(uploads, "diag-info").decode("utf-8", "replace"))
    blocks = parse_blocks(newest_upload(uploads, "diag-blocks").decode("ascii", "replace"))
    env = newest_upload(uploads, "diag-env")
    try:
        entries = sl.parse_env(env)
        sl.check_base_env(entries)
    except sl.LayoutError as exc:
        raise StateError(f"live vendor env is not a stock env ({exc}); run restore or recover first") from None
    problems, notes = [], []
    if sha(env) not in blocks["e"]:
        problems.append("diag-env upload does not match the env hash read on the camera")
    unstable = [i for i, v in blocks["b"].items() if len(set(v)) != 1]
    if unstable:
        problems.append("unstable reads in partitions " + ",".join(sorted({part_of(i) for i in unstable})))
    weak_variants = len(set(blocks["w"]))
    if weak_variants != 1:      # the trial kernel (tk) and later the OpenIPC rootfs would sit on that block
        problems.append(f"weak block 0x{sl.WEAK_BIT_ABS & -sl.ERASE:x} read {weak_variants} different ways "
                        f"in {len(blocks['w'])} reads")
    if len(set(blocks["u"])) != 1:
        problems.append("vendor U-Boot reads differ")
    uboot = blocks["u"][0]
    if uboot != sha(dump[:sl.UBOOT_LEN]):
        problems.append("vendor U-Boot differs from the SPI dump (the dump would not restore this boot loader)")
    if sha(dump[sl.SENV_OFF:sl.SENV_OFF + sl.SENV_LEN]) != sha(env):
        notes.append("vendor env differs from the SPI dump (expected after env changes; E0 is the live env)")
    differs = sorted({part_of(i) for i, v in blocks["b"].items() if v[0] != sha(dump[i * sl.ERASE:(i + 1) * sl.ERASE])})
    mtd = [l.strip() for l in info.get("mtd", "").splitlines() if l.startswith("mtd")]
    if mtd != STOCK_MTD:
        problems.append("/proc/mtd is not the stock 6-partition table")
    usb_ok = MT7601_USB in info.get("usb", "")
    if not usb_ok:
        problems.append("MT7601 USB Wi-Fi (148f:7601) not listed")
    wdt = [l for l in info.get("watchdog", "").splitlines() if l and "No such file" not in l]
    if wdt:
        problems.append("a watchdog device exists (the hook refuses to stop the stock app)")
    mem = re.search(r"MemTotal:\s+(\d+) kB", info.get("meminfo", ""))
    diag = {"ok": not problems, "problems": problems, "notes": notes, "uboot": uboot, "envlive": sha(env),
            "env_keys": [k for k, _ in entries], "unstable_blocks": unstable, "weak_block_variants": weak_variants,
            "partitions_differing_from_spi_dump": differs, "mt7601_usb": usb_ok, "watchdog": bool(wdt),
            "mem_total_kb": int(mem.group(1)) if mem else None,
            "block_hashes": {str(i): v[0] for i, v in blocks["b"].items()}}
    write_private(state / "env-base.img", env, overwrite=True)
    save_json(state, "diag.json", diag)
    for stale in ("probe.json", "trial.json", "trial-build.json", "env-live-probe.img", "env-live-trial.img"):
        (state / stale).unlink(missing_ok=True)
    return {k: diag[k] for k in ("ok", "problems", "notes", "weak_block_variants", "partitions_differing_from_spi_dump",
                                 "mt7601_usb", "watchdog", "mem_total_kb")} | {"unstable_block_count": len(unstable)}


def need_diag(state: Path) -> tuple:
    diag = load_json(state, "diag.json", "diag")
    if not diag.get("ok"):
        raise StateError("diag did not pass: " + "; ".join(diag.get("problems", [])))
    return diag, (state / "env-base.img").read_bytes()


def write_action(out: Path, action: str, files: dict, envlive: str, uboot: str, evidence: dict) -> dict:
    out = Path(out).absolute()
    if inside_repo(out) or os.path.lexists(out):
        raise StateError("--out must be a new directory outside the Git repository")
    for name, data in files.items():
        validate_action_file(name, data)
    out.mkdir(mode=0o700, parents=True)
    for name, data in files.items():
        write_private(out / name, data)
    meta = {"schema": ACTION_SCHEMA, "action": action, "envlive": envlive, "uboot": uboot, "evidence": evidence,
            "files": {n: {"size": len(d), "sha256": sha(d)} for n, d in sorted(files.items())}}
    write_private(out / "action.json", (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode())
    return {"action": action, "out": str(out), "files": {n: len(d) for n, d in sorted(files.items())},
            "serve": f"serve_migration.py --mode {action} --action-dir <out>"
                     + (f" --write-confirm {ACTIONS[action][0]}" if ACTIONS[action][0] else "")}


def cmd_probe(args, state: Path) -> dict:
    diag, base = need_diag(state)
    img, report = sl.build_probe(base)
    out = write_action(args.out, "probe-arm", {"env-probe.img": img}, diag["envlive"], diag["uboot"],
                       {"built_from": "diag env (E0)"})
    return out | {"probe": report}


# ---- collect ------------------------------------------------------------------------------------
def collected(uploads: Path, diag: dict) -> tuple:
    info = newest_upload(uploads, "collect-info").decode("ascii", "replace")
    tl, env = newest_upload(uploads, "collect-tl"), newest_upload(uploads, "collect-env")
    blk = dict(re.findall(r"^blk (\d+) ([0-9a-f]{64})$", info, re.M))
    uboot_same = all(blk.get(str(i)) == diag["block_hashes"][str(i)] for i in range(4))
    stable = "tl stable" in info and "env stable" in info
    return info, tl, env, uboot_same, stable


def parse_trial_log(tl: bytes, raw_log: bytes = None) -> tuple:
    text = None
    if raw_log:
        text = raw_log.decode("ascii", "replace")
    elif tl[sl.TL_TEXT_LEN:sl.TL_TEXT_LEN + len(sl.TL_MARKER)] == sl.TL_MARKER:
        text = tl[:sl.TL_TEXT_LEN].rstrip(b"\0").decode("ascii", "replace")
    return text, (dict(FLAG_RE.findall(text)) if text else {})


def cmd_collect(args, state: Path) -> dict:
    diag, base = need_diag(state)
    uploads = private_dir(args.uploads, create=False)
    info, tl, env, uboot_same, stable = collected(uploads, diag)
    if args.stage == "probe":
        res = sl.check_probe_result(env, base)
        out = {"ok": res["ok"] and uboot_same and stable, "reason": res["reason"], "uboot_unchanged": uboot_same,
               "reads_stable": stable, "envlive": sha(env)}
        if not uboot_same:
            out["reason"] = "vendor U-Boot blocks changed"
        elif not stable:
            out["reason"] = "collect reads were not stable"
        save_json(state, "probe.json", out)
        if out["ok"]:
            write_private(state / "env-live-probe.img", env, overwrite=True)
        return {k: v for k, v in out.items() if k != "envlive"}
    build = load_json(state, "trial-build.json", "trial")
    probe_env = (state / "env-live-probe.img").read_bytes()
    raw = None
    try:
        raw = newest_upload(uploads, "trial-log")
    except StateError:
        pass
    text, flags = parse_trial_log(tl, raw)
    checks = {
        "trial_log_found": text is not None,
        "nonce_matches": flags.get("nonce") == build["nonce"],
        "kernel_booted_with_marker": flags.get("cmdline_mark") == "1",
        "mtd_table_ok": flags.get("mtd_table") == "ok",
        "modules_loaded": flags.get("insmod_cfg80211") == "0" and flags.get("insmod_mt7601sta") == "0",
        "wifi_interface": flags.get("iface", "none") != "none",
        "dhcp_ok": flags.get("dhcp") == "ok",
        "host_reached": flags.get("host") == "ok",
        "uboot_saveenv_ran": flags.get("env") in ("saved-by-uboot", "base"),
        "vendor_env_stock_equivalent": sl.stock_equivalent(env, probe_env),
        "uboot_unchanged": uboot_same,
        "reads_stable": stable,
    }
    out = {"ok": all(checks.values()), "checks": checks, "result": flags.get("result"),
           "kernel": flags.get("kernel"), "iface": flags.get("iface"), "envlive": sha(env),
           "log_source": "trial-log upload" if raw else ("tl mailbox" if text else None)}
    save_json(state, "trial.json", out)
    if out["ok"]:
        write_private(state / "env-live-trial.img", env, overwrite=True)
    return {k: v for k, v in out.items() if k != "envlive"}


# ---- trial ----------------------------------------------------------------------------------------
def make_dat(template: str, ssid: str, psk: str) -> str:
    want = {"SSID": ssid, "WPAPSK": psk, **DAT_KEYS}
    seen, lines = set(), []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        if "=" in line and key in want:
            line = f"{key}={want[key]}"
            seen.add(key)
        lines.append(line)
    if seen != set(want):
        raise StateError("MT7601USTA.dat template lacks " + ",".join(sorted(set(want) - seen)))
    return "\n".join(lines) + "\n"


def find_modules(rootfs: Path) -> dict:
    deps = sorted(rootfs.glob("lib/modules/*/modules.dep"))
    if len(deps) != 1:
        raise StateError("OpenIPC rootfs: expected exactly one lib/modules/*/modules.dep")
    dep = dict(l.split(":", 1) for l in deps[0].read_text().splitlines() if ":" in l)
    sta = next((k for k in dep if k.endswith("/mt7601sta.ko")), None)
    cfg = next((k for k in dep if k.endswith("/cfg80211.ko")), None)
    if not sta or not cfg or dep[sta].split() != [cfg]:
        raise StateError("OpenIPC rootfs: mt7601sta.ko must depend on cfg80211.ko only")
    return {"cfg80211.ko": deps[0].parent / cfg, "mt7601sta.ko": deps[0].parent / sta}


def trial_payload(live: bytes, armed: bytes, nonce: str, ssid: str, psk: str, rootfs: Path, mksquashfs: str,
                  workdir=None) -> tuple:
    """Return (payload SquashFS, {name: size}) of the trial payload (tp partition, <= 704 KiB)."""
    mods = find_modules(rootfs)
    template = (rootfs / "etc" / "mediatek" / "MT7601USTA.dat").read_text("ascii")
    from build_migration_appfs import BuildError, check_stock_sh  # late import: only the trial needs it
    for script in (TRIAL_SH, TRIAL_DHCP):
        try:
            check_stock_sh(script.read_text("ascii"), script.name)
        except BuildError as exc:
            raise StateError(str(exc)) from None
    with tempfile.TemporaryDirectory(prefix="om-trial-", dir=workdir) as tmp:
        tree = Path(tmp) / "tp"
        tree.mkdir(mode=0o700)
        files = {"trial.sh": TRIAL_SH.read_bytes(), "trial_dhcp.sh": TRIAL_DHCP.read_bytes(),
                 "MT7601USTA.dat": make_dat(template, ssid, psk).encode("ascii"),
                 "env-base.img": live, "env-base.md5": (hashlib.md5(live).hexdigest() + "\n").encode(),
                 "env-armed.md5": (hashlib.md5(armed).hexdigest() + "\n").encode(), "nonce": (nonce + "\n").encode(),
                 "tl-blank.img": sl.tl_blank()}
        files.update({n: p.read_bytes() for n, p in mods.items()})
        for name, data in files.items():
            write_private(tree / name, data)
            os.chmod(tree / name, 0o755 if name.endswith(".sh") else 0o600)
        img = Path(tmp) / "tp.squashfs"
        r = subprocess.run([mksquashfs, str(tree), str(img), *TRIAL_MKSQUASHFS], capture_output=True, timeout=600)
        if r.returncode != 0:
            raise StateError("mksquashfs failed for the trial payload")
        payload = img.read_bytes()
    if len(payload) > sl.TP_LEN:
        raise StateError(f"trial payload {len(payload)} B exceeds the {sl.TP_LEN} B tp partition")
    return payload, {name: len(data) for name, data in sorted(files.items())}


def pinned_firmware(path) -> tuple:
    fw = Path(path).read_bytes()
    if sha(fw) != FIRMWARE_SHA256:
        raise StateError("firmware differs from the pinned OpenIPC image")
    return fw, sl.uimage_info(fw)


def cmd_trial(args, state: Path) -> dict:
    diag, base = need_diag(state)
    probe = load_json(state, "probe.json", "collect --stage probe")
    if not probe.get("ok"):
        raise StateError("the saveenv probe did not pass: " + probe.get("reason", ""))
    live = (state / "env-live-probe.img").read_bytes()
    fw, k = pinned_firmware(args.firmware)
    wifi = make_env.parse_private_vars(Path(args.wifi_vars))
    if not wifi.get("wlanssid") or not wifi.get("wlanpass"):
        raise StateError("--wifi-vars needs wlanssid and wlanpass")
    armed, report = sl.build_trial(live)
    nonce = secrets.token_hex(16)
    payload, _ = trial_payload(live, armed, nonce, wifi["wlanssid"], wifi["wlanpass"], Path(args.oipc_rootfs),
                               args.mksquashfs, args.workdir)
    pad = lambda data, n: data + b"\xff" * (n - len(data))  # noqa: E731
    trial = pad(fw[:k["total"]], sl.TK_LEN) + pad(payload, sl.TP_LEN)
    build = {"nonce": nonce, "payload_bytes": len(payload), "payload_limit_bytes": sl.TP_LEN,
             "payload_margin_bytes": sl.TP_LEN - len(payload), "kernel_bytes": k["total"],
             "kernel_margin_bytes": sl.TK_LEN - k["total"], "armed_env": report}
    save_json(state, "trial-build.json", build)
    (state / "trial.json").unlink(missing_ok=True)
    out = write_action(args.out, "trial-arm", {"trial.img": trial, "env-armed.img": armed}, probe["envlive"],
                       diag["uboot"], {"built_from": "env left by the saveenv probe"})
    return out | {k: v for k, v in build.items() if k != "nonce"}


def cmd_final(args, state: Path) -> dict:
    diag, base = need_diag(state)
    trial = load_json(state, "trial.json", "collect --stage trial")
    if not trial.get("ok"):
        bad = [k for k, v in trial.get("checks", {}).items() if not v]
        raise StateError("the one-shot trial did not pass: " + ",".join(bad))
    fw = Path(args.firmware).read_bytes()
    if sha(fw) != FIRMWARE_SHA256:
        raise StateError("firmware differs from the pinned OpenIPC image")
    fplan = sl.plan(fw)["firmware"]
    final, report = sl.build_final(base)
    out = write_action(args.out, "final", {"env-final.img": final}, trial["envlive"], diag["uboot"],
                       {"built_from": "diag env (E0)", "trial": "passed"})
    return out | {"final_env": report, "kernel_slot_margin_bytes": fplan["kernel_slot_margin_bytes"],
                  "rootfs_slot_margin_bytes": fplan["rootfs_slot_margin_bytes"]}


def cmd_restore(args, state: Path) -> dict:
    diag, base = need_diag(state)
    sl.check_restore(base)
    return write_action(args.out, "restore-env", {"env-base.img": base}, diag["envlive"], diag["uboot"],
                        {"built_from": "diag env (E0)"})


# Placeholder vendor env for `plan` (documentation addresses only); the real one comes from the camera.
PLAN_ENV = [("bootdelay", "1"), ("baudrate", "115200"), ("ethaddr", "00:00:5e:00:53:01"), ("ipaddr", "192.0.2.10"),
            ("serverip", "192.0.2.1"), ("netmask", "255.255.255.0"), ("bootfile", "uImage"),
            ("bootcmd", sl.STOCK_BOOTCMD), ("stdin", "serial"), ("stdout", "serial"), ("stderr", "serial"),
            ("verify", "n"), ("ver", "U-Boot 2010.06 (placeholder)"),
            ("bootargs", "mem=42M lpj=1093632 root=1f01 rootfstype=squashfs mtdparts=hi_sfc:2048K(boot),768K(rootfs),"
                         "2560K(appfs),128K(mdat1),128K(mdat2),2560K(bakfs)")]


def cmd_plan(args, state=None) -> dict:
    fw, k = pinned_firmware(args.firmware)
    base = sl.build_env(PLAN_ENV)
    probe, probe_rep = sl.build_probe(base)
    armed, trial_rep = sl.build_trial(base)
    final, final_rep = sl.build_final(base)
    psk = secrets.token_urlsafe(48)[:63]  # placeholder of maximal WPA length; never stored
    payload, files = trial_payload(base, armed, secrets.token_hex(16), "x" * 32, psk, Path(args.oipc_rootfs),
                                   args.mksquashfs, args.workdir)
    out = {"layout": sl.plan(fw), "probe_env": probe_rep, "trial_env": trial_rep, "final_env": final_rep,
           "trial_kernel": {"bytes": k["total"], "slot_bytes": sl.TK_LEN, "margin_bytes": sl.TK_LEN - k["total"]},
           "trial_payload": {"bytes": len(payload), "limit_bytes": sl.TP_LEN, "margin_bytes": sl.TP_LEN - len(payload),
                             "file_bytes": {n: v for n, v in files.items() if n != "MT7601USTA.dat"},
                             "wifi": "placeholder of maximal length (32-char SSID, 63-char PSK)"},
           "camera_contacted": False}
    if args.bundle:
        meta = json.loads((Path(args.bundle) / "bundle.json").read_text())["appfs"]
        out["migration_appfs"] = {k: meta[k] for k in ("size", "limit_bytes", "margin_bytes")}
    return out


def cmd_status(args, state: Path) -> dict:
    out = {}
    for name in ("diag", "probe", "trial-build", "trial"):
        try:
            d = json.loads((state / f"{name}.json").read_text())
        except (OSError, json.JSONDecodeError):
            out[name] = "missing"
            continue
        out[name] = {k: v for k, v in d.items() if k in ("ok", "problems", "reason", "checks", "payload_bytes",
                                                         "payload_margin_bytes", "result")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", type=Path, help="private state directory (0700, outside the repo); all but plan")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("diag")
    p.add_argument("--uploads", type=Path, required=True)
    p.add_argument("--spi-dump", type=Path, required=True)
    p = sub.add_parser("probe")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("collect")
    p.add_argument("--uploads", type=Path, required=True)
    p.add_argument("--stage", choices=("probe", "trial"), required=True)
    p = sub.add_parser("trial")
    p.add_argument("--firmware", type=Path, required=True)
    p.add_argument("--oipc-rootfs", type=Path, required=True)
    p.add_argument("--wifi-vars", type=Path, required=True)
    p.add_argument("--mksquashfs", required=True)
    p.add_argument("--workdir")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("final")
    p.add_argument("--firmware", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("restore")
    p.add_argument("--out", type=Path, required=True)
    sub.add_parser("status")
    p = sub.add_parser("plan")
    p.add_argument("--firmware", type=Path, required=True)
    p.add_argument("--oipc-rootfs", type=Path, required=True)
    p.add_argument("--mksquashfs", required=True)
    p.add_argument("--bundle", type=Path, help="private bundle from build_migration_appfs.py (appfs size only)")
    p.add_argument("--workdir")
    args = ap.parse_args(argv)
    if args.cmd != "plan" and args.state is None:
        ap.error("--state is required")
    os.umask(0o077)
    try:
        state = private_dir(args.state) if args.cmd != "plan" else None
        result = {"diag": cmd_diag, "probe": cmd_probe, "collect": cmd_collect, "trial": cmd_trial,
                  "final": cmd_final, "restore": cmd_restore, "status": cmd_status, "plan": cmd_plan}[args.cmd](
            args, state)
    except (StateError, sl.LayoutError, make_env.EnvError, BundleError, OSError, UnicodeDecodeError) as exc:
        print(f"build_stockboot: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
