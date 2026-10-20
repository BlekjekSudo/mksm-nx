"""Package and verify a public Switch SD ZIP without original game files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP = 'switch/shaolin-recomp/'
SDK = '40e9c35451305f09ca9d86f677e4b9c2c37ff26b'
BUILD_INPUTS = {'switch/CMakeLists.txt', 'tools/prepare_switch_runtime.py',
                'tools/prepare_recomp_runtime.py', 'config/game.toml'}
MANIFEST_KEYS = {'schema', 'tag', 'source_commit', 'build_id', 'nro_sha256', 'xbe_sha256',
                 'sdk_revision', 'source_inputs', 'nro_metadata', 'archive', 'files'}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=root)


def blob(root: Path, name: str) -> bytes:
    return git(root, 'show', 'HEAD:' + name)


def tracked(root: Path) -> list[str]:
    return list(filter(None, git(root, 'ls-files', '-z').decode().split('\0')))


def source_names(root: Path) -> set[str]:
    return BUILD_INPUTS | {name for name in tracked(root) if name.startswith('src/')
                           and Path(name).suffix in {'.cpp', '.h', '.inl'}}


def check_tag(tag: str) -> None:
    require(re.fullmatch(r'switch-v\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', tag) is not None,
            'Use a switch-vMAJOR.MINOR.PATCH tag with an optional prerelease suffix')


def inspect_nro(data: bytes) -> dict:
    require(0x80 <= len(data) <= 96 << 20 and data[0x10:0x14] == b'NRO0', 'Invalid NRO')
    base = struct.unpack_from('<I', data, 0x18)[0]
    require(0x80 <= base <= len(data) - 56 and data[base:base + 4] == b'ASET', 'Missing NRO assets')
    icon_at, icon_size, nacp_at, nacp_size, _, romfs_size = struct.unpack_from('<6Q', data, base + 8)
    require(nacp_size == 0x4000 and icon_size > 4 and romfs_size == 0,
            'Expected NACP and icon only; embedded RomFS data is not permitted')
    ranges = sorted([(icon_at, icon_at + icon_size), (nacp_at, nacp_at + nacp_size)])
    require(ranges[0][0] >= 56 and ranges[0][1] <= ranges[1][0]
            and base + ranges[1][1] == len(data), 'Invalid or extra NRO asset data')
    icon = data[base + icon_at:base + icon_at + icon_size]
    require(icon[:2] == b'\xff\xd8' and icon[-2:] == b'\xff\xd9', 'Expected a JPEG NRO icon')
    nacp = data[base + nacp_at:base + nacp_at + nacp_size]
    supported = struct.unpack_from('<I', nacp, 0x302c)[0]
    require(supported != 0, 'Missing NRO languages')
    for i in range(16):
        title = nacp[i * 0x300:i * 0x300 + 0x200].split(b'\0', 1)[0].decode()
        author = nacp[i * 0x300 + 0x200:(i + 1) * 0x300].split(b'\0', 1)[0].decode()
        if title or author or supported & (1 << i):
            require((title, author) == ('Shaolin Monks Recomp', 'ArdRecomp'), 'NRO title/author mismatch')
    version = nacp[0x3060:0x3070].split(b'\0', 1)[0].decode()
    require(re.fullmatch('[0-9A-Za-z.+-]{1,16}', version) is not None, 'Invalid NRO version')
    return {'title': 'Shaolin Monks Recomp', 'author': 'ArdRecomp', 'version': version,
            'icon_sha256': sha256(icon)}


def public_files(root: Path, metadata: dict, nro: bytes) -> dict[str, bytes]:
    info = {key: metadata[key] for key in ('tag', 'source_commit', 'build_id', 'nro_sha256',
                                          'xbe_sha256', 'sdk_revision')}
    info.update(original_game_files_included=False, full_campaign_verified=False)
    game_note = (blob(root, 'resources/switch-game-files.txt') +
                 ('\nRequired default.xbe SHA-256 for this NRO:\n' + metadata['xbe_sha256'] + '\n').encode())
    files = {APP + 'ShaolinMonksRecomp.nro': nro,
             APP + 'readme.txt': blob(root, 'resources/switch-readme.txt'),
             APP + 'game/README.txt': game_note,
             APP + 'build.json': json_bytes(info),
             APP + 'licenses/LICENSE.txt': blob(root, 'LICENSE')}
    for name in tracked(root):
        if name.startswith('licenses/') and Path(name).suffix in {'.txt', '.md'}:
            files[APP + name] = blob(root, name)
    return files


def make_package(root: Path, nro_path: Path, receipt_path: Path, tag: str, output: Path,
                 forwarder: Path | None = None) -> Path:
    check_tag(tag)
    require(not output.exists(), 'Choose a fresh release output directory')
    receipt = json.loads(receipt_path.read_text())
    nro = nro_path.read_bytes()
    require(sha256(nro) == receipt['nro_sha256'], 'NRO differs from its build receipt')
    metadata = inspect_nro(nro)
    require(re.fullmatch('[0-9a-f]{16}', receipt['build_id']) is not None, 'Invalid build ID')
    require(receipt['build_id'].encode() in nro, 'NRO does not contain the recorded build ID')
    inputs = receipt['translation']['inputs']
    require(inputs['sdk'] == SDK, 'Wrong SDK revision')
    catalog = {}
    for name in sorted(source_names(root)):
        data = (root / name).read_bytes()
        require(sha256(data) == receipt['maintained_sources'].get(name),
                'Rebuild the NRO after changing ' + name)
        committed = blob(root, name)
        require(data.replace(b'\r\n', b'\n') == committed, 'Uncommitted binary input: ' + name)
        catalog[name] = sha256(committed)
    manifest = {'schema': 1, 'tag': tag, 'source_commit': git(root, 'rev-parse', 'HEAD').decode().strip(),
                'build_id': receipt['build_id'], 'nro_sha256': sha256(nro),
                'xbe_sha256': inputs['xbe_sha256'], 'sdk_revision': SDK, 'source_inputs': catalog,
                'nro_metadata': metadata}
    files = public_files(root, manifest, nro)
    output.mkdir(parents=True)
    archive = output / f'mksm-nx-{tag}-sd-card.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, data)
    manifest['archive'] = {'name': archive.name, 'sha256': sha256(archive.read_bytes()),
                           'bytes': archive.stat().st_size}
    manifest['files'] = {name: {'sha256': sha256(data), 'bytes': len(data)}
                         for name, data in sorted(files.items())}
    (output / 'release-manifest.json').write_bytes(json_bytes(manifest))
    checksum_files = (archive, output / 'release-manifest.json')
    (output / 'checksums.txt').write_bytes(''.join(
        sha256(path.read_bytes()) + '  ' + path.name + '\n' for path in checksum_files).encode())
    if forwarder:
        from package_forwarder_release import prepare_forwarder
        prepare_forwarder(forwarder, output)
    verify_package(root, output, tag)
    return archive


def verify_package(root: Path, output: Path, tag: str) -> dict:
    check_tag(tag)
    manifest = json.loads((output / 'release-manifest.json').read_bytes())
    require(set(manifest) == MANIFEST_KEYS and manifest['schema'] == 1, 'Unexpected release manifest')
    require(manifest['tag'] == tag, 'Release tag mismatch')
    require(manifest['source_commit'] == git(root, 'rev-parse', 'HEAD').decode().strip(),
            'Release must match the checked-out tag commit')
    require(manifest['sdk_revision'] == SDK, 'SDK mismatch')
    require(re.fullmatch('[0-9a-f]{16}', manifest['build_id']) is not None, 'Invalid build ID')
    require(re.fullmatch('[0-9a-f]{64}', manifest['xbe_sha256']) is not None, 'Invalid XBE hash')
    require(set(manifest['source_inputs']) == source_names(root), 'Incomplete binary source provenance')
    for name, expected in manifest['source_inputs'].items():
        require(sha256(blob(root, name)) == expected, 'Tagged source mismatch: ' + name)
    expected_name = f'mksm-nx-{tag}-sd-card.zip'
    require(manifest['archive']['name'] == expected_name, 'Unexpected archive name')
    from package_forwarder_release import ASSETS, verify_forwarder
    found = {p.name for p in output.iterdir()}
    expected_assets = {expected_name, 'release-manifest.json', 'checksums.txt'}
    if found & ASSETS:
        expected_assets |= ASSETS
        verify_forwarder(output)
    require(found == expected_assets,
            'Unexpected release assets; do not include private staging output')
    archive = output / expected_name
    require(archive.stat().st_size == manifest['archive']['bytes']
            and sha256(archive.read_bytes()) == manifest['archive']['sha256'], 'Archive checksum mismatch')
    checksums = ''.join(sha256(path.read_bytes()) + '  ' + path.name + '\n'
                        for path in (archive, output / 'release-manifest.json')).encode()
    require((output / 'checksums.txt').read_bytes() == checksums, 'Release checksums mismatch')
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(len(entries) <= 128 and len({e.filename for e in entries}) == len(entries),
                'Unexpected or duplicate ZIP entries')
        require(sum(e.file_size for e in entries) <= 128 << 20, 'Release exceeds size bound')
        nro = bundle.read(APP + 'ShaolinMonksRecomp.nro')
        require(sha256(nro) == manifest['nro_sha256'], 'NRO checksum mismatch')
        require(manifest['build_id'].encode() in nro, 'NRO build ID mismatch')
        require(inspect_nro(nro) == manifest['nro_metadata'], 'NRO metadata mismatch')
        expected_files = public_files(root, manifest, nro)
        require({e.filename for e in entries} == set(expected_files),
                'ZIP contains unapproved files, game data, or user data')
        require(set(manifest['files']) == set(expected_files), 'File manifest mismatch')
        for name, expected in expected_files.items():
            data = bundle.read(name)
            require(data == expected and manifest['files'][name] == {'sha256': sha256(data), 'bytes': len(data)},
                    'Packaged file differs: ' + name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--forwarder', type=Path, help='Validated local forwarder build directory')
    parser.add_argument('--nro', type=Path, default=ROOT / 'build/switch/arm64/ShaolinMonksRecomp.nro')
    parser.add_argument('--receipt', type=Path, default=ROOT / 'build/switch/build-receipt.json')
    args = parser.parse_args()
    if args.verify:
        result = verify_package(ROOT, args.output.resolve(), args.tag)
        print(f"Verified public SD package {result['tag']} ({result['build_id']}); no original game files.")
    else:
        print(make_package(ROOT, args.nro.resolve(), args.receipt.resolve(), args.tag, args.output.resolve(),
                           args.forwarder))


if __name__ == '__main__':
    main()
