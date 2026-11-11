#!/usr/bin/env python3
"""Observe only scoped packet headers and DNS names; never persist payloads/MACs."""
import socket,struct,select,time,json,argparse,collections,os
p=argparse.ArgumentParser();p.add_argument('--seconds',type=float,default=65);p.add_argument('--output',required=True);a=p.parse_args()
socks={}
for dev in ['ens18','iermu-sta0']:
 s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3));s.bind((dev,0));s.setblocking(False);socks[s]=dev
stats=collections.Counter();queries=collections.Counter();answers=collections.Counter();syns=collections.Counter();synacks=collections.Counter();resets=collections.Counter()
allowed={'192.168.1.3','192.168.10.35'};targets={'192.168.1.201','192.168.10.22'};ports={53,443,3399,3368,1935,8443}
def name(b,o,depth=0):
 if depth>8:raise ValueError('DNS pointer depth')
 parts=[];end=None
 while o<len(b):
  n=b[o];o+=1
  if n==0:return '.'.join(parts),end or o
  if n&192==192:
   ptr=((n&63)<<8)|b[o];end=o+1;v,_=name(b,ptr,depth+1);parts.append(v);return '.'.join(parts),end
  if n>63 or o+n>len(b):raise ValueError('DNS label')
  parts.append(b[o:o+n].decode('ascii','replace'));o+=n
 raise ValueError('DNS truncation')
t0=time.monotonic()
while time.monotonic()-t0<a.seconds:
 ready,_,_=select.select(list(socks),[],[],.2)
 for s in ready:
  b,_=s.recvfrom(65535)
  if len(b)<42 or b[12:14]!=b'\x08\x00':continue
  off=14+(b[14]&15)*4;src=socket.inet_ntoa(b[26:30]);dst=socket.inet_ntoa(b[30:34]);proto=b[23]
  toward=src in allowed and dst in targets;back=src in targets and dst in allowed
  if not (toward or back) or proto not in (6,17) or len(b)<off+8:continue
  sp,dp=struct.unpack('!HH',b[off:off+4]);port=dp if toward else sp
  if port not in ports:continue
  key=f'{socks[s]} {src} -> {dst} '+('TCP' if proto==6 else 'UDP')+f' server_port={port}'
  stats[key]+=1
  if proto==6:
   if len(b)<off+20:continue
   flags=b[off+13]
   if flags&0x12==2:syns[key]+=1
   if flags&0x12==0x12:synacks[key]+=1
   if flags&4:resets[key]+=1
  elif port==53:
   d=b[off+8:]
   try:
    if len(d)<12:continue
    qname,nxt=name(d,12)
    if len(qname)>180:continue
    qr=bool(d[2]&128)
    if not qr:queries[f'{src} -> {dst} {qname}']+=1
    else:
     qd,an=struct.unpack('!HH',d[4:8]);o=12
     for _ in range(qd):_,o=name(d,o);o+=4
     for _ in range(min(an,20)):
      _,o=name(d,o);typ,cl,ttl,ln=struct.unpack('!HHIH',d[o:o+10]);o+=10
      if typ==1 and ln==4:answers[f'{qname} = {socket.inet_ntoa(d[o:o+4])}']+=1
      o+=ln
   except (ValueError,IndexError,struct.error):pass
for s in socks:s.close()
r={'duration_seconds':round(time.monotonic()-t0,1),'packets':dict(stats),'dns_queries':dict(queries),'dns_a_answers':dict(answers),'tcp_syn':dict(syns),'tcp_synack':dict(synacks),'tcp_resets':dict(resets),'raw_payloads_saved':False,'mac_addresses_saved':False}
fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
os.fchown(fd,int(os.environ.get('SUDO_UID','1000')),int(os.environ.get('SUDO_GID','1000')))
with os.fdopen(fd,'w') as f:json.dump(r,f,indent=2);f.write('\n')
