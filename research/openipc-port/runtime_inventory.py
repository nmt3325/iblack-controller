#!/usr/bin/env python3
"""Inspect pinned public OpenIPC files offline; never boots or flashes anything."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import struct
import subprocess
import zlib

KERNEL_SHA256 = 'fa79e044bc61556c39f4e50f278674077fbb9c89c4c0b2a3ce6cd68da6f38c3c'
ROOTFS_SHA256 = 'd0c146e7cbee11cc15475675e49c9fc11322778bd591c7f618680cb78ab9226e'
FILES = (
    'etc/wireless/usb', 'etc/init.d/S40network',
    'etc/network/interfaces.d/wlan0', 'usr/sbin/wlan_conf',
    'usr/sbin/sysupgrade', 'usr/bin/load_hisilicon',
    'lib/modules/3.0.8/extra/mt7601sta.ko',
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, maximum: int) -> bytes:
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= maximum:
        raise ValueError('Only bounded nonempty regular files are accepted')
    return path.read_bytes()


def kernel_evidence(uimage: bytes) -> dict:
    payload = uimage[64:]
    if len(payload) < 48 or struct.unpack_from('<I', payload, 0x24)[0] != 0x016F2818:
        raise ValueError('Expected a self-decompressing ARM zImage')
    limit = 24 * 1024 * 1024
    for match in re.finditer(b'\x1f\x8b\x08', payload):
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            kernel = d.decompress(payload[match.start():], limit)
        except zlib.error:
            continue
        if not d.eof or b'Linux version ' not in kernel:
            continue
        version = re.search(rb'Linux version ([0-9][^\s\0]*)', kernel)
        markers = (b'mtd_split_layout_register', b'split NOR layout: root on mtdblock', b'rootfs_data')
        return {
            'arm_zimage_magic_valid': True,
            'gzip_offset_within_zimage': match.start(),
            'decompressed_kernel_bytes': len(kernel),
            'decompressed_kernel_sha256': sha256(kernel),
            'linux_version': version.group(1).decode('ascii') if version else None,
            'split_markers': {s.decode(): s in kernel for s in markers},
            'markers_do_not_prove_actual_boot': True,
        }
    raise ValueError('No bounded, complete gzip Linux kernel found in zImage')


def wifi_profile(text: str, name: str) -> dict:
    pattern = r'if \[ "\$1" = "' + re.escape(name) + r'" \]; then\n(.*?)\nfi'
    found = re.search(pattern, text, re.S)
    if not found:
        raise ValueError('Expected profile not found: ' + name)
    modules = re.findall(r'^\s*modprobe\s+([A-Za-z0-9_]+)', found.group(1), re.M)
    return {'modules': modules, 'block_sha256': sha256(found.group(0).encode())}


def script_evidence(files: dict[str, bytes]) -> dict:
    text = {k: v.decode(errors='replace') for k, v in files.items() if not k.endswith('.ko')}
    usb, net, iface = (text[k] for k in ('etc/wireless/usb', 'etc/init.d/S40network',
                                       'etc/network/interfaces.d/wlan0'))
    updater, sensor = text['usr/sbin/sysupgrade'], text['usr/bin/load_hisilicon']
    return {
        'wireless_profiles': {name: wifi_profile(usb, name) for name in
                              ('mt7601u-generic', 'mt7601u-hi3518ev100-camhi')},
        'mt7601sta_module_in_pinned_rootfs': bool(files.get('lib/modules/3.0.8/extra/mt7601sta.ko')),
        'network_reads_wlandev_from_environment': 'fw_printenv -n wlandev' in net,
        'network_credentials_are_environment_fields_not_exported': all(
            'fw_printenv -n ' + n in iface for n in ('wlanssid', 'wlanpass')),
        'network_uses_wlan0': 'iface wlan0 inet dhcp' in iface,
        'supplicant_nl80211_wext_fallback': '-D nl80211,wext' in iface,
        'updater_contains_version_1_0_84': '1.0.84' in updater,
        'updater_ramroot': {s: s in updater for s in (
            'enter_ramfs()', 'pivot_root . mnt', 'chroot "$RAM_ROOT" /bin/busybox true',
            'release_old_root()', 'check_flash_ram()')},
        'updater_watchdog': {s: s in updater for s in (
            'watchdog_owner()', 'watchdog_keep()', 'printf 1 >&9', 'sleep 3')},
        'sensor_ar0141_24mhz_case': bool(re.search(
            r'ar0140\s*\|\s*ar0141\).*?devmem 0x20030030 32 0x1', sensor, re.S)),
        'file_fingerprints': {k: {'bytes': len(v), 'sha256': sha256(v)} for k, v in files.items()},
        'private_network_values_read_or_exported': False,
        'driver_load_association_sensor_output_not_tested': True,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--rootfs', type=Path, required=True)
    p.add_argument('--unsquashfs', type=Path, required=True, help='Trusted host unsquashfs executable')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        if a.output.exists():
            raise ValueError('Refusing to overwrite an existing report')
        kernel, rootfs = read_regular(a.kernel, 2 * 1024 * 1024), read_regular(a.rootfs, 8 * 1024 * 1024)
        if (sha256(kernel), sha256(rootfs)) != (KERNEL_SHA256, ROOTFS_SHA256):
            raise ValueError('Inputs differ from the pinned public artifacts')
        files = {}
        for name in FILES:
            # Fixed paths only, never /dev/mtd or vendor configuration files.
            item = subprocess.run([str(a.unsquashfs.resolve()), '-cat', str(a.rootfs.resolve()), name],
                                  capture_output=True, check=True, timeout=30)
            if not 0 < len(item.stdout) <= 2 * 1024 * 1024:
                raise ValueError('Unexpected extracted file size')
            files[name] = item.stdout
        report = {'mode': 'OFFLINE_PUBLIC_IMAGE_INVENTORY',
                  'kernel_sha256': sha256(kernel), 'rootfs_sha256': sha256(rootfs),
                  'kernel': kernel_evidence(kernel), 'runtime': script_evidence(files),
                  'host_unsquashfs_commands': len(FILES), 'network_connections': 0,
                  'real_device_writes': 0, 'real_reboots': 0, 'flash_write_allowed': False}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, indent=2) + '\n')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Do not echo extractor output or file contents into a report/error.
        p.error(type(exc).__name__ + ': offline inventory failed')
    print('PASS: pinned split-kernel/Wi-Fi/RAM-root inventory; no hardware validation')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
