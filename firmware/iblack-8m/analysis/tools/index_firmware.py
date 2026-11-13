"""Read-only ARM ELF literal/call index; never executes target programs."""
import argparse, bisect, hashlib, io, json, pathlib, re, struct
from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_LITTLE_ENDIAN

def literal_address(word, pc):
    if word & 0x0f7f0000 != 0x051f0000:
        return None
    return pc + 8 + ((word & 0xfff) if word & 0x00800000 else -(word & 0xfff))

def branch_target(word, pc):
    if word & 0x0f000000 != 0x0b000000:
        return None
    delta = word & 0xffffff
    if delta & 0x800000:
        delta -= 0x1000000
    return (pc + 8 + (delta << 2)) & 0xffffffff

def safe(text):
    text = re.sub(r'(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', '<MAC>', text)
    if re.search(r'(?i)(password|passwd|authorization|pwd\s*[=:]|psk\s*[=:]|root:)', text):
        return '[credential-bearing string omitted]'
    return text

class Image:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.raw = self.path.read_bytes()
        self.elf = ELFFile(io.BytesIO(self.raw))
        self.sections = [s for s in self.elf.iter_sections() if s['sh_flags'] & 2 and s['sh_type'] != 'SHT_NOBITS']
        self.code = [s for s in self.sections if s['sh_flags'] & 4]
        self.section_data = {s.name:s.data() for s in self.sections}
        self.strings = []
        for section in self.sections:
            if section['sh_flags'] & 4:
                continue
            for m in re.finditer(rb'[\x20-\x7e]{4,}', section.data()):
                self.strings.append({'address': section['sh_addr'] + m.start(), 'text': m.group().decode(), 'section': section.name})
        self.strings.sort(key=lambda x: x['address'])
        self.starts = [x['address'] for x in self.strings]
        self.symbols = []
        for section in self.elf.iter_sections():
            if section['sh_type'] not in ('SHT_DYNSYM', 'SHT_SYMTAB'):
                continue
            for sym in section.iter_symbols():
                if sym['st_info']['type'] == 'STT_FUNC' and sym.name:
                    self.symbols.append({'name': sym.name, 'address': sym['st_value'], 'size': sym['st_size'], 'defined': sym['st_shndx'] != 'SHN_UNDEF'})
        self.plt, self.plt_layout_verified = {}, False
        plt = self.elf.get_section_by_name('.plt')
        reloc = self.elf.get_section_by_name('.rel.plt')
        if plt and reloc and plt['sh_size'] == 20 + 12 * reloc.num_relocations():
            symtab = self.elf.get_section(reloc['sh_link'])
            self.plt = {plt['sh_addr'] + 20 + 12*i: symtab.get_symbol(rel['r_info_sym']).name for i, rel in enumerate(reloc.iter_relocations())}
            self.plt_layout_verified = True
        self.references, self.calls, self.prologues = {}, {}, []
        for section in self.code:
            data, start = section.data(), section['sh_addr']
            for i in range(0, len(data)-3, 4):
                word = struct.unpack_from('<I', data, i)[0]
                address = start + i
                literal = literal_address(word, address)
                if literal is not None:
                    value = self.word(literal)
                    target = self.string_at(value) if value is not None else None
                    if target:
                        self.references.setdefault(target['address'], []).append({'instruction': address, 'literal_pool': literal, 'pointer': value, 'register': (word>>12)&15})
                target = branch_target(word, address)
                if target is not None:
                    self.calls.setdefault(target, []).append(address)
                if word & 0xffff0000 == 0xe92d0000 and word & 0x4000:
                    self.prologues.append(address)
        self.prologues.sort()
        self.cs = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)

    def section_at(self, address):
        for s in self.sections:
            if s['sh_addr'] <= address < s['sh_addr'] + s['sh_size']:
                return s
        return None

    def read(self, address, length):
        section = self.section_at(address)
        if not section:
            return b''
        off = address-section['sh_addr']
        return self.section_data[section.name][off:off+length]

    def word(self, address):
        b = self.read(address, 4)
        return struct.unpack('<I', b)[0] if len(b) == 4 else None

    def string_at(self, address):
        i = bisect.bisect_right(self.starts, address)-1
        if i >= 0:
            row = self.strings[i]
            if address < row['address'] + len(row['text']):
                return row
        return None

    def function(self, address):
        for sym in self.symbols:
            if sym['defined'] and sym['size'] and sym['address'] <= address < sym['address']+sym['size']:
                return {'start': sym['address'], 'name': sym['name'], 'confidence': 'dynamic-symbol'}
        i = bisect.bisect_right(self.prologues, address)-1
        return {'start': self.prologues[i] if i >= 0 else address, 'name': None, 'confidence': 'nearest-push-lr-heuristic'}

    def find(self, pattern, limit=30):
        rx = re.compile(pattern, re.I)
        results = []
        for row in self.strings:
            if rx.search(row['text']):
                refs = [dict(r, exact_text=row['text'][r['pointer']-row['address']:]) for r in self.references.get(row['address'], []) if rx.search(row['text'][r['pointer']-row['address']:])]
                results.append({'address': hex(row['address']), 'text': safe(row['text']), 'reference_count': len(refs), 'references': [{'instruction': hex(r['instruction']), 'literal_pool': hex(r['literal_pool']), 'pointer': hex(r['pointer']), 'exact_text': safe(r['exact_text']), 'function': self.function(r['instruction'])} for r in refs[:8]]})
        return {'match_count': len(results), 'matches': results[:limit]}

    def disasm(self, address, length=256):
        rows=[]
        for ins in self.cs.disasm(self.read(address,length),address):
            word=int.from_bytes(ins.bytes,'little');annotation=[]
            lit=literal_address(word,ins.address)
            if lit is not None:
                value=self.word(lit)
                if value is not None:
                    s=self.string_at(value)
                    if self.path.name == 'cmsMain.out' and 0x384c84 <= value < 0x384ca0:
                        annotation.append('literal='+hex(value)+' [key material omitted]')
                        s=None
                    annotation.append('literal='+hex(value)+((' '+safe(s['text'][value-s['address']:])) if s and s['section'] in ('.rodata','.data') else ''))
            target=branch_target(word,ins.address)
            if target is not None:
                name=self.plt.get(target)
                if not name:
                    name=next((s['name'] for s in self.symbols if s['defined'] and s['address']==target),None)
                if name:annotation.append('call '+name)
            rows.append(f'{ins.address:08x}  {ins.mnemonic:8} {ins.op_str:32}'+(' ; '+' | '.join(annotation) if annotation else ''))
        return rows

    def summary(self):
        return {'path':str(self.path),'bytes':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest(),'entry':hex(self.elf['e_entry']),'code_sections':[{'name':s.name,'address':hex(s['sh_addr']),'bytes':s['sh_size']} for s in self.code],'strings':len(self.strings),'symbols':len(self.symbols),'literal_string_references':sum(map(len,self.references.values())),'plt_layout_verified':self.plt_layout_verified,'plt_symbols':len(self.plt),'prologue_candidates':len(self.prologues)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('binary');p.add_argument('--find');p.add_argument('--limit',type=int,default=30);p.add_argument('--disasm',type=lambda x:int(x,0));p.add_argument('--length',type=int,default=256);p.add_argument('--calls');p.add_argument('--summary',action='store_true');args=p.parse_args();im=Image(args.binary)
    if args.disasm is not None:print('\n'.join(im.disasm(args.disasm,args.length)))
    elif args.find:print(json.dumps(im.find(args.find,args.limit),ensure_ascii=False,indent=2))
    elif args.calls:
        result=[{'target':hex(a),'name':n,'calls':[{'address':hex(c),'function':im.function(c)} for c in im.calls.get(a,[])]} for a,n in im.plt.items() if re.search(args.calls,n,re.I)];print(json.dumps(result,ensure_ascii=False,indent=2))
    else:print(json.dumps(im.summary(),ensure_ascii=False,indent=2))
