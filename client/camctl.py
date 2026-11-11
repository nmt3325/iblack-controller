#!/usr/bin/env python3
"""Local camera CMS control. Credentials are read from the existing private setup."""
import ast, socket, struct, json, os, time, pathlib, argparse, ipaddress, sys
D=pathlib.Path(__file__).resolve().parent

NETSET_LENGTH = 44

class DNSUpdateError(RuntimeError):
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result

def dns_address(value, secondary=False):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('DNS must be an IPv4 address, not a hostname or IPv6 address') from exc
    if address.is_multicast or int(address) == 0xffffffff:
        raise ValueError('DNS must be a unicast IPv4 address')
    if address.is_unspecified and not secondary:
        raise ValueError('Primary DNS cannot be 0.0.0.0')
    return address

def parse_network_config(body):
    if len(body) != NETSET_LENGTH:
        raise RuntimeError('Unsupported NETSET structure length; expected 44 bytes')
    if body[3] not in (0, 1):
        raise RuntimeError('Unsupported NETSET DHCP flag; refusing to guess layout')
    def address(offset):
        return str(ipaddress.IPv4Address(bytes(body[offset:offset + 4])))
    return {'dhcp': bool(body[3]), 'ip': address(4), 'netmask': address(8),
            'gateway': address(12), 'primary_dns': address(16),
            'secondary_dns': address(20)}

def patch_network_dns(body, primary, secondary=None):
    parse_network_config(body)
    updated = bytearray(body)
    updated[16:20] = dns_address(primary).packed
    if secondary is not None:
        updated[20:24] = dns_address(secondary, secondary=True).packed
    return bytes(updated)

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
 def dns(self):
  return parse_network_config(self.get(8))
 def set_dns(self,primary,secondary=None,*,apply=False,persist=False,experimental=False,app_sequence=False,static=False):
  primary=str(dns_address(primary))
  if secondary is not None:secondary=str(dns_address(secondary,secondary=True))
  if persist and not apply:raise ValueError('--save requires --apply')
  if static and not app_sequence:raise ValueError('--static requires --app-save-sequence')
  if app_sequence and apply and not persist:raise ValueError('--app-save-sequence requires --save when applying')
  if apply and not experimental:raise ValueError('Per-device experimental DNS writes require --experimental explicitly')
  original=self.get(8)
  before=parse_network_config(original)
  updated=bytearray(patch_network_dns(original,primary,secondary))
  if static:updated[3]=0
  updated=bytes(updated)
  changed=[i for i,(a,b) in enumerate(zip(original,updated)) if a!=b]
  result={'before':before,'requested':parse_network_config(updated),'changed_offsets':changed,
          'dry_run':not apply,'applied':False,'readback_verified':False,'saved':False,
          'save_attempted':False,'save_acknowledged':False,
          'effective_dns_verified':False,'reboot_persistence_verified':False,
          'layout_status':'DNS offsets live-validated on the reference camera; other firmware remains experimental',
          'commit_order':'save_then_reconnect_readback' if app_sequence else 'readback_then_save',
          'warnings':[]}
  if before['dhcp'] and not static:
   result['warnings'].append('DHCP remains enabled; DHCP may override these DNS fields. Effective resolver requires packet verification.')
  if static:
   result['warnings'].append('Static addressing keeps the current IP, mask and gateway. Reserve or exclude this IP in the router DHCP pool.')
  if app_sequence:
   result['warnings'].append('Explicit app sequence commits before readback and may briefly interrupt networking; backup is retained on failure.')
  if not changed:
   result['unchanged']=True
   return result
  if not apply:return result
  stage='backup'
  try:
   result['backup']=str(self.backup(8,original))
   stage='set'
   ack=self.set(8,updated)
   if ack!=b'\x00':raise RuntimeError('Unexpected NETSET acknowledgment; no save sent')
   result['write_acknowledged']=True
   if app_sequence:
    stage='save'
    result['save_attempted']=True
    if self.request(65,3)!=b'':raise RuntimeError('Unexpected save acknowledgment')
    result['save_acknowledged']=True
    stage='post_save_readback'
    observed=self.wait_network_readback(updated)
    if observed!=updated:raise RuntimeError('Post-save NETSET readback differs; result remains unconfirmed')
    result.update(readback_verified=True,applied=True,saved=True)
    return result
   stage='readback'
   observed=self.get(8)
   if observed!=updated:raise RuntimeError('NETSET readback differs from requested bytes; no save sent')
   result['readback_verified']=True
   result['applied']=True
   if persist:
    stage='save'
    result['save_attempted']=True
    if self.request(65,3)!=b'':raise RuntimeError('Unexpected save acknowledgment')
    result['save_acknowledged']=True
    stage='post_save_readback'
    if self.get(8)!=updated:raise RuntimeError('NETSET changed after save; persistence not confirmed')
    result['saved']=True
   return result
  except (OSError,RuntimeError,EOFError) as exc:
   result['failed_stage']=stage
   raise DNSUpdateError(str(exc),result) from exc
 def wait_network_readback(self,expected):
  host,port=self.s.getpeername()
  self.close()
  for delay in (2,3,5,8,8,8):
   time.sleep(delay)
   peer=None
   try:
    peer=Camera(host,port)
    observed=peer.get(8)
    parse_network_config(observed)
    if observed==expected:return observed
   except (OSError,RuntimeError,EOFError):pass
   finally:
    if peer is not None:peer.close()
  raise RuntimeError('Save acknowledged but exact NETSET readback was not recovered; do not repeat writes blindly')
 def restore_dns(self,backup,*,apply=False,persist=False,experimental=False,app_sequence=False):
  saved=pathlib.Path(backup).read_bytes()
  config=parse_network_config(saved)
  return self.set_dns(config['primary_dns'],config['secondary_dns'],apply=apply,persist=persist,experimental=experimental,app_sequence=app_sequence)
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
  dest=D/'stab'/('backup-param-%d-%s-%d.bin'%(sub,time.strftime('%Y%m%d-%H%M%S'),time.time_ns()))
  fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as handle:handle.write(body)
  return dest
 def status(self):
  ir=self.get(41);wifi=self.get(38)
  return {'host':self.s.getpeername()[0],'wifi_ssid':wifi[2:34].split(b'\0')[0].decode('utf8','replace'),'infrared_raw':ir.hex(),'infrared_thresholds':list(struct.unpack('>HH',ir[:4])),'infrared_bytes_after_thresholds':list(ir[4:8])}
