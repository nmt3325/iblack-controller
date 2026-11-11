#!/usr/bin/env python3
import sys,re,struct
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'retool'))
from elftools.elf.elffile import ELFFile
from capstone import *
f=open(sys.argv[1],'rb'); elf=ELFFile(f); raw=Path(sys.argv[1]).read_bytes()
syms={}
for section in elf.iter_sections():
 if section['sh_type'] in ['SHT_DYNSYM','SHT_SYMTAB']:
  for s in section.iter_symbols():
   if s['st_value']:syms[s.name]=(int(s['st_value']),int(s['st_size']))
byaddr={v&~1:k for k,(v,n) in syms.items()}
rel=elf.get_section_by_name('.rel.plt'); plt=elf.get_section_by_name('.plt')
if rel and plt:
 ds=elf.get_section(rel['sh_link'])
 for i,r in enumerate(rel.iter_relocations()):byaddr[int(plt['sh_addr'])+20+i*12]=ds.get_symbol(r['r_info_sym']).name+'@plt'
def get(addr,n):
 for seg in elf.iter_segments():
  if seg['p_type']=='PT_LOAD' and seg['p_vaddr']<=addr<seg['p_vaddr']+seg['p_filesz']:
   off=int(seg['p_offset']+addr-seg['p_vaddr']);return raw[off:off+n]
 return b''
def describe(addr):
 name=byaddr.get(addr)
 if name:return name
 return ''
for arg in sys.argv[2:]:
 if arg in syms:addr,n=syms[arg]
 else:
  pair=arg.split(':');addr=int(pair[0],0);n=int(pair[1],0) if len(pair)>1 else 256
 n=n or 256;md=Cs(CS_ARCH_ARM,CS_MODE_THUMB if addr&1 else CS_MODE_ARM);base=addr&~1
 print('\n===',arg,hex(addr),'size',n,'===')
 for ins in md.disasm(get(base,n),base):
  note=''
  if ins.mnemonic in ['bl','blx','b','beq','bne','bhi','blo','bgt','blt']:
   try:target=int(ins.op_str.lstrip('#'),0);note=describe(target)
   except:pass
  if 'pc' in ins.op_str and ins.mnemonic.startswith('ldr'):
   m=re.search(r'\[pc(?:, #(-?0x[0-9a-f]+|-?\d+))?\]',ins.op_str)
   if m:
    delta=int(m[1],0) if m[1] else 0;pc=(ins.address+(4 if addr&1 else 8))&~3;literal=pc+delta;b=get(literal,4)
    if len(b)==4:
     val=struct.unpack('<I',b)[0];note+=' literal=%#x'%val
     txt=get(val,90).split(b'\0')[0]
     if len(txt)>3 and all(32<=c<127 for c in txt):note+=' '+repr(txt.decode())
  print('%08x %-8s %-42s %s'%(ins.address,ins.mnemonic,ins.op_str,note))
