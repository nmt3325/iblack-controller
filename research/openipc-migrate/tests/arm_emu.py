#!/usr/bin/env python3
"""A small ARMv5TE interpreter (ARM state only) that runs a position-independent shared object in a test.

It knows what audio/libmajaudiofix.so and its PLT use: data processing with an immediate or an immediate-shifted
register operand, SMULL, LDR/STR(B) with an immediate or shifted-register offset, LDRH/STRH/LDRSB/LDRSH, LDM/STM,
B/BL and BX/BLX register. Anything else raises ArmFault, so a test notices when the code needs more; there is no
Thumb, no coprocessor and no system state.

load() maps an ELF ET_DYN at a base address, applies its relocations (R_ARM_JUMP_SLOT, R_ARM_GLOB_DAT,
R_ARM_RELATIVE; any other type, or one into a read-only segment, fails) and then makes PT_GNU_RELRO read-only, as
musl's loader does. Imported functions are Python callables at trap addresses outside the mapped memory.
An access outside the mapped memory, an unaligned word or halfword access, a write into read-only memory and an
instruction fetch outside an executable segment all raise ArmFault. Cpu.call() checks what AAPCS asks of the
callee: r4-r11 and sp restored, sp 8-byte aligned at every call out; a callee clobbers r1-r3, ip, lr and the flags.
"""
from __future__ import annotations

import struct

MASK = 0xFFFFFFFF
RETURN = 0xFFFF0000  # the return address Cpu.call() gives the code it runs


class ArmFault(AssertionError):
    pass


def signed(x: int) -> int:
    return x - (1 << 32) if x & 0x80000000 else x


class Memory:
    def __init__(self):
        self.regions = []  # [start, bytearray, writable, executable, name]

    def map(self, start: int, size: int, *, writable: bool, executable: bool = False, name: str = "",
            data: bytes = b"") -> list:
        for r in self.regions:
            if start < r[0] + len(r[1]) and r[0] < start + size:
                raise ArmFault(f"{name} overlaps {r[4]}")
        buf = bytearray(size)
        buf[:len(data)] = data
        region = [start, buf, writable, executable, name]
        self.regions.append(region)
        return region

    def region(self, addr: int, n: int = 1) -> list:
        for r in self.regions:
            if r[0] <= addr and addr + n <= r[0] + len(r[1]):
                return r
        raise ArmFault(f"access to unmapped memory at {addr:#x} ({n} bytes)")

    def read(self, addr: int, n: int) -> bytes:
        r = self.region(addr, n)
        return bytes(r[1][addr - r[0]:addr - r[0] + n])

    def write(self, addr: int, data: bytes, force: bool = False) -> None:
        r = self.region(addr, len(data))
        if not (r[2] or force):
            raise ArmFault(f"write into read-only {r[4]} at {addr:#x}")
        r[1][addr - r[0]:addr - r[0] + len(data)] = data

    def load(self, addr: int, size: int) -> int:
        if addr % size:
            raise ArmFault(f"unaligned {size}-byte read at {addr:#x}")
        return int.from_bytes(self.read(addr, size), "little")

    def store(self, addr: int, size: int, value: int) -> None:
        if addr % size:
            raise ArmFault(f"unaligned {size}-byte write at {addr:#x}")
        self.write(addr, (value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))

    def cstring(self, addr: int, limit: int = 256) -> str:
        out = bytearray()
        while len(out) < limit:
            c = self.read(addr + len(out), 1)
            if c == b"\0":
                return out.decode()
            out += c
        raise ArmFault(f"unterminated string at {addr:#x}")


