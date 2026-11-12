#!/usr/bin/env python3
"""Offline verification only: no camera access or network changes."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    parser = argparse.ArgumentParser(description='Offline verification of the lightweight source repository')
    parser.add_argument('--artifacts-root', type=Path, help='Optionally also verify an extracted external artifact archive')
    args = parser.parse_args()
    py_files = sorted(ROOT.rglob("*.py"))
    for p in py_files:
        if ".git" not in p.parts:
            ast.parse(p.read_text(), filename=str(p.relative_to(ROOT)))
    for helper in ("iermu-wifi-vif-up", "iermu-home-uplink-guard"):
        ast.parse((ROOT / "deployment/sbin" / helper).read_text(), filename=helper)
    print(f"PASS Python syntax: {len(py_files)} files plus the virtual-interface and uplink helpers", flush=True)
    sh_files = sorted(ROOT.rglob("*.sh")) + sorted((ROOT / "reference/sbin").iterdir())
    sh_files += [ROOT / "client/camera-stream", ROOT / "deployment/sbin/iermu-ap-up", ROOT / "deployment/sbin/iermu-ap-down", ROOT / "deployment/sbin/iermu-rtmp-publish", ROOT / "deployment/sbin/iermu-home-network"]
    for p in sh_files:
        if p.is_file():
            subprocess.run(["bash", "-n", str(p)], check=True)
    print(f"PASS shell syntax: {len(sh_files)} files", flush=True)
    embedded = 0
    for p, delim in ((ROOT / "reference/iermu_cam/iermu_run.sh", "PY"), (ROOT / "reference/iermu_cam/ap/iermu_reset_capture.sh", "PYCODE")):
        text = p.read_text()
        if delim == "PY":
            body = text.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        else:
            body = text.split("<<'PYCODE'\n", 1)[1].split("\nPYCODE\n", 1)[0]
        ast.parse(body, filename=str(p) + ":embedded")
        embedded += 1
    print(f"PASS embedded Python syntax: {embedded} blocks", flush=True)
    manifest = json.loads((ROOT / "evidence/source-manifest.json").read_text())
    for row in manifest["files"]:
        got = digest_file(ROOT / row["path"])
        assert got == row["sanitized_sha256"], row["path"]
    print(f"PASS reference checksums: {len(manifest['files'])} files", flush=True)
    current_path = ROOT / "evidence/current-source-manifest.json"
    if current_path.exists():
        current = json.loads(current_path.read_text())
        for row in current["files"]:
            got = digest_file(ROOT / row["path"])
            assert got == row["sha256"], row["path"]
        print(f"PASS current snapshot checksums: {len(current['files'])} files", flush=True)
    link_count = 0
    for p in ROOT.rglob("*.md"):
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', p.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = (p.parent / link.split("#", 1)[0]).resolve()
            assert target.exists(), str(p) + " -> " + link
            link_count += 1
    print(f"PASS relative documentation links: {link_count}", flush=True)
    artifacts = json.loads((ROOT / "artifacts/manifest.json").read_text())
    assert artifacts["version"] == 1
    rows = artifacts["files"]
    assert len({row["path"] for row in rows}) == len(rows)
    for row in rows:
        assert isinstance(row["bytes"], int) and row["bytes"] >= 0
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        assert not Path(row["path"]).is_absolute() and ".." not in Path(row["path"]).parts
    index = json.loads((ROOT / "artifacts/archive.json").read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", index["sha256"]) and index["bytes"] > 0
    assert index["binary_files"] == len(rows)
    print(f"PASS external artifact manifest: {len(rows)} binary inputs indexed; no Git LFS required", flush=True)
    if args.artifacts_root:
        subprocess.run([sys.executable, "tools/verify_artifacts.py", "--root", str(args.artifacts_root.resolve())], cwd=ROOT, check=True)
    else:
        print("INFO raw APK/JADX/JDK/source payloads are separate; use --artifacts-root to verify them", flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, env=env, check=True)
    print("PASS offline tests; no network operations performed", flush=True)

if __name__ == "__main__":
    main()