def main(argv=None):
 ap=argparse.ArgumentParser(description='Local CMS camera controls; DNS writes are experimental')
 ap.add_argument('action',choices=['status','get','night','dns','dns-set','dns-restore'])
 ap.add_argument('param',nargs='?',help='parameter number, night mode, primary DNS, or DNS backup file')
 ap.add_argument('--host');ap.add_argument('--port',type=int,default=3357)
 ap.add_argument('--secondary',help='Secondary DNS; omitted means preserve; 0.0.0.0 clears this field')
 ap.add_argument('--apply',action='store_true',help='Write DNS changes; default is read-only preview')
 ap.add_argument('--save',action='store_true',help='Save explicitly; app sequence saves before delayed readback')
 ap.add_argument('--experimental',action='store_true',help='Explicitly allow per-device experimental DNS writes')
 ap.add_argument('--app-save-sequence',action='store_true',help='Use APK set/save order, then reconnect and verify; applying requires --save')
 ap.add_argument('--static',action='store_true',help='Explicitly disable DHCP, keeping the current IP/mask/gateway; requires app sequence')
 a=ap.parse_args(argv)
 dns_write=a.action in ('dns-set','dns-restore')
 if (a.apply or a.save or a.experimental or a.app_save_sequence or a.static) and not dns_write:ap.error('DNS write flags require dns-set or dns-restore')
 if a.secondary is not None and a.action!='dns-set':ap.error('--secondary requires dns-set')
 if a.static and (a.action!='dns-set' or not a.app_save_sequence):ap.error('--static requires dns-set and --app-save-sequence')
 if a.app_save_sequence and a.apply and not a.save:ap.error('--app-save-sequence requires --save when applying')
 if a.save and not a.apply:ap.error('--save requires --apply')
 if a.apply and not a.experimental:ap.error('--apply requires explicit --experimental for this device-specific workflow')
 if a.action in ('get','night','dns-set','dns-restore') and a.param is None:ap.error('param is required')
 if a.action in ('status','dns') and a.param is not None:ap.error('this action does not take a parameter')
 if a.action=='get':
  if not a.param.isdigit() or not 0<=int(a.param)<=255:ap.error('param must be an integer from 0 to 255')
  if int(a.param) in [8,25,26,27,38,39,48,52,59,62,63]:ap.error('Sensitive parameters are not printed; use dns for redacted network settings')
 if a.action=='night' and a.param not in ('auto','day','night'):ap.error('mode must be auto, day, or night')
 if a.action=='dns-set':
  try:
   dns_address(a.param)
   if a.secondary is not None:dns_address(a.secondary,secondary=True)
  except ValueError as exc:ap.error(str(exc))
 if a.action=='dns-restore':
  try:parse_network_config(pathlib.Path(a.param).read_bytes())
  except (OSError,RuntimeError) as exc:ap.error(str(exc))
 c=None
 try:
  c=Camera(a.host,a.port)
  if a.action=='status':result=c.status()
  elif a.action=='night':result=c.night(a.param)
  elif a.action=='dns':result=c.dns()
  elif a.action=='dns-set':result=c.set_dns(a.param,a.secondary,apply=a.apply,persist=a.save,experimental=a.experimental,app_sequence=a.app_save_sequence,static=a.static)
  elif a.action=='dns-restore':result=c.restore_dns(a.param,apply=a.apply,persist=a.save,experimental=a.experimental,app_sequence=a.app_save_sequence)
  else:
   print(c.get(int(a.param)).hex())
   return 0
  print(json.dumps(result,ensure_ascii=False,indent=2))
  return 0
 except DNSUpdateError as exc:
  print(json.dumps({'error':str(exc),'result':exc.result},ensure_ascii=False,indent=2),file=sys.stderr)
  return 1
 except (OSError,RuntimeError,ValueError,EOFError) as exc:
  print(json.dumps({'error':str(exc)},ensure_ascii=False),file=sys.stderr)
  return 1
 finally:
  if c is not None:c.close()

if __name__=='__main__':
 raise SystemExit(main())