def load(mem: Memory, data: bytes, base: int, imports: dict) -> dict:
    """Maps the ARM ET_DYN `data` at `base`; imports maps names to addresses. Returns {export: address}."""
    if data[:4] != b"\x7fELF" or data[4:6] != b"\1\1" or struct.unpack_from("<HH", data, 16) != (3, 40):
        raise ArmFault("not a 32-bit little-endian ARM shared object")
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    phdrs = [struct.unpack_from("<8I", data, phoff + i * phentsize) for i in range(phnum)]
    loads = [p for p in phdrs if p[0] == 1]
    relro = [(p[2], p[2] + p[5]) for p in phdrs if p[0] == 0x6474E552]
    late_ro = []
    for _t, off, vaddr, _pa, filesz, memsz, flags, _al in loads:
        content = data[off:off + filesz] + bytes(memsz - filesz)
        cuts = sorted({vaddr, vaddr + memsz} | {x for r in relro for x in r if vaddr < x < vaddr + memsz})
        for a, b in zip(cuts, cuts[1:]):
            region = mem.map(base + a, b - a, writable=bool(flags & 2), executable=bool(flags & 1),
                             name=f"segment {vaddr:#x}+{a - vaddr:#x}", data=content[a - vaddr:b - vaddr])
            if any(r0 <= a and b <= r1 for r0, r1 in relro):
                late_ro.append(region)

    def off(vaddr: int) -> int:
        for _t, p_off, p_vaddr, _pa, p_filesz, _ms, _fl, _al in loads:
            if p_vaddr <= vaddr < p_vaddr + p_filesz:
                return p_off + vaddr - p_vaddr
        raise ArmFault(f"{vaddr:#x} is not in the file")

    dyn = next(p for p in phdrs if p[0] == 2)
    tags = {}
    for i in range(dyn[4] // 8):
        tag, val = struct.unpack_from("<iI", data, dyn[1] + i * 8)
        if tag == 0:
            break
        tags.setdefault(tag, val)
    if 22 in tags or tags.get(30, 0) & 4:
        raise ArmFault("text relocations")
    strtab, symtab = off(tags[5]), off(tags[6])
    nsyms = struct.unpack_from("<II", data, off(tags[4]))[1]
    syms = []
    for i in range(nsyms):
        name, value, _size, _info, _other, shndx = struct.unpack_from("<IIIBBH", data, symtab + i * 16)
        syms.append((data[strtab + name:data.index(b"\0", strtab + name)].decode(), value, shndx))
    for table, size in ((17, 18), (23, 2)):  # DT_REL/DT_RELSZ, DT_JMPREL/DT_PLTRELSZ
        if table not in tags:
            continue
        for i in range(tags[size] // 8):
            r_offset, r_info = struct.unpack_from("<II", data, off(tags[table]) + i * 8)
            where = base + r_offset
            if not mem.region(where, 4)[2]:
                raise ArmFault(f"relocation into read-only memory at {r_offset:#x}")
            kind, name = r_info & 0xFF, syms[r_info >> 8][0]
            if kind == 23:  # R_ARM_RELATIVE
                value = (mem.load(where, 4) + base) & MASK
            elif kind in (21, 22):  # R_ARM_GLOB_DAT, R_ARM_JUMP_SLOT
                if name not in imports:
                    raise ArmFault(f"unresolved import {name}")
                value = imports[name]
            else:
                raise ArmFault(f"relocation type {kind} for {name or '-'}")
            mem.write(where, value.to_bytes(4, "little"), force=True)
    for region in late_ro:
        region[2] = False
    return {name: base + value for name, value, shndx in syms if name and shndx}


class Cpu:
    def __init__(self, mem: Memory, functions: dict):
        self.mem, self.functions = mem, functions  # functions: {trap address: callable(cpu) -> r0}
        self.r = [0] * 16
        self.pc = self.next = 0
        self.n = self.z = self.c = self.v = False
        self.min_sp = 0

    def reg(self, i: int) -> int:
        return (self.pc + 8) & MASK if i == 15 else self.r[i]

    def write_reg(self, i: int, value: int) -> None:
        value &= MASK
        if i != 15:
            self.r[i] = value
        elif value & 3:
            raise ArmFault(f"branch to {value:#x} (Thumb or unaligned)")
        else:
            self.next = value

    def call(self, entry: int, *args: int, sp: int, max_steps: int = 1_000_000) -> int:
        if len(args) > 4:
            raise ArmFault("arguments on the stack are not supported")
        self.r = [0x0BAD0000 | i << 8 for i in range(16)]
        self.r[:len(args)] = [a & MASK for a in args]
        self.r[13], self.r[14], self.pc = sp, RETURN, entry
        saved, self.min_sp = self.r[4:12], sp
        for _ in range(max_steps):
            if self.pc == RETURN:
                break
            fn = self.functions.get(self.pc)
            if fn is None:
                self.step()
                self.min_sp = min(self.min_sp, self.r[13])
                continue
            if self.r[13] % 8:
                raise ArmFault(f"sp {self.r[13]:#x} is not 8-byte aligned at a call out")
            result = fn(self)
            self.r[0] = (result or 0) & MASK
            self.r[1], self.r[2], self.r[3], self.r[12] = 0xC0FFEE01, 0xC0FFEE02, 0xC0FFEE03, 0xC0FFEE0C
            self.n, self.z, self.c, self.v = True, False, True, True
            self.pc, self.r[14] = self.r[14], 0xC0FFEE0E
        else:
            raise ArmFault("no return")
        if self.r[4:12] != saved:
            raise ArmFault("r4-r11 not restored")
        if self.r[13] != sp:
            raise ArmFault(f"sp {self.r[13]:#x} not restored to {sp:#x}")
        return self.r[0]

    def cond(self, c: int) -> bool:
        n, z, cf, v = self.n, self.z, self.c, self.v
        return (z, not z, cf, not cf, n, not n, v, not v, cf and not z, not cf or z, n == v, n != v,
                not z and n == v, z or n != v, True)[c]

    def step(self) -> None:
        if not self.mem.region(self.pc, 4)[3]:
            raise ArmFault(f"instruction fetch outside code at {self.pc:#x}")
        ins = self.mem.load(self.pc, 4)
        self.next = (self.pc + 4) & MASK
        if ins >> 28 == 15:
            raise ArmFault(f"unconditional-space instruction {ins:#010x} at {self.pc:#x}")
        if self.cond(ins >> 28):
            self.execute(ins)
        self.pc = self.next

    def execute(self, ins: int) -> None:
        if ins & 0x0FFFFFD0 == 0x012FFF10:  # BX, BLX (register)
            target = self.reg(ins & 15)
            if ins & 0x20:
                self.r[14] = (self.pc + 4) & MASK
            self.write_reg(15, target)
        elif ins & 0x0E000000 == 0x0A000000:  # B, BL
            offset = ins & 0xFFFFFF
            offset -= (offset & 0x800000) << 1
            if ins & 0x01000000:
                self.r[14] = (self.pc + 4) & MASK
            self.write_reg(15, self.pc + 8 + 4 * offset)
        elif ins & 0x0E000000 == 0x08000000:
            self.block(ins)
        elif ins & 0x0C000000 == 0x04000000:
            if ins & 0x02000010 == 0x02000010:
                raise ArmFault(f"undefined instruction {ins:#010x}")
            self.transfer(ins)
        elif ins & 0x0E000090 == 0x00000090 and ins & 0x60:
            self.halfword(ins)
        elif ins & 0x0F0000F0 == 0x00000090:
            self.multiply(ins)
        elif ins & 0x0C000000 == 0 and ins & 0x01900000 != 0x01000000 and ins & 0x02000090 != 0x90:
            self.data(ins)
        else:
            raise ArmFault(f"unsupported instruction {ins:#010x} at {self.pc:#x}")

    def shift(self, val: int, kind: int, amount: int) -> tuple:
        if kind == 0:  # LSL
            return ((val << amount) & MASK, bool(val >> (32 - amount) & 1)) if amount else (val, self.c)
        if kind == 1:  # LSR, 0 means 32
            amount = amount or 32
            return (val >> amount if amount < 32 else 0), bool(val >> (amount - 1) & 1)
        if kind == 2:  # ASR, 0 means 32
            amount = amount or 32
            s = signed(val)
            return (s >> amount) & MASK, bool(s >> (amount - 1) & 1)
        if amount == 0:  # RRX
            return (int(self.c) << 31) | (val >> 1), bool(val & 1)
        val = ((val >> amount) | (val << (32 - amount))) & MASK
        return val, bool(val >> 31)

    def data(self, ins: int) -> None:
        op, s, rn, rd = (ins >> 21) & 15, (ins >> 20) & 1, (ins >> 16) & 15, (ins >> 12) & 15
        if ins & 0x02000000:
            rot, imm = (ins >> 7) & 30, ins & 0xFF
            b = ((imm >> rot) | (imm << (32 - rot))) & MASK if rot else imm
            carry = bool(b >> 31) if rot else self.c
        elif ins & 0x10:
            raise ArmFault(f"register-specified shift {ins:#010x}")
        else:
            b, carry = self.shift(self.reg(ins & 15), (ins >> 5) & 3, (ins >> 7) & 31)
        a, overflow = self.reg(rn), self.v
        if op in (0, 1, 8, 9, 12, 13, 14, 15):  # AND EOR TST TEQ ORR MOV BIC MVN
            res = {0: a & b, 1: a ^ b, 8: a & b, 9: a ^ b, 12: a | b, 13: b, 14: a & ~b, 15: ~b}[op] & MASK
        else:  # SUB RSB ADD ADC SBC RSC CMP CMN
            c = int(self.c)
            x, y, cin = {2: (a, ~b, 1), 3: (b, ~a, 1), 4: (a, b, 0), 5: (a, b, c), 6: (a, ~b, c), 7: (b, ~a, c),
                         10: (a, ~b, 1), 11: (a, b, 0)}[op]
            x, y = x & MASK, y & MASK
            full = x + y + cin
            res, carry = full & MASK, full > MASK
            overflow = bool(~(x ^ y) & (x ^ res) & 0x80000000)
        if s:
            if rd == 15:
                raise ArmFault("flag-setting write to pc")
            self.n, self.z, self.c, self.v = bool(res >> 31), res == 0, carry, overflow
        if not 8 <= op <= 11:
            self.write_reg(rd, res)

    def multiply(self, ins: int) -> None:
        if ins & 0x0FF000F0 != 0x00C00090:  # SMULL without S
            raise ArmFault(f"unsupported multiply {ins:#010x}")
        hi, lo, rs, rm = (ins >> 16) & 15, (ins >> 12) & 15, (ins >> 8) & 15, ins & 15
        if len({hi, lo, rm}) < 3 or 15 in (hi, lo, rs, rm):
            raise ArmFault(f"UNPREDICTABLE SMULL {ins:#010x}")
        p = (signed(self.r[rm]) * signed(self.r[rs])) & ((1 << 64) - 1)
        self.r[lo], self.r[hi] = p & MASK, p >> 32

    def address(self, ins: int, offset: int) -> tuple:
        p, u, w, rn = (ins >> 24) & 1, (ins >> 23) & 1, (ins >> 21) & 1, (ins >> 16) & 15
        base = self.reg(rn)
        moved = (base + offset if u else base - offset) & MASK
        if not p and w:
            raise ArmFault(f"user-mode or UNPREDICTABLE transfer {ins:#010x}")
        if (not p or w) and (rn == 15 or rn == (ins >> 12) & 15):
            raise ArmFault(f"UNPREDICTABLE writeback {ins:#010x}")
        return (moved if p else base), (moved if not p or w else None), rn

    def transfer(self, ins: int) -> None:  # LDR, STR, LDRB, STRB
        if ins & 0x02000000:
            offset, _ = self.shift(self.reg(ins & 15), (ins >> 5) & 3, (ins >> 7) & 31)
        else:
            offset = ins & 0xFFF
        ea, wb, rn = self.address(ins, offset)
        size, rd = (1 if ins & 0x00400000 else 4), (ins >> 12) & 15
        if ins & 0x00100000:
            value = self.mem.load(ea, size)
        elif rd == 15:
            raise ArmFault("store of pc")
        else:
            self.mem.store(ea, size, self.r[rd])
        if wb is not None:
            self.r[rn] = wb
        if ins & 0x00100000:
            self.write_reg(rd, value)

    def halfword(self, ins: int) -> None:  # LDRH, STRH, LDRSB, LDRSH
        offset = ((ins >> 4) & 0xF0 | ins & 0xF) if ins & 0x00400000 else self.reg(ins & 15)
        ea, wb, rn = self.address(ins, offset)
        kind, rd = (ins >> 5) & 3, (ins >> 12) & 15
        if rd == 15:
            raise ArmFault("halfword transfer of pc")
        if ins & 0x00100000:
            value = self.mem.load(ea, 2 if kind != 2 else 1)
            if kind == 2:
                value = signed(value << 24) >> 24
            elif kind == 3:
                value = signed(value << 16) >> 16
        elif kind == 1:
            self.mem.store(ea, 2, self.r[rd])
        else:
            raise ArmFault(f"LDRD/STRD {ins:#010x}")
        if wb is not None:
            self.r[rn] = wb
        if ins & 0x00100000:
            self.write_reg(rd, value)

    def block(self, ins: int) -> None:  # LDM, STM
        p, u, s, w, load = (ins >> 24) & 1, (ins >> 23) & 1, (ins >> 22) & 1, (ins >> 21) & 1, (ins >> 20) & 1
        rn, regs = (ins >> 16) & 15, [i for i in range(16) if ins >> i & 1]
        if s or rn == 15 or not regs or (w and rn in regs) or (not load and 15 in regs):
            raise ArmFault(f"unsupported LDM/STM {ins:#010x}")
        base, n = self.r[rn], len(regs)
        first = base + (4 if p else 0) if u else base - 4 * n + (0 if p else 4)
        values = []
        for k, i in enumerate(regs):
            if load:
                values.append((i, self.mem.load((first + 4 * k) & MASK, 4)))
            else:
                self.mem.store((first + 4 * k) & MASK, 4, self.r[i])
        if w:
            self.r[rn] = (base + 4 * n if u else base - 4 * n) & MASK
        for i, value in values:
            self.write_reg(i, value)
