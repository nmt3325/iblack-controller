#!/usr/bin/env python3
"""Refresh only the current-file manifest; never touch initial references."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"evidence/current-source-manifest.json", "evidence/repository-audit.json"}

def digest_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def main():
    raw = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    rows = []
    for name in sorted(set(raw.decode().split("\0")) - {""}):
        p = ROOT / name
        if p.is_file() and name not in EXCLUDED:
            rows.append({"path": name, "sha256": digest_file(p)})
    data = {
        "recorded_at": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds"),
        "scope": "Lightweight source repository only; external APK-derived originals and toolchains are indexed separately, not tracked; runtime secrets/media excluded; initial reference manifest unchanged; this manifest and audit report excluded",
        "files": rows,
    }
    p = ROOT / "evidence/current-source-manifest.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"Updated current snapshot checksums: {len(rows)} files")

if __name__ == "__main__":
    main()
