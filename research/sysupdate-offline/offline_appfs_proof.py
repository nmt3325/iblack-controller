#!/usr/bin/env python3
"""RAM-only proof through real header dispatcher and opcode 0x96 app/bak writer.
No accept/socket calls: a captured connection is represented by a byte queue.
Stop before the firmware's reboot helper; never execute host/device I/O.
"""
import collections, hashlib, json, struct
from pathlib import Path
from unicorn import UC_HOOK_CODE
from offline_write_proof import VirtualFlash
from pack_update import Packer, header, crc32
from firmware_math import FIRMWARE_SHA256

class AppUpdateVM(VirtualFlash):
    def __init__(self):
        self.parts = {33: bytearray([0xa5])*0x280000, 34: bytearray([0x5a])*0x280000}
        self.positions = {33: 0, 34: 0}
        self.opens = []
        self.stop_reason = None
        super().__init__(self.parts[33])
        self.parts[33] = self.flash
        for addr in (0xcb2c, 0xcb74, 0xcce8, 0xcd5c, 0x8c74):
            self.uc.hook_add(UC_HOOK_CODE, self._stop_at, begin=addr, end=addr)

    def _stop_at(self, uc, address, size, user):
        self.stop_reason = {0xcb2c: 'next_client', 0xcb74: 'header_crc_rejected', 0xcce8: 'client_complete',
                            0xcd5c: 'dispatch_error', 0x8c74: 'reboot_requested'}[address]
        uc.emu_stop()

    def _hook(self, uc, address, size, user):
        r = self.reg
        fd = uc.reg_read(r.UC_ARM_REG_R0)
        if address == 0x8bc0 and fd in (7,8):
            self.io.append([fd,'close_socket'])
            self._ret(0)
            return
        if address == 0x8bb4:
            try:
                path = self._cstr(fd)
                out = {b'/dev/mtd2': 33, b'/dev/mtd5': 34}[path]
                self.opens.append(path.decode())
                self._ret(out)
            except Exception as exc:
                self.failure = exc; uc.emu_stop()
            return
        if address in (0x89b0,0x8a34,0x8a7c,0x8adc,0x8b60,0x8bc0) and fd in self.parts:
            self.flash, self.position = self.parts[fd], self.positions[fd]
            uc.reg_write(r.UC_ARM_REG_R0,33)
            before = len(self.io)
            super()._hook(uc,address,size,user)
            self.positions[fd] = self.position
            for i in range(before,len(self.io)): self.io[i].insert(0,fd)
            return
        super()._hook(uc,address,size,user)

    def run(self, head, body):
        r=self.reg; sp=0x7f01e000
        self.uc.reg_write(r.UC_ARM_REG_CPSR,0x10)
        self.uc.reg_write(r.UC_ARM_REG_SP,sp)
        self.uc.reg_write(r.UC_ARM_REG_LR,self.sentinel)
        for reg in (r.UC_ARM_REG_R0,r.UC_ARM_REG_R1,r.UC_ARM_REG_R2,r.UC_ARM_REG_R3,
                    r.UC_ARM_REG_R4,r.UC_ARM_REG_R5,r.UC_ARM_REG_R6,r.UC_ARM_REG_R7,
                    r.UC_ARM_REG_R8,r.UC_ARM_REG_R9,r.UC_ARM_REG_R10,r.UC_ARM_REG_R11,r.UC_ARM_REG_R12):
            self.uc.reg_write(reg,0)
        for offset,value in ((0x1e4,2560),(0x1f0,2560),(0x1ac,16),(0x1b8,16)):
            self.uc.mem_write(sp+offset,struct.pack('<I',value))
        for reg,value in ((r.UC_ARM_REG_R0,7),(r.UC_ARM_REG_R4,sp+0x188),
                          (r.UC_ARM_REG_R5,0x181cc),(r.UC_ARM_REG_R7,8),(r.UC_ARM_REG_R8,7)):
            self.uc.reg_write(reg,value)
        self.incoming=bytearray(head+body+header(0xfe,head[8:24],0))
        self.uc.emu_start(0xcb58,self.sentinel,timeout=20000000,count=30000000)
        if self.failure: raise RuntimeError('pc=0x%x: %s'%(self.uc.reg_read(r.UC_ARM_REG_PC),self.failure))
        if not self.stop_reason: raise RuntimeError('No bounded completion point reached')
        return {'stop_reason':self.stop_reason,'status':[[x[0],x[1]] for x in self.outgoing],
                'reply_crc_valid':all(crc32(x[:24])==struct.unpack('>I',x[24:28])[0] for x in self.outgoing),
                'remaining_input':len(self.incoming),'opens':self.opens,'io':self.io}

def main():
    p=Packer(); tests={}
    image=bytes((i*37+11)%256 for i in range(4093))
    h,b=p.pack(0x94,image); h=header(0x96,h[8:24],len(b))
    vm=AppUpdateVM(); out=vm.run(h,b)
    out['appfs_exact']=bytes(vm.parts[33][:len(image)])==image
    stored=struct.pack('<II',len(image)+8,crc32(image))+image
    out['bakfs_exact']=bytes(vm.parts[34][:len(stored)])==stored
    out['input_sha256']=hashlib.sha256(image).hexdigest()
    out['appfs_sha256']=hashlib.sha256(vm.parts[33][:len(image)]).hexdigest()
    out['bakfs_payload_sha256']=hashlib.sha256(vm.parts[34][8:8+len(image)]).hexdigest()
    out['passed']=(out['appfs_exact'] and out['bakfs_exact'] and out['stop_reason']=='reboot_requested'
                   and out['reply_crc_valid'] and out['remaining_input']==0 and out['status'][-1]==[254,0])
    tests['custom_appfs_and_backup']=out
    print('Active/backup write comparison:',out['appfs_exact'],out['bakfs_exact'],out['stop_reason'])
    assert out['passed'],'Active/backup write did not match expected format'
    badh=bytearray(h); badh[24]^=1
    badb=bytearray(b); badb[31]^=1
    ih,ib=p.pack(0x94,image,bad_plain_crc=True); ih=header(0x96,ih[8:24],len(ib))
    for name,head,body in [('bad_header_crc',bytes(badh),b),('bad_cipher_crc',h,bytes(badb)),('bad_plain_crc',ih,ib)]:
        m=AppUpdateVM(); result=m.run(head,body)
        result['passed']=(not any(x[1] in ('MEMERASE','write') for x in result['io']) and all(x==0xa5 for x in m.parts[33]) and all(x==0x5a for x in m.parts[34]) and result['stop_reason']!='reboot_requested' and result['reply_crc_valid'] and (result['stop_reason']=='header_crc_rejected' or any(x[0]==255 for x in result['status'])))
        tests[name]=result
        assert result['passed'],name
    report={'passed':all(x['passed'] for x in tests.values()),'tests':tests,'firmware_sha256':FIRMWARE_SHA256,
            'scope':'Real receive-header/CRC/dispatch + 0x96 appfs/bakfs update; OS I/O simulated',
            'network_calls':0,'real_device_writes':0,'host_commands_executed':0,
            'not_tested':['real listener','actual boot','full-sized bootable firmware','physical erase geometry']}
    Path('offline_appfs_report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: real dispatcher, appfs and backup writer, plus 3 rejection cases; no real I/O')

if __name__=='__main__': main()
