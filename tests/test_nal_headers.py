import importlib.util,unittest
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'client/fix_flv.py'
s=importlib.util.spec_from_file_location('current_fixer',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def nal(data):return len(data).to_bytes(4,'big')+data
class NalHeaderTests(unittest.TestCase):
 def test_plausible_trailer_length_with_type_zero_is_trimmed(self):
  good=nal(b'\x41\x80');tail=nal(b'\x00')
  self.assertEqual(m.fix_payload(good+tail,4,'truncate'),(good,len(tail)))
 def test_forbidden_bit_in_trailer_is_trimmed(self):
  good=nal(b'\x65\x80');tail=nal(b'\x81x')
  self.assertEqual(m.fix_payload(good+tail,4,'truncate'),(good,len(tail)))
 def test_valid_multi_nal_payload_is_kept(self):
  good=nal(b'\x67abc')+nal(b'\x68abc')+nal(b'\x65abc')
  self.assertEqual(m.fix_payload(good,4,'truncate'),(good,0))
