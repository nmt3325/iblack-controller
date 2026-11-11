import importlib.util,io,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location('client_'+name,ROOT/'client'/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
ctl=load('camctl');rtmp=load('rtmp_probe')
class Sock:
 def __init__(self,data):self.data=data;self.sent=b''
 def recv(self,n):r=self.data[:min(n,2)];self.data=self.data[len(r):];return r
 def sendall(self,b):self.sent+=b
 def close(self):pass
def camera(data):
 c=ctl.Camera.__new__(ctl.Camera);c.s=Sock(data);return c
class ControlTests(unittest.TestCase):
 def test_save_status_zero_is_success(self):
  c=camera(bytes([65,3,255,0]));self.assertEqual(c.request(65,3),b'');self.assertEqual(c.s.sent,bytes([65,3,0,0]))
 def test_standard_wifi_provision_status_zero_is_success(self):
  c=camera(bytes([72,8,255,0]));self.assertEqual(c.request(72,8,bytes(97)),b'');self.assertEqual(c.s.sent[:4],bytes([72,8,97,0]))
 def test_nonzero_status_is_error(self):
  with self.assertRaisesRegex(RuntimeError,'code=9'):camera(bytes([69,6,255,9])).request(69,6)
 def test_get_ff00_is_not_a_length(self):
  with self.assertRaises(RuntimeError):camera(bytes([74,41,255,0])).get(41)
 def test_length_endianness_and_short_reads(self):
  body=bytes(range(256))+b'test';c=camera(bytes([74,41])+len(body).to_bytes(2,'big')+body)
  self.assertEqual(c.get(41),body)
  c=camera(bytes([75,41,0,1,0]));c.set(41,b'x'*300);self.assertEqual(c.s.sent[:4],bytes([75,41,44,1]))
 def test_reply_command_must_match(self):
  with self.assertRaisesRegex(RuntimeError,'mismatch'):camera(bytes([74,5,0,0])).get(41)
 def test_night_changes_only_control_byte(self):
  original=bytes.fromhex('0019000f')+bytes(20)
  with tempfile.TemporaryDirectory() as d,patch.object(ctl,'D',Path(d)):
   (Path(d)/'stab').mkdir();c=ctl.Camera.__new__(ctl.Camera);c.get=lambda sub:original;c.backup=lambda sub,b:None
   got=[];c.set=lambda sub,b:(got.append((sub,bytes(b))) or b'\0')
   for mode,value in [('auto',0),('day',1),('night',2)]:
    self.assertTrue(c.night(mode)['accepted']);expected=bytearray(original);expected[4]=value;self.assertEqual(got[-1],(41,bytes(expected)))
   with self.assertRaises(ValueError):c.night('invalid')
 def test_failed_night_ack_is_not_success(self):
  c=ctl.Camera.__new__(ctl.Camera);c.get=lambda sub:bytes(24);c.backup=lambda *a:None;c.set=lambda *a:b'\1'
  with self.assertRaises(RuntimeError):c.night('night')
 def test_stream_stdout_is_binary_only_and_left_open(self):
  class Out:
   def __init__(self):self.buffer=io.BytesIO()
  class Fake:
   def __init__(self,*a,**k):
    self.s=Sock(b'');self.messages=iter([(20,0,0,b''.join(rtmp.amf_enc(v) for v in ['_result',1.,None,{}])),(20,0,0,b''.join(rtmp.amf_enc(v) for v in ['_result',2.,None,1.])),(8,1,0,b'\xaf\0\x15\x08'),(9,1,0,b'\x17\0\0\0\0')])
   def handshake(self):pass
   def set_chunk_size(self,n):pass
   def cmd(self,*a):pass
   def send_msg(self,*a):pass
   def handle_ctrl(self,*a):return None
   def read_msg(self):
    try:return next(self.messages)
    except StopIteration:raise EOFError()
  out=Out()
  with patch.object(rtmp,'Rtmp',Fake),patch.object(rtmp.sys,'stdout',out):r=rtmp.session('live','live',1,'-',False)
  self.assertFalse(out.buffer.closed);self.assertTrue(out.buffer.getvalue().startswith(b'FLV'));self.assertEqual((r['a'],r['v']),(1,1))
if __name__=='__main__':unittest.main()
