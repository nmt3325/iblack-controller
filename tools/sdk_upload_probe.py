#!/usr/bin/env python3
"""Offline ARM probes for one known iBlack firmware, never a camera client/server.

Only synthetic RFC5737 endpoints and token-shaped fixtures are used. libc memory,
logging and clock functions are modeled; synthetic recvfrom is opt-in per probe.
Unmodeled PLT imports and ARM interrupts abort. No host sockets are opened.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
from ipaddress import IPv4Address
import json
from pathlib import Path
import re
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRMWARE = ROOT / 'firmware/iblack-8m/extracted/camera_modules/cmsapp/cmsMain.out'
FIRMWARE_SHA256 = '4419e7f86607eb83337de021563db2bb7392040059b29b715090e2ba54572e56'
G = 0x4889C8
PACKET, OBJECT, STACK, STOP = 0x60000000, 0x61000000, 0x71000000, 0x70000000
TRACKER_IP, TRACKER_PORT = int(IPv4Address('192.0.2.1')), 3368
ENDPOINT = struct.pack('<IH', int(IPv4Address('198.51.100.7')), 1935)


def synthetic_reply(*, command=0x01010818, status=0, corrupt_crc=False):
    """76-byte synthetic fixture, not a verified live cloud response template."""
    packet = bytearray(76)
    struct.pack_into('<I', packet, 4, command)
    packet[8] = status
    packet[12:18] = ENDPOINT
    checksum = zlib.crc32(packet[4:])
    struct.pack_into('<I', packet, 0, checksum ^ (1 if corrupt_crc else 0))
    return bytes(packet)


class FirmwareProbe:
    """Execute selected unmodified ARM instructions in an isolated emulator."""
    def __init__(self, firmware=DEFAULT_FIRMWARE):
        if not __debug__:
            raise RuntimeError("Run without Python -O; probes require assertions")
        # Optional dependencies are deliberately not imported by pure fixture tests.
        from elftools.elf.elffile import ELFFile
        import unicorn
        from unicorn import arm_const
        self.ucmod, self.arm = unicorn, arm_const
        raw = Path(firmware).read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if self.sha256 != FIRMWARE_SHA256:
            raise ValueError('Firmware hash mismatch: these addresses apply to one image only')
        elf = ELFFile(BytesIO(raw))
        if elf.elfclass != 32 or not elf.little_endian or elf['e_machine'] != 'EM_ARM':
            raise ValueError('Expected ARM32 little-endian ELF')
        self.loads = [(int(s['p_vaddr']), int(s['p_memsz']), s.data())
                      for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
        plt = elf.get_section_by_name('.plt')
        self.plt = (int(plt['sh_addr']), int(plt['sh_addr'] + plt['sh_size']))
        self.registers = [getattr(arm_const, 'UC_ARM_REG_R' + str(n)) for n in range(4)]

    def machine(self):
        m = self.ucmod
        u = m.Uc(m.UC_ARCH_ARM, m.UC_MODE_ARM | m.UC_MODE_LITTLE_ENDIAN)
        # Mapping the bounded PT_LOAD span avoids thousands of per-page calls.
        lo = min(a for a, _, _ in self.loads) & ~4095
        hi = (max(a + n for a, n, _ in self.loads) + 4095) & ~4095
        if hi - lo > 32 * 1024 * 1024:
            raise ValueError('Unexpected ELF mapping span')
        u.mem_map(lo, hi - lo)
        for address, _, data in self.loads:
            u.mem_write(address, data)
        for address, size in [(PACKET, 0x10000), (OBJECT, 0x10000),
                              (STACK, 0x40000), (STOP, 4096)]:
            u.mem_map(address, size)
        return u

    @staticmethod
    def word(u, address, value):
        u.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    @staticmethod
    def string(u, address):
        # All fixtures and the selected log strings fit in this bounded read.
        return bytes(u.mem_read(address, 4096)).split(bytes([0]), 1)[0]

    def execute(self, u, entry, args, *, stack_args=(), extra_hooks=None):
        arm, m = self.arm, self.ucmod
        sp = STACK + 0x3F000
        for index, value in enumerate(stack_args):
            self.word(u, sp + 4 * index, value)
        for register, value in zip(self.registers, args):
            u.reg_write(register, value)
        u.reg_write(arm.UC_ARM_REG_SP, sp)
        u.reg_write(arm.UC_ARM_REG_LR, STOP)
        used, visited, returned = set(), set(), [False]
        extra_hooks = extra_hooks or {}

        def ret(value=0):
            u.reg_write(arm.UC_ARM_REG_R0, value & 0xFFFFFFFF)
            u.reg_write(arm.UC_ARM_REG_PC, u.reg_read(arm.UC_ARM_REG_LR))

        def hook(uc, pc, size, _):
            if pc == STOP:
                returned[0] = True
                uc.emu_stop()
                return
            if pc in (0xFF248, 0xF720C, 0xFDCA8):
                visited.add(hex(pc))
            if pc in extra_hooks:
                used.add(hex(pc))
                extra_hooks[pc](uc, ret)
                return
            if pc in (0x10D78, 0x114C8, 0x11924, 0x113F0, 0x11030,
                      0x115DC, 0x1184C, 0x118A0, 0x101FFC, 0xBD194, 0xFE3AC):
                used.add(hex(pc))
                a, b, c = [uc.reg_read(r) for r in self.registers[:3]]
                if pc == 0x10D78:  # memcpy
                    if c:
                        uc.mem_write(a, bytes(uc.mem_read(b, c)))
                    ret(a)
                elif pc == 0x114C8:  # memset
                    if c:
                        uc.mem_write(a, bytes([b & 255]) * c)
                    ret(a)
                elif pc == 0x11924:  # strcpy, logs only in these probes
                    uc.mem_write(a, self.string(uc, b) + bytes([0]))
                    ret(a)
                elif pc == 0x113F0:  # strncpy
                    text = self.string(uc, b)[:c]
                    uc.mem_write(a, text + bytes(c - len(text)))
                    ret(a)
                elif pc == 0x11030:  # strtol, only controlled base-16 fixtures
                    if c != 16:
                        raise RuntimeError('Unexpected strtol base')
                    text = self.string(uc, a).decode('ascii')
                    match = re.match(r'[0-9a-fA-F]+', text)
                    if b:
                        self.word(uc, b, a + (len(match[0]) if match else 0))
                    ret(int(match[0], 16) if match else 0)
                elif pc == 0xFE3AC:  # deterministic clock, not the state logic
                    uc.reg_write(arm.UC_ARM_REG_R1, 0)
                    ret(123456)
                else:  # logging/formatting whose text is not used for decisions
                    ret(0)
            elif self.plt[0] <= pc < self.plt[1]:
                raise RuntimeError('Blocked unmodeled PLT import at ' + hex(pc))

        def interrupt(*_):
            raise RuntimeError('Blocked ARM interrupt/syscall')

        u.hook_add(m.UC_HOOK_CODE, hook)
        u.hook_add(m.UC_HOOK_INTR, interrupt)
        u.emu_start(entry, STOP + 4, timeout=2000000, count=500000)
        if not returned[0]:
            raise RuntimeError('Function did not return within the instruction/time budget')
        return {'entry': hex(entry), 'returned': True,
                'return_r0': int(u.reg_read(arm.UC_ARM_REG_R0)),
                'modeled_calls': sorted(used), 'visited': sorted(visited)}

    def initialize_discovery(self, u, *, state=2, mode=2, transport=0):
        self.word(u, G + 0x860, TRACKER_IP)
        u.mem_write(G + 0x864, struct.pack('<H', TRACKER_PORT))
        u.mem_write(G + 0x6B1, bytes([state]))
        u.mem_write(G + 0x6B4, bytes([mode]))
        u.mem_write(G + 0x82C, bytes(6))
        self.word(u, OBJECT + 0xC, transport)

    def reply(self, label, *, source_ip=TRACKER_IP, source_port=TRACKER_PORT,
              status=0, size=76, state=2, mode=2, command=0x01010818,
              transport=0, receiver=False, corrupt_crc=False, expect=True):
        u = self.machine()
        self.initialize_discovery(u, state=state, mode=mode, transport=transport)
        packet = synthetic_reply(command=command, status=status, corrupt_crc=corrupt_crc)
        u.mem_write(PACKET, packet)
        hooks = {}
        if receiver:
            # Run the real recvfrom wrapper and dispatcher. Only host I/O is modeled.
            self.word(u, OBJECT, 42)
            u.mem_write(G + 0x49E, struct.pack('<H', 0x580))
            self.word(u, G + 0x4E4, 0)
            self.word(u, G + 0x6A8, 0)
            received = [False]

            def recvfrom(uc, ret):
                if received[0]:
                    raise RuntimeError('Receiver unexpectedly requested a second packet')
                received[0] = True
                address = uc.reg_read(self.registers[1])
                sp = uc.reg_read(self.arm.UC_ARM_REG_SP)
                sockaddr = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
                addrlen = struct.unpack('<I', bytes(uc.mem_read(sp + 4, 4)))[0]
                sa = struct.pack('<H', 2) + struct.pack('>H', source_port)
                sa += int(source_ip).to_bytes(4, 'big') + bytes(8)
                uc.mem_write(address, packet[:size])
                uc.mem_write(sockaddr, sa)
                self.word(uc, addrlen, 16)
                self.word(uc, G + 0x6A8, 1)  # clean exit after this one loop iteration
                ret(size)

            hooks[0x115AC] = recvfrom
            run = self.execute(u, 0xF911C, [OBJECT], extra_hooks=hooks)
        else:
            run = self.execute(u, 0xFF248, [OBJECT, PACKET, size, source_ip],
                               stack_args=[source_port])
        after = bytes(u.mem_read(G + 0x6B1, 1))[0]
        copied = bytes(u.mem_read(G + 0x82C, 6)) == ENDPOINT
        assert copied == expect, (label, after, copied, run)
        assert after == (4 if expect else state), (label, after)
        return dict(case=label, group='receiver' if receiver else 'reply-parser',
                    state_before=state, state_after=after, mode=mode,
                    endpoint_copied=copied, crc_corrupted=corrupt_crc, passed=True, **run)

    def crc(self, label, data):
        u = self.machine()
        if data:
            u.mem_write(PACKET, data)
        run = self.execute(u, 0xEE238, [PACKET, len(data)])
        assert run['return_r0'] == zlib.crc32(data), (label, run)
        return dict(case=label, group='crc32', bytes=len(data), passed=True, **run)

    def hex_token(self, label, text):
        u = self.machine()
        u.mem_write(PACKET, text.encode('ascii') + bytes([0]))
        run = self.execute(u, 0xF5098, [OBJECT, PACKET, len(text)])
        count = len(text) // 2
        expected = bytes.fromhex(text[:2 * count])
        assert bytes(u.mem_read(OBJECT, count)) == expected, (label, run)
        return dict(case=label, group='hex-token', input_length=len(text),
                    decoded_bytes=count, passed=True, **run)

    def endpoint_copy(self):
        u = self.machine()
        self.initialize_discovery(u, state=1)

        def asynchronous_completion(uc, ret):
            # Model the separate receiver thread's completion; parser is tested above.
            assert bytes(uc.mem_read(G + 0x6B1, 1)) == bytes([2])
            uc.mem_write(G + 0x82C, ENDPOINT)
            uc.mem_write(G + 0x6B1, bytes([4]))
            ret(0)

        run = self.execute(u, 0xF720C, [2, OBJECT],
                           extra_hooks={0xFE4CC: asynchronous_completion})
        assert bytes(u.mem_read(OBJECT, 6)) == ENDPOINT, run
        assert run['return_r0'] == 4, run
        return dict(case='selected-endpoint-copy', group='endpoint-getter', passed=True,
                    completion='Asynchronous receiver completion modeled at sleep boundary', **run)

    def run_all(self):
        cases = [('valid-mode' + str(mode), {'mode': mode}) for mode in range(2, 7)]
        cases += [('wrong-source-ip', {'source_ip': TRACKER_IP + 1, 'expect': False}),
                  ('wrong-source-port', {'source_port': 3399, 'expect': False}),
                  ('status-not-success', {'status': 1, 'expect': False}),
                  ('wrong-length', {'size': 75, 'expect': False}),
                  ('wrong-state', {'state': 1, 'expect': False}),
                  ('wrong-command', {'command': 0x01010819, 'expect': False}),
                  ('wrong-transport', {'transport': 1, 'expect': False})]
        results = [self.reply(label, **opts) for label, opts in cases]
        for label, opts in [('receiver-valid', {}),
                            ('receiver-bad-crc', {'corrupt_crc': True}),
                            ('receiver-wrong-source', {'source_ip': TRACKER_IP + 1, 'expect': False}),
                            ('receiver-short', {'size': 75, 'expect': False})]:
            results.append(self.reply(label, receiver=True, **opts))
        for label, data in [('crc-empty', b''), ('crc-check-vector', b'123456789'),
                            ('crc-synthetic-reply', synthetic_reply()[4:])]:
            results.append(self.crc(label, data))
        for label, text in [('hex-lower', '0123456789abcdef' * 2),
                            ('hex-upper', '0123456789ABCDEF' * 2),
                            ('hex-odd-truncates', '0123456789abcdef0')]:
            results.append(self.hex_token(label, text))
        results.append(self.endpoint_copy())
        return {'recorded_at': datetime.now(timezone.utc).isoformat(),
                'firmware_sha256': self.sha256,
                'method': 'Unmodified ARM instructions executed offline with Unicorn',
                'network_allowed': False, 'arm_syscalls_allowed': False,
                'fixtures': 'Synthetic RFC5737 IPs and non-secret token-shaped hex',
                'scope': 'Parser, one receive loop, CRC32, hex decoding and endpoint getter only',
                'limitations': ['No live camera, cloud bootstrap, SDK threads or RTMP session',
                                'State and selected tracker are initialized as test preconditions',
                                'Logging, libc memory/string operations and clock are modeled',
                                'Endpoint getter probe models asynchronous receiver completion',
                                'CRC non-validation finding applies only to the tested 76-byte receive path'],
                'passed': all(r['passed'] for r in results), 'tests': len(results),
                'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, default=DEFAULT_FIRMWARE)
    parser.add_argument('--output', type=Path, help='Write a synthetic-only JSON report')
    args = parser.parse_args()
    report = FirmwareProbe(args.firmware).run_all()
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print('PASS offline firmware probes:', report['tests'])
    print('No camera connections, network access, firmware writes or service changes')


if __name__ == '__main__':
    main()
