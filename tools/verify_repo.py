#!/usr/bin/env python3
"""Offline verification only: no camera access or network changes."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    py_files = sorted(ROOT.rglob("*.py"))
    for p in py_files:
        if ".git" not in p.parts:
            ast.parse(p.read_text(), filename=str(p.relative_to(ROOT)))
    print(f"PASS Python syntax: {len(py_files)} files", flush=True)
    sh_files = sorted(ROOT.rglob("*.sh")) + sorted((ROOT / "reference/sbin").iterdir())
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
        got = hashlib.sha256((ROOT / row["path"]).read_bytes()).hexdigest()
        assert got == row["sanitized_sha256"], row["path"]
    print(f"PASS reference checksums: {len(manifest['files'])} files", flush=True)
    link_count = 0
    for p in ROOT.rglob("*.md"):
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', p.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = (p.parent / link.split("#", 1)[0]).resolve()
            assert target.exists(), str(p) + " -> " + link
            link_count += 1
    print(f"PASS relative documentation links: {link_count}", flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, env=env, check=True)
    print("PASS offline tests; no network operations performed", flush=True)

if __name__ == "__main__":
    main()
