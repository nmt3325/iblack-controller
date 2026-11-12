#!/usr/bin/env python3
"""Verify preserved analysis inputs offline; never run the APK or contact a camera."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def digest_stream(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(chunk)
    return h.hexdigest()


def digest_file(path):
    with path.open('rb') as stream:
        return digest_stream(stream)


def safe_path(root, name):
    raw = PurePosixPath(name)
    if not name or '\\' in name or raw.is_absolute() or '..' in raw.parts:
        raise ValueError('Unsafe artifact path')
    root = root.resolve()
    path = root.joinpath(*raw.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError('Artifact path escapes repository')
    return path


def tree_metadata(root):
    rows = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Unexpected symlink in decompiled source tree')
        if path.is_file():
            rows.append((path.relative_to(root).as_posix(), path.stat().st_size,
                         digest_file(path)))
    h = hashlib.sha256()
    for name, size, sha in rows:
        h.update((name + '\0' + sha + '\n').encode('utf-8'))
    return {'file_count': len(rows), 'bytes': sum(row[1] for row in rows),
            'sha256': h.hexdigest()}


def verify(root, manifest_path=None):
    manifest_path = manifest_path or root / 'artifacts/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != 1:
        raise ValueError('Unsupported artifact manifest version')
    rows = manifest['files']
    if len({row['path'] for row in rows}) != len(rows):
        raise ValueError('Duplicate artifact path')
    for row in rows:
        path = safe_path(root, row['path'])
        if path.stat().st_size != row['bytes'] or digest_file(path) != row['sha256']:
            with path.open('rb') as stream:
                pointer = stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1')
            if pointer:
                raise ValueError('Artifact payload is a pointer, not the original file; extract the separate archive listed in artifacts/archive.json')
            raise ValueError('Artifact checksum mismatch: ' + row['path'])
    tree = manifest['java_source_tree']
    actual = tree_metadata(safe_path(root, tree['root']))
    if any(actual[key] != tree[key] for key in ('file_count', 'bytes', 'sha256')):
        raise ValueError('Decompiled Java tree does not match the preserved original output')
    apk = safe_path(root, manifest['apk']['path'])
    extracted = [row for row in rows if 'apk_entry' in row]
    with zipfile.ZipFile(apk) as archive:
        entries = {info.filename for info in archive.infolist()
                   if info.filename.endswith('.dex') or
                   (info.filename.startswith('lib/') and info.filename.endswith('.so'))}
        if {row['apk_entry'] for row in extracted} != entries:
            raise ValueError('DEX/native export does not cover all APK entries')
        for row in extracted:
            with archive.open(row['apk_entry']) as stream:
                if digest_stream(stream) != row['sha256']:
                    raise ValueError('Extracted file differs from the original APK')
    return {'verified': True, 'artifact_files': len(rows),
            'artifact_bytes': sum(row['bytes'] for row in rows),
            'java_files': actual['file_count'], 'java_bytes': actual['bytes'],
            'apk_extracted_entries': len(extracted)}


def main():
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description='Verify the separately stored original analysis archive offline')
    parser.add_argument('--root', type=Path, required=True, help='Root of the extracted external artifact archive')
    args = parser.parse_args()
    try:
        result = verify(args.root, manifest_path=repository / 'artifacts/manifest.json')
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        print('FAIL analysis artifacts: ' + str(error), file=sys.stderr)
        return 1
    print('PASS original analysis artifacts: ' + json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
