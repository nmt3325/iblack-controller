"""Offline firmware observations only: no sockets, commands, or flash writes.

The DNS model mirrors cmsMain.out 0x5b954 in the analyzed dump. It is not a
measurement of the running camera or a guarantee about resolver retry order.
The header reader only checks the observed 36-byte additive header structure;
it does not validate a whole update, authenticity, or flash compatibility.
"""
import argparse
import ipaddress
import json
import struct
from pathlib import Path


def ipv4(value):
    return str(ipaddress.IPv4Address(value))


def model_nameservers(gateway, configured_dns, dhcp_dns=()):
    gateway, configured_dns = ipv4(gateway), ipv4(configured_dns)
    zero, google, public114 = '0.0.0.0', '8.8.8.8', '114.114.114.114'
    result = []
    written_dns = zero
    if configured_dns not in (zero, gateway, google):
        result.append(configured_dns)
        written_dns = configured_dns
    for candidate in dhcp_dns:
        candidate = ipv4(candidate)
        if candidate == zero:
            break
        if candidate not in (gateway, written_dns, google, public114):
            result.append(candidate)
    if configured_dns != public114:
        result.append(public114)
    result.append(google)
    if gateway != zero:
        result.append(gateway)
    return result


def inspect_update_header(data):
    if len(data) < 36:
        raise ValueError('At least 36 bytes are required; this is not a raw flash parser.')
    words = struct.unpack('<9I', data[:36])
    computed = sum(words[:8]) & 0xffffffff
    return {
        'analysis_only': True,
        'header_bytes': 36,
        'header_sum_matches': computed == words[8],
        'header_sum_computed': f'{computed:08x}',
        'header_sum_stored': f'{words[8]:08x}',
        'type_byte_at_3': data[3],
        'compat_id_at_0x14': words[5],
        'compat_selector_low_nibble_at_0x18': data[24] & 15,
        'compat_revision_u16_at_0x1a': struct.unpack_from('<H', data, 26)[0],
        'whole_update_validated': False,
        'authenticity_validated': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    dns = sub.add_parser('dns-order')
    dns.add_argument('--gateway', required=True)
    dns.add_argument('--dns', required=True)
    dns.add_argument('--dhcp', nargs='*', default=[])
    header = sub.add_parser('update-header')
    header.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.mode == 'dns-order':
        result = {'static_model_only': True, 'nameservers': model_nameservers(args.gateway, args.dns, args.dhcp)}
    else:
        with args.path.open('rb') as f:
            result = inspect_update_header(f.read(36))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
