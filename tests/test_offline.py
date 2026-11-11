import ast
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "reference/iermu_cam"

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

fix = load("iermu_fix_flv", CODE / "fix_flv.py")
rtmp = load("iermu_rtmp", CODE / "rtmp_probe.py")

def video_nal(data):
    return len(data).to_bytes(4, "big") + data

def tag(kind, body, ts=0):
    header = bytes([kind]) + len(body).to_bytes(3, "big") + (ts & 0xffffff).to_bytes(3, "big") + bytes([ts >> 24]) + b"\x00\x00\x00"
    return header + body + struct.pack(">I", 11 + len(body))

class OfflineTests(unittest.TestCase):
    def test_short_reads(self):
        class Short(io.BytesIO):
            def read(self, n=-1):
                return super().read(min(n, 2))
        f = Short(b"abcdef")
        self.assertEqual(fix.readn(f, 5), b"abcde")
        self.assertEqual(fix.readn(f, 5), b"f")

    def test_valid_nal_is_unchanged(self):
        p = video_nal(b"\x41\x11\x22")
        self.assertEqual(fix.fix_payload(p, 4, "truncate"), (p, 0))

    def test_truncate_at_valid_nal_boundary(self):
        p = video_nal(b"\x41\x11\x22")
        self.assertEqual(fix.fix_payload(p + b"\xff" * 79, 4, "truncate"), (p, 79))

    def test_extend_length(self):
        p = video_nal(b"\x41\x11\x22")
        out, left = fix.fix_payload(p + b"\xff" * 79, 4, "extend")
        self.assertEqual(left, 79)
        self.assertEqual(int.from_bytes(out[:4], "big"), 82)
        self.assertEqual(out[4:], p[4:] + b"\xff" * 79)

    def test_no_valid_nal_is_unchanged(self):
        p = b"\x00\x00\x00\x00bad"
        self.assertEqual(fix.fix_payload(p, 4, "truncate"), (p, 0))

    def test_aac_end_condition(self):
        self.assertTrue(fix.aac_ends_at(b"\x01\x07", 2))
        self.assertFalse(fix.aac_ends_at(b"\x01\x04", 2))
        self.assertFalse(fix.aac_ends_at(b"\x00\x00", 2))
        self.assertFalse(fix.aac_ends_at(b"\x07", 1))

    def test_flv_tag_lengths_and_previous_tag_size(self):
        header = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00" * 4
        vp = b"\x27\x01\x00\x00\x00" + video_nal(b"\x41\x11\x22")
        ap = b"\xaf\x01\x01\x07"
        src = header + tag(9, vp + b"\xff" * 79, 10) + tag(8, ap + b"\x80" * 89, 10) + tag(18, b"metadata", 10)
        p = subprocess.run([sys.executable, "-B", str(CODE / "fix_flv.py"), "-", "-"], input=src, capture_output=True, check=True)
        expected = header + tag(9, vp, 10) + tag(8, ap, 10) + tag(18, b"metadata", 10)
        self.assertEqual(p.stdout, expected)

    def test_amf_round_trip(self):
        for v in (None, True, False, 1.5, "live", {"app": "live", "value": 1.0}):
            with self.subTest(value=v):
                b = rtmp.amf_enc(v)
                out, consumed = rtmp.amf_dec(b)
                self.assertEqual(out, v)
                self.assertEqual(consumed, len(b))

    def test_request_header_from_original_function(self):
        s = (CODE / "iermu_run.sh").read_text()
        code = s.split("python3 - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(code)
        fun = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "send_cmd")
        ns = {}
        exec(compile(ast.Module(body=[fun], type_ignores=[]), "original-send-cmd", "exec"), ns)
        class Sink:
            def sendall(self, b):
                self.data = b
        for size in (0, 40, 97, 300):
            with self.subTest(size=size):
                sink = Sink()
                body = b"x" * size
                ns["send_cmd"](sink, 72, 8, body)
                self.assertEqual(sink.data[:4], bytes([72, 8, size & 255, size >> 8]))
                self.assertEqual(sink.data[4:], body)

if __name__ == "__main__":
    unittest.main()
