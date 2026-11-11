import sys,struct,re
from pathlib import Path
sys.path.insert(0,'retool')
from elftools.elf.elffile import ELFFile
from capstone import *
p=Path('lib/armeabi-v7a/libcmsffmpeg.so'); raw=p.read_bytes(); elf=ELFFile(p.open('rb'))
segs=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
def get(a,n=4):
 for s in segs:
  if s['p_vaddr']<=a<s['p_vaddr']+s['p_filesz']:
   o=int(s['p_offset']+a-s['p_vaddr']);return raw[o:o+n]
 return b''
targets={}
for word in [b'devtype=',b'devtype\0',b'accesstoken\0',b'NetStream.Publish.Start',b'publish\0']:
 o=raw.find(word)
 for s in segs:
  if s['p_offset']<=o<s['p_offset']+s['p_filesz']:
   targets[int(s['p_vaddr']+o-s['p_offset'])]=word.decode().rstrip('\0')
print('TARGETS',[(hex(a),v) for a,v in targets.items()])
text=elf.get_section_by_name('.text');md=Cs(CS_ARCH_ARM,CS_MODE_THUMB);md.skipdata=True
ins=list(md.disasm_lite(text.data(),text['sh_addr']));regs={};hits=[]
for i,(a,n,m,o) in enumerate(ins):
 if m.startswith('ldr'):
  z=re.match(r'(r\d+), \[pc(?:, #(-?0x[0-9a-f]+|-?\d+))?\]',o)
  if z:
   b=get(((a+4)&~3)+(int(z[2],0) if z[2] else 0))
   if len(b)==4:
    v=struct.unpack('<I',b)[0];regs[z[1]]=(v,i)
    if v in targets:hits.append((i,targets[v],v))
 if m.startswith('add'):
  z=re.match(r'(r\d+), pc$',o)
  if z and z[1] in regs:
   v,j=regs[z[1]]
   if i-j<20:
    t=(a+4+v)&0xffffffff
    if t in targets:hits.append((i,targets[t],t))
for i,w,t in hits:
 print('\nXREF',w,hex(t))
 for a,n,m,o in ins[max(0,i-6):i+8]:print('%08x %-8s %s'%(a,m,o))
