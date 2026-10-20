"""Prepare or verify a private SD layout using the user's original game files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
APP = Path('switch/shaolin-recomp')


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            result.update(chunk)
    return result.hexdigest()


def checked_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Invalid staged file: ' + relative)
    return path


def verify_stage(stage: Path) -> dict:
    stage = stage.resolve()
    if (stage / 'INCOMPLETE.txt').exists():
        raise ValueError('Setup is incomplete')
    manifest = json.loads((stage / 'staging.json').read_text())
    if manifest.get('schema') != 1 or not manifest.get('private_game_files'):
        raise ValueError('Not a completed private SD setup')
    for record in manifest['files']:
        target = checked_file(stage, record['path'])
        if target.stat().st_size != record['size'] or sha256(target) != record['sha256']:
            raise ValueError('Staged file differs from its manifest: ' + record['path'])
    return manifest


def stage_game(game: Path, nro: Path, receipt_path: Path, output: Path | None = None,
               hardlink: bool = False) -> Path:
    game, nro, receipt_path = game.resolve(), nro.resolve(), receipt_path.resolve()
    receipt = json.loads(receipt_path.read_text())
    build_id = receipt['build_id']
    if not re.fullmatch('[0-9a-f]{16}', build_id):
        raise ValueError('Invalid build ID in receipt')
    with nro.open('rb') as stream:
        stream.seek(0x10)
        if stream.read(4) != b'NRO0':
            raise ValueError('Invalid NRO header')
    if sha256(nro) != receipt['nro_sha256']:
        raise ValueError('NRO differs from the build receipt')
    expected_xbe = receipt['translation']['inputs']['xbe_sha256']
    if sha256(game / 'default.xbe') != expected_xbe:
        raise ValueError('Game XBE does not match this translation')
    stage = (output or ROOT / 'out/switch-sd' / build_id).resolve()
    if stage == game or stage.is_relative_to(game) or game.is_relative_to(stage):
        raise ValueError('Output must be separate from the original game folder')
    if stage.exists():
        raise FileExistsError('Output already exists; choose a new folder: ' + str(stage))
    entries = sorted(game.rglob('*'))
    if any(path.is_symlink() for path in entries):
        raise ValueError('Original game folder must not contain symbolic links')
    sources = [path for path in entries if path.is_file()]
    for source in sources:
        if source.stat().st_size >= 1 << 32:
            raise ValueError('Asset exceeds FAT32 file size limit: ' + source.name)
    destination = stage / APP
    destination.mkdir(parents=True)
    # No completed manifest is written until every copied byte has been checked.
    incomplete = stage / 'INCOMPLETE.txt'
    incomplete.write_text('Setup is incomplete. Do not copy this folder to the SD card.\n')
    shutil.copy2(nro, destination / 'ShaolinMonksRecomp.nro')
    shutil.copy2(receipt_path, destination / 'build-receipt.json')
    shutil.copytree(ROOT / 'licenses', destination / 'licenses')
    shutil.copy2(ROOT / 'LICENSE', destination / 'licenses/LICENSE.txt')
    shutil.copy2(ROOT / 'resources/switch-readme.txt', destination / 'readme.txt')
    for name in ('hdd', 'cache', 'logs'):
        (destination / name).mkdir()
    records = []
    for source in sources:
        target = destination / 'game' / source.relative_to(game)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = source.stat()
        expected = hashlib.sha256()
        if hardlink:
            os.link(source, target)
            expected_hash = sha256(source)
        else:
            with source.open('rb') as incoming, target.open('xb') as outgoing:
                for chunk in iter(lambda: incoming.read(1 << 20), b''):
                    expected.update(chunk)
                    outgoing.write(chunk)
            expected_hash = expected.hexdigest()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Original file changed during setup: ' + source.name)
        if sha256(target) != expected_hash:
            raise ValueError('Copied asset checksum mismatch: ' + source.name)
        records.append({'path': target.relative_to(stage).as_posix(), 'size': after.st_size,
                        'sha256': expected_hash, 'mode': 'hardlink' if hardlink else 'copy'})
    if sha256(game / 'default.xbe') != expected_xbe:
        raise ValueError('Original executable changed during setup')
    app_nro = destination / 'ShaolinMonksRecomp.nro'
    if sha256(app_nro) != receipt['nro_sha256']:
        raise ValueError('Staged NRO checksum mismatch')
    records.append({'path': app_nro.relative_to(stage).as_posix(), 'size': app_nro.stat().st_size,
                    'sha256': receipt['nro_sha256'], 'mode': 'copy'})
    manifest = {'schema': 1, 'build_id': build_id, 'private_game_files': True,
                'not_for_redistribution': True, 'game_files': len(sources), 'files': records,
                'hardware_tested': False}
    (stage / 'staging.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'checksums.txt').write_text(receipt['nro_sha256'] + '  ShaolinMonksRecomp.nro\n')
    incomplete.unlink()
    return stage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, help='Complete external extracted game folder')
    parser.add_argument('--verify', type=Path, help='Verify a completed local SD setup')
    parser.add_argument('--output', type=Path, help='New output folder; existing folders are never replaced')
    parser.add_argument('--nro', type=Path, default=ROOT / 'build/switch/arm64/ShaolinMonksRecomp.nro')
    parser.add_argument('--receipt', type=Path, default=ROOT / 'build/switch/build-receipt.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--copy', action='store_true', help='Copy original files (default)')
    mode.add_argument('--hardlink', action='store_true', help='Local development only: share original file storage')
    args = parser.parse_args()
    if args.verify:
        result = verify_stage(args.verify)
        print(f"Verified {len(result['files'])} staged files for build {result['build_id']}")
        return
    if not args.game:
        parser.error('--game is required unless --verify is used')
    stage = stage_game(args.game, args.nro, args.receipt, args.output, args.hardlink)
    print(stage)
    print('Copy the switch/ folder to the SD root. Preserve the existing hdd/ folder when updating.')
    print('This local setup contains private game files and must not be uploaded or redistributed.')


if __name__ == '__main__':
    main()
