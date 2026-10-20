"""Build the Switch port locally and prepare an SD folder from user-owned files."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from stage_switch import ROOT, stage_game

DEPENDENCIES = {
    'XboxXBERecomp': ('TGP482/XboxXBERecomp', '40e9c35451305f09ca9d86f677e4b9c2c37ff26b'),
    'zydis': ('zyantific/zydis', 'a2278f1d254e492f6a6b39f6cb5d1f5d515659dc'),
    'tomlplusplus': ('marzer/tomlplusplus', '30172438cee64926dc41fdd9c11fb3ba5b2ba9de'),
    'imgui': ('ocornut/imgui', '8199457a7d9e453f8d3d9cadc14683fb54a858b5'),
    'glslang': ('KhronosGroup/glslang', '8a85691a0740d390761a1008b4696f57facd02c4'),
}
ICON_URL = 'https://cdn2.steamgriddb.com/icon_thumb/1b3ecca102b57d735ac385a9c03fd15b.png'
ICON_SHA256 = '42a1f166f317ecc471ba3b35ca18a23f8da69cf14628be066cd88461f8749a0c'


def run(command: list) -> None:
    subprocess.run([str(value) for value in command], cwd=ROOT, check=True)


def fetch_dependencies() -> None:
    for name, (repository, revision) in DEPENDENCIES.items():
        destination = ROOT / 'third_party' / name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            run(['git', 'init', destination])
            run(['git', '-C', destination, 'remote', 'add', 'origin', 'https://github.com/' + repository + '.git'])
            run(['git', '-C', destination, 'fetch', '--depth=1', 'origin', revision])
            run(['git', '-C', destination, 'checkout', '--detach', 'FETCH_HEAD'])
            if name == 'zydis':
                run(['git', '-C', destination, 'submodule', 'update', '--init', '--recursive', '--depth=1'])
        actual = subprocess.check_output(['git', '-C', str(destination), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise ValueError(f'{name} is at {actual}, expected {revision}; existing checkouts are preserved')
        # Existing checkouts may use CRLF. Reject source edits without treating
        # checkout line endings or a submodule's line endings as local patches.
        subprocess.run(['git', '-C', str(destination), 'diff', '--quiet', '--ignore-space-at-eol',
                        '--ignore-submodules=dirty', 'HEAD', '--'], check=True)
        submodules = subprocess.check_output(['git', '-C', str(destination), 'submodule', 'status',
                                             '--recursive'], text=True)
        if any(row and row[0] in '+-U' for row in submodules.splitlines()):
            raise ValueError(name + ' has missing or changed submodule revisions')
        run(['git', '-C', destination, 'submodule', 'foreach', '--recursive',
             'git diff --quiet --ignore-space-at-eol HEAD --'])


def fetch_icon() -> Path:
    from PIL import Image

    request = urllib.request.Request(ICON_URL, headers={'User-Agent': 'ShaolinMonksRecomp-Setup'})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(2 << 20)
    if hashlib.sha256(payload).hexdigest() != ICON_SHA256:
        raise ValueError('Requested artwork checksum changed; supply your own --icon JPEG instead')
    target = ROOT / '.private/branding/icon.jpg'
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(payload)) as image:
        if image.size != (256, 256):
            raise ValueError('Unexpected icon dimensions')
        image.convert('RGB').save(target, 'JPEG', quality=95, subsampling=0)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True, help='Your complete extracted Xbox game folder')
    default_devkit = Path(os.environ.get('DEVKITPRO', '/opt/devkitpro'))
    if os.name == 'nt' and not (default_devkit / 'cmake/Switch.cmake').is_file():
        default_devkit = Path('C:/devkitPro')
    parser.add_argument('--devkitpro', type=Path, default=default_devkit)
    parser.add_argument('--mesa-sdk', type=Path, default=ROOT / '.private/mesa-sdk')
    parser.add_argument('--fetch-deps', action='store_true', help='Fetch pinned public source dependencies')
    parser.add_argument('--fetch-mesa', action='store_true', help='Prepare the digest-pinned native Mesa SDK')
    branding = parser.add_mutually_exclusive_group()
    branding.add_argument('--fetch-icon', action='store_true', help='Fetch the requested SteamGridDB icon privately')
    branding.add_argument('--icon', type=Path, help='Your external 256x256 RGB JPEG')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--skip-codegen', action='store_true', help='Reuse a matching translation receipt')
    parser.add_argument('--reuse-build', action='store_true', help='Stage the existing NRO without recompiling')
    parser.add_argument('--output', type=Path, help='Fresh output folder; defaults to out/switch-sd/<build ID>')
    parser.add_argument('--hardlink', action='store_true', help='Development only: share original file storage')
    args = parser.parse_args()
    args.game = args.game.resolve()
    args.mesa_sdk = args.mesa_sdk.resolve()
    args.devkitpro = args.devkitpro.resolve()
    args.output = args.output.resolve() if args.output else None
    args.icon = args.icon.resolve() if args.icon else None
    if not (args.game / 'default.xbe').is_file():
        parser.error('--game must contain the original default.xbe and all extracted files')
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if not args.reuse_build and not (args.devkitpro / 'cmake/Switch.cmake').is_file():
        parser.error('--devkitpro must point to an installed Switch toolchain')
    if args.output and args.output.exists():
        parser.error('--output already exists; existing files and saves are never replaced')
    if args.reuse_build and (args.fetch_deps or args.fetch_mesa or args.fetch_icon or args.icon):
        parser.error('--reuse-build only stages existing build products; omit dependency/icon options')
    if not args.reuse_build:
        if args.fetch_deps:
            fetch_dependencies()
        if args.fetch_mesa:
            if (args.mesa_sdk / 'manifest.json').exists():
                print('Existing Mesa SDK will be verified by the builder.', flush=True)
            else:
                run([sys.executable, ROOT / 'tools/setup_mesa_sdk.py', '--destination', args.mesa_sdk,
                     '--devkitpro', args.devkitpro])
        icon = fetch_icon() if args.fetch_icon else args.icon
        command = [sys.executable, ROOT / 'tools/build_switch.py', '--xbe', args.game / 'default.xbe',
                   '--nro', '--mesa-sdk', args.mesa_sdk, '--devkitpro', args.devkitpro, '--jobs', args.jobs]
        if args.skip_codegen:
            command.append('--skip-codegen')
        if icon:
            command += ['--icon', icon]
        run(command)
    stage = stage_game(args.game, ROOT / 'build/switch/arm64/ShaolinMonksRecomp.nro',
                       ROOT / 'build/switch/build-receipt.json', args.output, args.hardlink)
    print(f'\nReady: {stage / "switch"}', flush=True)
    print('Copy switch/ to the SD root. Preserve hdd/ when updating.')
    print('This local setup includes private game data. Do not upload or redistribute it.')


if __name__ == '__main__':
    main()
