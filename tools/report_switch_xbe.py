"""Compare XBE metadata and section digests without copying original game bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'config/switch-xbe-reference.json'


def inspect(data: bytes) -> dict:
    def region(offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('XBE contains an out-of-range file field')
        return data[offset:offset + size]

    def u32(offset: int) -> int:
        return struct.unpack('<I', region(offset, 4))[0]

    if len(data) < 0x178 or data[:4] != b'XBEH':
        raise ValueError('Not an XBE executable')
    digest = lambda value: hashlib.sha256(value).hexdigest()
    base, headers, image_size = u32(0x104), u32(0x108), u32(0x10c)
    if headers < 0x178 or headers > len(data) or image_size < headers:
        raise ValueError('Invalid XBE image/header size')
    cert = u32(0x118) - base
    region(cert, 0xb0)
    certificate_size = u32(cert)
    if cert < 0x178 or certificate_size < 0xb0 or cert + certificate_size > headers:
        raise ValueError('Invalid XBE certificate')
    title = region(cert + 12, 80).decode('utf-16le').split('\0', 1)[0]
    entries = [(u32(0x128) ^ 0xa8fc57ab, u32(0x158) ^ 0x5b6d40b6, 'retail'),
               (u32(0x128) ^ 0x94859d4b, u32(0x158) ^ 0xefb1f152, 'debug')]
    valid = [(entry, thunk, kind) for entry, thunk, kind in entries
             if base <= entry < base + image_size and base <= thunk < base + image_size]
    if len(valid) != 1:
        raise ValueError('Unrecognised XBE entry-point encoding')
    entry, thunk, kind = valid[0]
    count, table = u32(0x11c), u32(0x120) - base
    if not 1 <= count <= 64 or table < 0x178 or table + count * 56 > headers:
        raise ValueError('Invalid XBE section table')
    sections = []
    for index in range(count):
        offset = table + index * 56
        flags, va, vsize, raw, rsize, name_at = struct.unpack('<6I', region(offset, 24))
        name_at -= base
        if not 0 <= name_at < headers or va < base or va + vsize > base + image_size:
            raise ValueError('Invalid XBE section name or virtual range')
        name = data[name_at:min(name_at + 33, headers)]
        if b'\0' not in name:
            raise ValueError('Unterminated XBE section name')
        sections.append({'name': name.split(b'\0', 1)[0].decode('ascii'), 'flags': flags,
                         'virtual_address': va, 'virtual_bytes': vsize, 'raw_bytes': rsize,
                         'sha256': digest(region(raw, rsize))})
    layout = {'base': base, 'header_bytes': headers, 'image_bytes': image_size, 'entry': entry,
              'kernel_thunk': thunk, 'tls_address': u32(0x12c), 'stack_commit': u32(0x130),
              'heap_reserve': u32(0x134), 'heap_commit': u32(0x138), 'init_flags': u32(0x124),
              'encoding': kind, 'title_id': f'{u32(cert + 8):08X}'}
    return {'schema': 1, 'file_bytes': len(data), 'sha256': digest(data), 'title': title,
            'certificate_version': u32(cert + 0xac), 'region_mask': u32(cert + 0xa0),
            'allowed_media_mask': u32(cert + 0x9c), 'header_sha256': digest(region(0, headers)),
            'layout': layout, 'sections': sections}


def compare(actual: dict, reference: dict) -> dict:
    fields = sorted(key for key in set(actual['layout']) | set(reference['layout'])
                    if actual['layout'].get(key) != reference['layout'].get(key))
    a = actual['sections']
    b = reference['sections']
    different = [index for index in range(max(len(a), len(b)))
                 if index >= len(a) or index >= len(b) or a[index] != b[index]]
    return {'exact_file_match': actual['sha256'] == reference['sha256'],
            'header_matches': actual['header_sha256'] == reference['header_sha256'],
            'layout_differences': fields, 'different_section_indices': different,
            'layout_and_section_hashes_match': not fields and not different,
            'runtime_compatibility_proven': False,
            'note': 'This report does not bypass the NRO check or establish runtime compatibility.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xbe', type=Path, required=True)
    parser.add_argument('--reference', type=Path, default=REFERENCE)
    parser.add_argument('--output', type=Path, required=True, help='New metadata-only JSON report')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output already exists; choose a new report path')
    if args.xbe.stat().st_size > 64 << 20:
        parser.error('XBE exceeds the diagnostic size limit')
    actual = inspect(args.xbe.read_bytes())
    reference = json.loads(args.reference.read_bytes())
    actual['comparison'] = compare(actual, reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(actual, indent=2) + '\n')
    print(json.dumps(actual['comparison'], indent=2))
    print('Metadata and digests only; no original file contents or certificate keys were exported.')


if __name__ == '__main__':
    main()
