#!/usr/bin/env python3
"""Execute the ACTUAL SysUpdate flash writer with fail-closed RAM-only I/O.
No sockets, shell commands, real /dev files, or firmware patches are used.
Firmware CRC, RC6, status, erase-loop and write-loop instructions run unchanged.
Direct entry to sub_9498 uses the arguments observed at opcode 0x94 dispatch.
"""
import hashlib
import json
from pathlib import Path
import struct
from pack_update import Packer, crc32, header
from firmware_math import FirmwareMath, DEFAULT_FIRMWARE, FIRMWARE_SHA256


class VirtualFlash(FirmwareMath):
    def __init__(self, initial, erase_shift=16, now=1700000000):
        self.flash = bytearray(initial)
        self.erase_shift = erase_shift
        self.position = 0
        self.io = []
        self.rand_state = 1
        super().__init__(DEFAULT_FIRMWARE, now=now)

    def _ret(self, value):
        r = self.reg
        self.uc.reg_write(r.UC_ARM_REG_R0, value & 0xffffffff)
        self.uc.reg_write(r.UC_ARM_REG_PC, self.uc.reg_read(r.UC_ARM_REG_LR))

    def _range(self, pos, size):
        if not 0 <= pos <= pos + size <= len(self.flash):
            raise RuntimeError('Virtual MTD access out of range')

    def _hook(self, uc, address, size, user):
        extra = (0x89b0, 0x89e0, 0x8a04, 0x8a34, 0x8a7c,
                 0x8ac4, 0x8adc, 0x8b30, 0x8b60, 0x8bc0)
        if address not in extra:
            return super()._hook(uc, address, size, user)
        try:
            r = self.reg
            a, b, c = [uc.reg_read(x) for x in (r.UC_ARM_REG_R0, r.UC_ARM_REG_R1, r.UC_ARM_REG_R2)]
            self.calls.append(address)
            if address == 0x8a04:
                raise RuntimeError('All emulated shell commands are blocked')
            if address in (0x89b0, 0x8a34, 0x8a7c, 0x8adc, 0x8b60, 0x8bc0) and a != 33:
                raise RuntimeError('Only virtual MTD fd 33 is permitted')
            value = 0
            if address == 0x89b0:
                if b == 0x80204d01:
                    uc.mem_write(c, struct.pack('<B3xIIIIIQ', 3, 0x400, len(self.flash),
                                                1 << self.erase_shift, 1, 0, 0))
                    self.io.append(['MEMGETINFO'])
                elif b in (0x40084d02, 0x40084d05, 0x40084d06):
                    pos, length = struct.unpack('<II', uc.mem_read(c, 8))
                    self._range(pos, length)
                    if pos % (1 << self.erase_shift) or length != 1 << self.erase_shift:
                        raise RuntimeError('Unexpected virtual erase/lock alignment')
                    name = {0x40084d02: 'MEMERASE', 0x40084d05: 'MEMLOCK',
                            0x40084d06: 'MEMUNLOCK'}[b]
                    self.io.append([name, pos, length])
                    if name == 'MEMERASE':
                        self.flash[pos:pos+length] = b'\xff' * length
                else:
                    raise RuntimeError('Unapproved ioctl 0x%x' % b)
            elif address == 0x8a34:
                offset = b if b < 0x80000000 else b - 0x100000000
                pos = offset if c == 0 else self.position + offset if c == 1 else len(self.flash) + offset if c == 2 else -1
                self._range(pos, 0)
                self.position = value = pos
                self.io.append(['lseek', pos])
            elif address == 0x8adc:
                self._range(self.position, c)
                uc.mem_write(b, bytes(self.flash[self.position:self.position+c]))
                self.io.append(['read', self.position, c])
                self.position += c
                value = c
            elif address == 0x8a7c:
                self._range(self.position, c)
                data = bytes(uc.mem_read(b, c))
                old = self.flash[self.position:self.position+c]
                if any((x & y) != y for x, y in zip(old, data)):
                    raise RuntimeError('NOR write attempted without sufficient erase')
                self.io.append(['write', self.position, c, hashlib.sha256(data).hexdigest()])
                self.flash[self.position:self.position+c] = data
                self.position += c
                value = c
            elif address == 0x8ac4:
                self.rand_state = (1103515245 * self.rand_state + 12345) & 0x7fffffff
                value = self.rand_state
            elif address == 0x8b30:
                self.rand_state = a
            elif address in (0x8b60, 0x8bc0):
                self.io.append(['fsync' if address == 0x8b60 else 'close'])
            # usleep is a no-op, never a host sleep.
            self._ret(value)
        except Exception as exc:
            self.failure = exc
            uc.emu_stop()

    def run_writer(self, head, body, offset=0, kind=2):
        if len(head) != 28:
            raise ValueError('Expected 28-byte header')
        r = self.reg
        hp = self.alloc(28, head)
        self.incoming = bytearray(body + header(0xfe, head[8:24], 0))
        self.outgoing, self.commands, self.calls = [], [], []
        self.failure = None
        self.uc.reg_write(r.UC_ARM_REG_CPSR, 0x10)
        sp = 0x7f01f000
        self.uc.reg_write(r.UC_ARM_REG_SP, sp)
        self.uc.reg_write(r.UC_ARM_REG_LR, self.sentinel)
        # offset, unlock flag, post-lock flag, erase shift, storage kind.
        self.uc.mem_write(sp, struct.pack('<5I', offset, 0, 0, self.erase_shift, kind))
        for reg, val in zip((r.UC_ARM_REG_R0, r.UC_ARM_REG_R1, r.UC_ARM_REG_R2, r.UC_ARM_REG_R3),
                            (7, hp, 33, len(self.flash) // 1024)):
            self.uc.reg_write(reg, val)
        self.uc.emu_start(0x9498, self.sentinel, timeout=20000000, count=30000000)
        if self.failure:
            raise RuntimeError('pc=0x%x: %s' % (self.uc.reg_read(r.UC_ARM_REG_PC), self.failure))
        if self.uc.reg_read(r.UC_ARM_REG_PC) != self.sentinel:
            raise RuntimeError('Writer did not return within execution bound')
        return {
            'return': self.uc.reg_read(r.UC_ARM_REG_R0),
            'status': [[x[0], x[1]] for x in self.outgoing],
            'reply_crc_valid': all(crc32(x[:24]) == struct.unpack('>I', x[24:28])[0] for x in self.outgoing),
            'remaining_input': len(self.incoming),
            'io': self.io,
            'commands': self.commands,
        }


def main():
    p = Packer()
    initial = bytes([0xa5]) * 0x10000
    tests = {}
    for size in (1, 12, 13, 16, 63, 64, 65, 4093):
        image = bytes((i * 37 + 11) % 256 for i in range(size))
        h, b = p.pack(0x94, image)
        assert p.verify(h, b, image)['all_ok']
        vm = VirtualFlash(initial)
        result = vm.run_writer(h, b)
        stored = struct.pack('<II', len(image) + 8, crc32(image)) + image
        ok = (result['return'] == 0 and bytes(vm.flash[:len(stored)]) == stored
              and bytes(vm.flash[len(stored):]) == b'\xff' * (len(initial)-len(stored))
              and result['reply_crc_valid'] and result['remaining_input'] == 0
              and result['status'][-1] == [0xfe, 0] and not result['commands'])
        result.update(passed=ok, input_image_sha256=hashlib.sha256(image).hexdigest(),
                      stored_image_sha256=hashlib.sha256(vm.flash[8:8+size]).hexdigest())
        tests['custom_image_%d' % size] = result
        assert ok, json.dumps(result)
    image = b'custom firmware test image' * 2
    h, b = p.pack(0x94, image)
    bad_crc = bytearray(b); bad_crc[31] ^= 1
    expired = bytearray(h[8:24]); old = (p.m.now - 40*86400).to_bytes(4, 'big')
    expired[6], expired[8], expired[7], expired[3] = old
    expired_h, expired_b = p.pack(0x94, image, nonce=bytes(expired), maintenance=False)
    cases = {
        'bad_cipher_crc': (h, bytes(bad_crc)),
        'bad_plain_crc': p.pack(0x94, image, bad_plain_crc=True),
        'bad_magic': p.pack(0x94, image, magic=0x01000002),
        'bad_image_type': p.pack(0x94, image, image_type=4),
        'expired_time': (expired_h, expired_b),
        'oversized_request': (header(0x94, h[8:24], len(initial)+0x34), b),
    }
    for name, (head, body) in cases.items():
        vm = VirtualFlash(initial)
        result = vm.run_writer(head, body)
        ok = (result['return'] == 0xffffffff and not any(x[0] in ('write', 'MEMERASE') for x in result['io'])
              and bytes(vm.flash) == initial and result['reply_crc_valid']
              and result['status'][0][0] == 0xff and not result['commands'])
        result['passed'] = ok
        tests[name] = result
        assert ok, name + ': ' + json.dumps(result)
    # A stale nonce is accepted by the real writer with its existing maintenance sentinel.
    mh, mb = p.pack(0x94, image, nonce=bytes(expired), maintenance=True)
    vm = VirtualFlash(initial)
    result = vm.run_writer(mh, mb)
    result['passed'] = result['return'] == 0 and vm.flash[8:8+len(image)] == image
    tests['maintenance_time'] = result
    assert result['passed']
    report = {
        'firmware_sha256': FIRMWARE_SHA256,
        'entrypoint': '0x9498; arguments from opcode 0x94 dispatcher at 0xd7ec',
        'scope': 'Actual writer/CRC/RC6/status/MTD helper instructions; OS I/O is simulated',
        'not_tested': ['live listener reachability', 'real flash write', 'reboot', 'custom firmware boot',
                       '0x91 boot writer', '0x96 appfs writer'],
        'network_calls': 0, 'host_commands_executed': 0, 'real_device_writes': 0,
        'virtual_partition_bytes': len(initial), 'erase_bytes': 1 << 16,
        'tests': tests, 'passed': all(x['passed'] for x in tests.values()),
    }
    Path('offline_write_report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'test_count': len(tests),
                      'positive_cases': 9, 'negative_cases': 6,
                      'network_calls': 0, 'real_device_writes': 0,
                      'sample': tests['custom_image_64'],
                      'report': 'offline_write_report.json'}, indent=2))


if __name__ == '__main__':
    main()
