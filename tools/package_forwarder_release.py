"""Prepare public NSP and corresponding loader source assets without local keys."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import zipfile

from package_switch_release import json_bytes, require, sha256

NSP = 'MK-Shaolin-Monk-forwarder.nsp'
SOURCE = 'MK-Shaolin-Monk-forwarder-source.zip'
MANIFEST = 'forwarder-manifest.json'
CHECKSUMS = 'forwarder-checksums.txt'
ASSETS = {NSP, SOURCE, MANIFEST, CHECKSUMS}
SOURCE_FILES = {'source/main.c', 'source/trampoline.s', 'source/version.h', 'source/forwarder.json',
                'source/base.json', 'source/package_switch_forwarder.py', 'README.txt',
                'licenses/Sphaira-GPL-3.0.txt', 'licenses/nx-hbloader-ISC.txt', 'licenses/libnx-ISC.txt'}
TARGET = 'sdmc:/switch/shaolin-recomp/ShaolinMonksRecomp.nro'


def nca_hashes(payload: bytes) -> dict[str, str]:
    require(len(payload) < 4 << 20 and payload[:4] == b'PFS0', 'Expected a small forwarder NSP')
    count, string_size = struct.unpack_from('<II', payload, 4)
    require(count == 3 and string_size < 1024, 'Expected three forwarder NCAs')
    strings_at = 16 + count * 24
    start = strings_at + string_size
    require(start <= len(payload), 'Invalid NSP table')
    strings = payload[strings_at:start]
    result = {}
    spans = []
    for i in range(count):
        offset, size, name_at = struct.unpack_from('<QQI', payload, 16 + i * 24)
        require(name_at < len(strings) and 0 in strings[name_at:], 'Invalid NCA name')
        name = strings[name_at:strings.index(0, name_at)].decode()
        require(name not in result and '/' not in name and '\\' not in name and name.endswith('.nca'),
                'Unexpected NSP member')
        require(size > 0 and start + offset + size <= len(payload), 'Invalid NCA range')
        data = payload[start + offset:start + offset + size]
        digest = sha256(data)
        require(name.split('.')[0] == digest[:32], 'NCA content ID mismatch')
        result[name] = digest
        spans.append((offset, offset + size))
    spans.sort()
    require(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])), 'Overlapping NCA data')
    return result


def checksum_bytes(output: Path) -> bytes:
    return ''.join(sha256((output / name).read_bytes()) + '  ' + name + '\n'
                   for name in (NSP, SOURCE, MANIFEST)).encode()


def prepare_forwarder(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    require(not any((output / name).exists() for name in ASSETS), 'Forwarder release assets already exist')
    receipt = json.loads((source / 'build.json').read_bytes())
    require(receipt['title'] == 'MK Shaolin Monk' and receipt['author'] == 'ArdRecomp'
            and receipt['title_id'] == '0559208E2C6F4000', 'Unexpected forwarder identity')
    require(receipt['target'] == TARGET and receipt['argv'] == TARGET and receipt['external_nro']
            and not receipt['embedded_game_assets'], 'Unexpected forwarder target')
    require(receipt['validation'] and all(receipt['validation'].values()), 'Forwarder build was not validated')
    nsp = (source / receipt['nsp']).read_bytes()
    require(sha256(nsp) == receipt['nsp_sha256'] and len(nsp) == receipt['nsp_bytes'], 'NSP differs from receipt')
    require(nca_hashes(nsp) == receipt['nca_files'], 'NSP content differs from validated build')
    expected = receipt['loader_source_hashes']
    for name, digest in expected.items():
        relative = 'source/base.json' if name == 'base.json' else name
        require(relative in SOURCE_FILES and sha256((source / relative).read_bytes()) == digest,
                'Loader source mismatch: ' + name)
    require(sha256((source / 'source/package_switch_forwarder.py').read_bytes()) == receipt['builder_sha256'],
            'Original forwarder builder source is missing')
    output.mkdir(parents=True, exist_ok=True)
    (output / NSP).write_bytes(nsp)
    with zipfile.ZipFile(output / SOURCE, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(SOURCE_FILES):
            data = (source / name).read_bytes()
            item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data)
    record = {'schema': 1, 'title': receipt['title'], 'author': receipt['author'],
              'title_id': receipt['title_id'], 'target': TARGET, 'external_nro': True,
              'original_game_files_included': False, 'hardware_boot_tested': False,
              'nca_files': receipt['nca_files'], 'loader_revision': receipt['loader_revision'],
              'loader_source_hashes': expected, 'builder_sha256': receipt['builder_sha256'],
              'source_files': {name: sha256((source / name).read_bytes()) for name in sorted(SOURCE_FILES)},
              'assets': {name: {'sha256': sha256((output / name).read_bytes()),
                               'bytes': (output / name).stat().st_size} for name in (NSP, SOURCE)}}
    (output / MANIFEST).write_bytes(json_bytes(record))
    (output / CHECKSUMS).write_bytes(checksum_bytes(output))
    verify_forwarder(output)


def verify_forwarder(output: Path) -> dict:
    record = json.loads((output / MANIFEST).read_bytes())
    require(record['schema'] == 1 and record['title'] == 'MK Shaolin Monk'
            and record['author'] == 'ArdRecomp' and record['title_id'] == '0559208E2C6F4000'
            and record['target'] == TARGET and record['external_nro']
            and not record['original_game_files_included'], 'Unexpected public forwarder identity')
    require(set(record['assets']) == {NSP, SOURCE}, 'Unexpected forwarder assets')
    for name in (NSP, SOURCE):
        data = (output / name).read_bytes()
        require(record['assets'][name] == {'sha256': sha256(data), 'bytes': len(data)},
                'Forwarder asset checksum mismatch: ' + name)
    require(nca_hashes((output / NSP).read_bytes()) == record['nca_files'], 'Forwarder NCA mismatch')
    require((output / CHECKSUMS).read_bytes() == checksum_bytes(output), 'Forwarder checksums mismatch')
    with zipfile.ZipFile(output / SOURCE) as archive:
        entries = archive.infolist()
        require(len(entries) == len(SOURCE_FILES) and {e.filename for e in entries} == SOURCE_FILES
                and set(record['source_files']) == SOURCE_FILES, 'Unexpected loader source package files')
        require(sum(e.file_size for e in entries) < 2 << 20, 'Source package exceeds size bound')
        for name in SOURCE_FILES:
            require(sha256(archive.read(name)) == record['source_files'][name], 'Loader source checksum mismatch')
        for name, digest in record['loader_source_hashes'].items():
            relative = 'source/base.json' if name == 'base.json' else name
            require(name in ('source/main.c', 'source/trampoline.s', 'base.json')
                    and sha256(archive.read(relative)) == digest, 'Pinned loader source mismatch')
        require(sha256(archive.read('source/package_switch_forwarder.py')) == record['builder_sha256'],
                'Loader build source mismatch')
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forwarder', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        verify_forwarder(args.output)
    else:
        if not args.forwarder:
            parser.error('--forwarder is required')
        prepare_forwarder(args.forwarder, args.output)
    print('Forwarder NSP, source/license ZIP and checksums verified.')


if __name__ == '__main__':
    main()
