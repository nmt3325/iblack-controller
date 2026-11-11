#!/usr/bin/env python3
"""Local camera CMS control. Credentials are read from the existing private setup."""
import ast, socket, struct, json, os, time, pathlib, argparse
D=pathlib.Path(__file__).resolve().parent
class Camera:
 def __init__(self,host=None,port=3357):
  host=host or os.environ.get('CAMERA_IP','192.168.10.34')
  self.s=socket.create_connection((host,port),4);self.s.settimeout(4)
  self.request(5,17)
  env_user=os.environ.get('CAMERA_USER');env_pwd=os.environ.get('CAMERA_PASSWORD')
  if env_user is not None and env_pwd is not None:
   user,pwd=env_user.encode(),env_pwd.encode()
  elif env_user is not None or env_pwd is not None:
   self.s.close();raise RuntimeError('Set both CAMERA_USER and CAMERA_PASSWORD')
  elif (D/'cms_info.py').exists():
   tree=ast.parse((D/'cms_info.py').read_text())
   for n in tree.body:
    if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Tuple) and [x.id for x in n.targets[0].elts]==['USER','PWD']:
     user,pwd=ast.literal_eval(n.value);break
   else:raise RuntimeError('Existing credentials not found')
  else:
   self.s.close();raise RuntimeError('Set CAMERA_USER and CAMERA_PASSWORD; no default credentials are bundled')
  if len(user)>20 or len(pwd)>20:
   self.s.close();raise ValueError('CMS credentials must be at most 20 bytes each')
  self.request(69,6,user.ljust(20,b'\0')+pwd.ljust(20,b'\0'))
 def recvn(self,n):
  buf=b''
  while len(buf)<n:
   c=self.s.recv(n-len(buf))
   if not c:raise EOFError('Camera closed connection')
   buf+=c
  return buf
 def request(self,main,sub,body=b''):
  self.s.sendall(bytes([main&255,sub])+struct.pack('<H',len(body))+body)
  h=self.recvn(4)
  if h[:2]!=bytes([main&255,sub]):raise RuntimeError('Response command mismatch')
  self.last_header=h
  if h[2]==255:
   if h[3]==0 and main not in (74,75):return b''
   raise RuntimeError('CMS rejected %d/%d code=%d'%(main,sub,h[3]))
  n=int.from_bytes(h[2:4],'big')
  if n>50000:raise RuntimeError('Unexpected reply size')
  r=self.recvn(n)
  if h[:2]!=bytes([main&255,sub]):raise RuntimeError('Response command mismatch')
  return r
 def get(self,sub):return self.request(74,sub)
 def set(self,sub,body):return self.request(75,sub,body)
 def night(self,mode):
  modes={'auto':0,'day':1,'night':2}
  if mode not in modes:raise ValueError('mode must be auto, day, or night')
  original=self.get(41)
  if len(original)!=24:raise RuntimeError('Unsupported infrared structure length')
  self.backup(41,original)
  body=bytearray(original);body[4]=modes[mode]
  ack=self.set(41,body)
  if ack!=b'\x00':raise RuntimeError('Unexpected infrared acknowledgment')
  result={'requested_mode':mode,'accepted':True,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'note':'Runtime control; camera does not report active mode in GET 41.'}
  dest=D/'stab'/'last-night-request.json';dest.write_text(json.dumps(result));dest.chmod(0o600)
  return result
 def close(self):self.s.close()
 def backup(self,sub,body=None):
  body=self.get(sub) if body is None else body
  (D/'stab').mkdir(mode=0o700,exist_ok=True)
  dest=D/'stab'/('backup-param-%d-%s.bin'%(sub,time.strftime('%Y%m%d-%H%M%S')))
  dest.write_bytes(body);dest.chmod(0o600);return dest
 def status(self):
  ir=self.get(41);wifi=self.get(38)
  return {'host':self.s.getpeername()[0],'wifi_ssid':wifi[2:34].split(b'\0')[0].decode('utf8','replace'),'infrared_raw':ir.hex(),'infrared_thresholds':list(struct.unpack('>HH',ir[:4])),'infrared_bytes_after_thresholds':list(ir[4:8])}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['status','get','night']);ap.add_argument('param',nargs='?');ap.add_argument('--host');ap.add_argument('--port',type=int,default=3357);a=ap.parse_args();c=Camera(a.host,a.port)
 try:
  if a.action=='status':print(json.dumps(c.status(),ensure_ascii=False,indent=2))
  elif a.action=='night':print(json.dumps(c.night(a.param),ensure_ascii=False,indent=2))
  elif a.param is None:ap.error('param is required')
  elif not a.param.isdigit():ap.error('param must be an integer')
  elif int(a.param) in [25,26,27,38,39,48,52,59,62,63]:ap.error('Sensitive parameters are not printed')
  else:print(c.get(int(a.param)).hex())
 finally:c.close()
