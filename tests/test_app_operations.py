"""Offline tests using synthetic Java only; no APK, credentials, network, or camera."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'research' / 'index_app_operations.py'
if not SCRIPT.exists():
    SCRIPT = pathlib.Path(__file__).with_name('index_app_operations.py')
spec = importlib.util.spec_from_file_location('app_index', SCRIPT)
idx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idx)


class SourceMethodsTests(unittest.TestCase):
    def test_mask_preserves_offsets(self):
        s = 'class A { /* }\n{ */ String x = "{ fake(); }"; }'
        masked = idx.mask(s)
        self.assertEqual(len(s), len(masked))
        self.assertEqual(s.count('\n'), masked.count('\n'))
        self.assertNotIn('fake', masked)

    def test_interface_declarations(self):
        _, methods = idx.source_methods('interface API {\n void play(String s);\n int stop();\n}')
        self.assertEqual([m['name'] for m in methods], ['play', 'stop'])
        self.assertTrue(all(m['direct'] and not m['has_body'] for m in methods))

    def test_constructor_exclusion(self):
        _, methods = idx.source_methods('class A {\n public A() {}\n public void start() {}\n}')
        self.assertEqual([m['name'] for m in methods], ['start'])

    def test_native_and_generic(self):
        _, methods = idx.source_methods('class A {\n public native int run(byte[] b);\n public <T> T value(Class<T> c) { return null; }\n}')
        self.assertEqual([m['name'] for m in methods], ['run', 'value'])
        self.assertFalse(methods[0]['has_body'])
        self.assertTrue(methods[1]['has_body'])

    def test_anonymous_listener_not_direct_outer_method(self):
        s = 'class A {\n Object cb = new Object() {\n public void onClick() { play(); }\n };\n public void stop() {}\n}'
        _, methods = idx.source_methods(s)
        by = {m['name']:m for m in methods}
        self.assertFalse(by['onClick']['direct'])
        self.assertTrue(by['stop']['direct'])

    def test_control_statements_not_methods(self):
        _, methods = idx.source_methods('class A {\n public void a() {\n if (x) {}\n while (x) {}\n }\n}')
        self.assertEqual([m['name'] for m in methods], ['a'])


class SyntheticIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        src = root / 'sources'
        files = {
            'com/iermu/client/ITest.java': 'package com.iermu.client;\npublic interface ITest {\n void play();\n void stop();\n}\n',
            'com/iermu/apiservice/service/TestService.java': 'interface TestService {\n @POST("/v2/demo")\n Result action(@Field("fileds") String data);\n}\n',
            'com/iermu/nativesdk/NativeSDKEngine.java': 'class NativeSDKEngine {\n private Object field = new Object();\n public native void nativePlay();\n public void init() { helper(); }\n}\n',
            'com/iermu/ui/TestScreen.java': 'class TestScreen {\n Object listener = new Object() {\n public void onClick() { api.play(); }\n };\n @OnClick({R.id.button_a, R.id.button_b})\n public void click() {\n // api.stop();\n String ignored = "api.stop()";\n switch (id) {\n case R.id.button_a: api.stop(); break;\n }\n }\n}\n',
            'com/iermu/opensdk/lan/utils/LanUtil.java': 'class LanUtil {\n public void cmd() {\n c.cmsMainCmd=74;\n c.cmsSubCmd=5;\n c.cmsMainCmd=75;\n c.cmsSubCmd=5;\n // c.cmsSubCmd=999;\n String ignored="c.cmsSubCmd=999;";\n CmsCmdStruct s=new CmsCmdStruct(65, 3);\n }\n}\n',
        }
        for rel, data in files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        out = root / 'index.json'
        subprocess.run([sys.executable, str(SCRIPT), '--source-root', str(src), '--output', str(out)], check=True, stdout=subprocess.PIPE, text=True)
        cls.result = json.loads(out.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contract_counts_and_check_scope(self):
        self.assertEqual(self.result['scope']['contract_methods_by_kind'], {'http':1, 'business':2, 'native_entry':2})
        self.assertEqual(self.result['unparsed_contract_declarations'], [])
        self.assertEqual(self.result['unparsed_check_scope'], ['business', 'lan', 'http'])
        self.assertFalse(self.result['network_or_camera_requests_performed'])

    def test_field_initializer_ui_and_ignored_literals(self):
        refs = self.result['ui_references']
        self.assertEqual([r['target'] for r in refs], ['play', 'stop'])
        self.assertEqual(refs[0]['enclosing_method'], 'onClick')
        http = next(c for c in self.result['contracts'] if c['kind']=='http')
        self.assertEqual(http['http_annotations'], [['POST','/v2/demo']])
        self.assertEqual(http['wire_fields'], [['Field','fileds']])

    def test_ui_action_ids(self):
        actions = self.result['ui_action_sites']
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]['view_ids'], ['button_a','button_b'])
        self.assertEqual(actions[1]['view_ids'], ['button_a'])

    def test_ordered_cms_assignments(self):
        cmd = self.result['cms_command_construction'][0]
        self.assertEqual([a['value'] for a in cmd['assignments']], [74,5,75,5])
        self.assertEqual(cmd['constructors'][0]['arguments'], '65, 3')


if __name__ == '__main__':
    unittest.main()
