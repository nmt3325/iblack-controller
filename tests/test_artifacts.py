import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

PATH = Path(__file__).resolve().parents[1] / 'tools/verify_artifacts.py'
spec = importlib.util.spec_from_file_location('verify_artifacts', PATH)
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


class ArtifactTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'artifacts/apk').mkdir(parents=True)
        (root / 'artifacts/dex').mkdir()
        (root / 'artifacts/native/lib/armeabi-v7a').mkdir(parents=True)
        (root / 'research/decompiled/sources').mkdir(parents=True)
        apk = root / 'artifacts/apk/app.apk'
        entries = {'classes.dex': b'dex-fixture', 'lib/armeabi-v7a/test.so': b'library-fixture'}
        with zipfile.ZipFile(apk, 'w') as z:
            for name, data in entries.items():
                z.writestr(name, data)
        rows = [{'path': 'artifacts/apk/app.apk'}]
        for name, data in entries.items():
            target = 'artifacts/dex/' + name if name.endswith('.dex') else 'artifacts/native/' + name
            (root / target).write_bytes(data)
            rows.append({'path': target, 'apk_entry': name})
        for row in rows:
            path = root / row['path']
            row.update(bytes=path.stat().st_size, sha256=artifacts.digest_file(path))
        source = root / 'research/decompiled/sources'
        (source / 'A.java').write_text('class A {}\n')
        manifest = {'version': 1, 'apk': {'path': 'artifacts/apk/app.apk'}, 'files': rows,
                    'java_source_tree': dict(root='research/decompiled/sources', **artifacts.tree_metadata(source))}
        (root / 'artifacts/manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_streaming_digest(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'data'
            path.write_bytes(b'abc')
            self.assertEqual(artifacts.digest_file(path), hashlib.sha256(b'abc').hexdigest())

    def test_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as name:
            for path in ['/outside', '../outside', 'a/../../outside', 'a\\outside', '']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    artifacts.safe_path(Path(name), path)

    def test_outward_symlink(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / 'repo'
            root.mkdir()
            (root / 'link').symlink_to(Path(name))
            with self.assertRaises(ValueError):
                artifacts.safe_path(root, 'link/outside')

    def test_tree_order_and_totals(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'B.java').write_bytes(b'b')
            (root / 'A.java').write_bytes(b'aa')
            first = artifacts.tree_metadata(root)
            self.assertEqual(first['file_count'], 2)
            self.assertEqual(first['bytes'], 3)
            self.assertEqual(first, artifacts.tree_metadata(root))

    def test_tree_rename_changes_identity(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'A.java').write_bytes(b'content')
            first = artifacts.tree_metadata(root)
            (root / 'A.java').rename(root / 'B.java')
            self.assertNotEqual(first['sha256'], artifacts.tree_metadata(root)['sha256'])

    def test_complete_small_archive(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.fixture(root)
            result = artifacts.verify(root)
            self.assertTrue(result['verified'])
            self.assertEqual(result['apk_extracted_entries'], 2)
            self.assertEqual(result['java_files'], 1)

    def test_changed_binary_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.fixture(root)
            (root / 'artifacts/dex/classes.dex').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                artifacts.verify(root)

    def test_changed_java_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            self.fixture(root)
            (root / 'research/decompiled/sources/A.java').write_text('changed')
            with self.assertRaises(ValueError):
                artifacts.verify(root)


if __name__ == '__main__':
    unittest.main()
