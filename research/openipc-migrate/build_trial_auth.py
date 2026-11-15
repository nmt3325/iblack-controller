#!/usr/bin/env python3
"""Build the trial-only static ARMv5 WPA2 runtime from pinned public sources.

No camera, network, flash, or Wi-Fi credentials are accessed. Inputs must already
have been downloaded. Work and output directories must be new, private, and
outside the repository. See auth/README.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile

import trial_auth as ta

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "auth/sources.json"
CONFIG = HERE / "auth/wpa.config"
PATCH = HERE / "auth/event-only.patch"
LIBNL_FILES = ("attr cache cache_mngt error genl genl_ctrl genl_family genl_mngt "
               "handlers msg nl object socket unl").split()
FLAGS = ("-Os -march=armv5te -mtune=arm926ej-s -mfloat-abi=soft "
         "-ffunction-sections -fdata-sections -fno-pie")


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def private_new(path):
    path = Path(path).absolute()
    if HERE == path or HERE in path.parents or os.path.lexists(path):
        raise ta.AuthError("work/output must be new directories outside the repository")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(path)):
        raise ta.AuthError("build paths must not contain whitespace or Makefile metacharacters")
    # Test the whole repository, not just this research subdirectory.
    if any((p / ".git").exists() for p in (path.parent, *path.parents)):
        raise ta.AuthError("work/output must be outside the repository")
    path.mkdir(mode=0o700, parents=True)
    return path


def extract(source, dest):
    """Only called after matching the archive against the committed public pin."""
    dest.mkdir(mode=0o700)
    with tarfile.open(source) as tf:
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ta.AuthError("unsafe archive path")
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(member.name) if member.issym() else "", member.linkname))
                if target.startswith("/") or target == ".." or target.startswith("../"):
                    raise ta.AuthError("unsafe archive link")
        # Supports the stock Debian Python 3.11 without the newer 'filter' argument.
        tf.extractall(dest)
    roots = list(dest.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ta.AuthError("unexpected public archive layout")
    return roots[0]


def build(args):
    pins = json.loads(SOURCES.read_text())
    for key, path in (("toolchain", args.toolchain_tar), ("wpa_supplicant", args.wpa_tar)):
        if digest(path) != pins[key]["sha256"]:
            raise ta.AuthError("public source archive does not match its pin: " + key)
    runenv = dict(os.environ, SOURCE_DATE_EPOCH="0", LC_ALL="C", TZ="UTC")
    rev = subprocess.check_output(["git", "-C", str(args.libnl_source), "rev-parse", "HEAD"],
                                  text=True, env=runenv).strip()
    dirty = subprocess.check_output(["git", "-C", str(args.libnl_source), "status", "--porcelain"],
                                    text=True, env=runenv).strip()
    if rev != pins["libnl_tiny"]["commit"] or dirty:
        raise ta.AuthError("libnl-tiny must be clean and at the pinned revision")
    work = private_new(args.workdir)
    out = private_new(args.out)
    with (work / "build.log").open("x") as log:
        def run(argv, cwd=work):
            subprocess.run([str(x) for x in argv], cwd=cwd, env=runenv, check=True,
                           stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        tc = extract(args.toolchain_tar, work / "toolchain")
        source = extract(args.wpa_tar, work / "wpa-source")
        nl = work / "libnl-tiny"
        shutil.copytree(args.libnl_source, nl, ignore=shutil.ignore_patterns(".git"))
        prefix = str(tc / "bin/arm-linux-")
        objs = work / "libnl-static"
        objs.mkdir(mode=0o700)
        for name in LIBNL_FILES:
            # Keep libnl's upstream external-inline API outside LTO; WPA itself uses LTO.
            run([prefix + "gcc", *FLAGS.split(), "-D_GNU_SOURCE", "-I" + str(nl / "include"),
                 "-c", nl / (name + ".c"), "-o", objs / (name + ".o")])
        run([prefix + "ar", "rcs", objs / "libnl-tiny.a", *sorted(objs.glob("*.o"))])
        run(["patch", "--batch", "--forward", "-p1", "-i", PATCH], cwd=source)
        wpa = source / "wpa_supplicant"
        cflags = (FLAGS + " -flto -fno-fat-lto-objects -D_GNU_SOURCE -DCONFIG_OMT_AUTH_EVENT_ONLY"
                  + " -ffile-prefix-map=" + str(work) + "=."
                  + " -fmacro-prefix-map=" + str(work) + "=."
                  + " -I" + str(nl / "include"))
        (wpa / ".config").write_text(CONFIG.read_text() + "\nCFLAGS += " + cflags +
            "\nLDFLAGS += -flto -Os -static -no-pie -Wl,--gc-sections\nLIBS += -L" + str(objs) + "\n")
        run(["make", "-j1", "CC=" + prefix + "gcc", "PKG_CONFIG=/bin/true", "wpa_supplicant"], cwd=wpa)
        run([prefix + "strip", "-o", out / "wpa_supplicant", wpa / "wpa_supplicant"])
        os.chmod(out / "wpa_supplicant", 0o755)
        with (work / "elf.txt").open("x") as f:
            subprocess.run([prefix + "readelf", "-h", "-l", "-A", out / "wpa_supplicant"],
                           check=True, stdout=f, stderr=log, env=runenv)
    binary = (out / "wpa_supplicant").read_bytes()
    ta.validate_elf(binary)
    meta = {"schema": ta.SCHEMA, "version": "2.11", "profile": ta.PROFILE,
            "capabilities": ta.CAPABILITIES, "bytes": len(binary), "sha256": digest(out / "wpa_supplicant"),
            "public_sources": pins, "event_patch_sha256": digest(PATCH), "config_sha256": digest(CONFIG),
            "build_recipe_sha256": digest(__file__)}
    (out / "runtime.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    ta.runtime_files(out)
    return {"private_runtime_created": True, "profile": ta.PROFILE, "bytes": len(binary),
            "camera_contacted": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("toolchain-tar", "wpa-tar", "libnl-source", "workdir", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args(argv)
    os.umask(0o077)
    try:
        result = build(args)
    except (ta.AuthError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print("build_trial_auth: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())