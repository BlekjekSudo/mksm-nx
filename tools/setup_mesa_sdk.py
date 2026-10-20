#!/usr/bin/env python3
"""Extract the pinned native Horizon Mesa NVK SDK without modifying devkitPro.

The default downloads a checksum-verified OCI layer and copies the installed
devkitPro support archives into the project. The optional Docker method requires
a running Linux-container engine and copies from one stopped, network-disabled
container. Neither method executes image code or exposes game files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tarfile
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/autorunhq/switch-dev@sha256:ba1197f4ff6afe334b960535812ef4c412346cbefb15548d9300d9fc2aebaf23'
MESA_REVISION = '0ff52079ca9e9e837ed27eb95a8d239f439a1864'
VULKAN_SHA256 = '0f90a65b697609e6ebe5f36e3234467057597dfc5692e35bb9278b7dbe285aaa'
MANIFEST = 'sha256:f69d5631ae7776d12755692bb36cdbea5a704eb89a29eee4bd20334edd3f6a82'
MESA_LAYER = 'sha256:3be2ad423ecc70682e4840153495abe75f6ea2dc0fc58098122ecadaf5889b90'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def registry_sdk(destination: Path, devkitpro: Path) -> dict:
    """Fetch the immutable Mesa layer directly; never execute container code."""
    token_url = 'https://ghcr.io/token?service=ghcr.io&scope=repository:autorunhq/switch-dev:pull'
    with urllib.request.urlopen(token_url, timeout=60) as response:
        token = json.load(response)['token']
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json'}
    metadata = destination / 'upstream-metadata'
    metadata.mkdir(parents=True, exist_ok=True)

    def document(kind: str, digest: str, name: str) -> dict:
        request = urllib.request.Request('https://ghcr.io/v2/autorunhq/switch-dev/' + kind + '/' + digest, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest.removeprefix('sha256:'):
            raise RuntimeError('Registry metadata checksum mismatch: ' + name)
        (metadata / name).write_bytes(data)
        return json.loads(data)

    index = document('manifests', IMAGE.split('@', 1)[1], 'oci-index.json')
    if not any(entry['digest'] == MANIFEST and entry['platform'] == {'architecture': 'amd64', 'os': 'linux'}
               for entry in index['manifests']):
        raise RuntimeError('Pinned platform manifest absent from immutable image index')
    manifest = document('manifests', MANIFEST, 'oci-manifest.json')
    if not any(entry['digest'] == MESA_LAYER for entry in manifest['layers']):
        raise RuntimeError('Pinned Mesa layer absent from immutable platform manifest')
    config = document('blobs', manifest['config']['digest'], 'oci-config.json')
    if config['config']['Labels']['dev.autorun.mesa-switch'] != MESA_REVISION:
        raise RuntimeError('Image Mesa revision label mismatch')
    cache = destination / 'downloads'
    cache.mkdir(exist_ok=True)
    archive = cache / (MESA_LAYER.removeprefix('sha256:') + '.tar.gz')
    if not archive.is_file() or sha256(archive) != MESA_LAYER.removeprefix('sha256:'):
        request = urllib.request.Request('https://ghcr.io/v2/autorunhq/switch-dev/blobs/' + MESA_LAYER, headers=headers)
        print('Downloading digest-pinned native Mesa SDK layer (135 MB)', flush=True)
        with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if sha256(archive) != MESA_LAYER.removeprefix('sha256:'):
            raise RuntimeError('Native Mesa layer checksum mismatch')
    prefix = 'opt/devkitpro/portlibs/switch/'
    with tarfile.open(archive, 'r:gz') as package:
        for member in package:
            name = member.name.removeprefix('./')
            if not name.startswith(prefix) or not member.isfile():
                continue
            relative = name[len(prefix):]
            if relative.startswith(('include/vulkan/', 'include/vk_video/')) or relative == 'lib/libvulkan.a':
                target = (destination / relative).resolve()
            elif relative in ('lib/cmake/Vulkan/VulkanConfig.cmake', 'lib/pkgconfig/vulkan.pc'):
                target = metadata / Path(relative).name
            elif relative.startswith('share/licenses/mesa-switch/'):
                target = destination / 'licenses' / relative.removeprefix('share/licenses/')
            else:
                continue
            if not target.is_relative_to(destination):
                raise RuntimeError('SDK archive path escapes destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    dependency_origins = {}
    for library in ('libexpat.a', 'libzstd.a', 'libz.a'):
        source = devkitpro / 'portlibs/switch/lib' / library
        if not source.is_file():
            raise RuntimeError('Missing installed devkitPro dependency: ' + str(source))
        shutil.copyfile(source, destination / 'lib' / library)
        dependency_origins[library] = {'path': str(source), 'sha256': sha256(source)}
    return {'Id': manifest['config']['digest'], 'acquisition': 'verified-OCI-layer',
            'platform_manifest': MANIFEST, 'mesa_layer': MESA_LAYER,
            'dependency_origins': dependency_origins}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=ROOT / '.private/mesa-sdk')
    parser.add_argument('--pull', action='store_true', help='Pull the digest-pinned image if not already local')
    parser.add_argument('--method', choices=('registry', 'docker'), default='registry',
                        help='Download only the verified Mesa OCI layer, or copy from an existing Docker image')
    default_devkit = Path(os.environ.get('DEVKITPRO', '/opt/devkitpro'))
    if os.name == 'nt' and not default_devkit.is_dir():
        default_devkit = Path('C:/devkitPro')
    parser.add_argument('--devkitpro', type=Path, default=default_devkit)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if args.pull and args.method == 'docker':
        subprocess.run(['docker', 'pull', IMAGE], check=True)
    destination.mkdir(parents=True, exist_ok=True)
    if args.method == 'registry':
        image = registry_sdk(destination, args.devkitpro.resolve())
    else:
        image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
        if IMAGE not in image.get('RepoDigests', []):
            raise RuntimeError('Docker image does not carry the requested immutable digest')
        name = 'mksm-mesa-sdk-extract-' + uuid.uuid4().hex[:12]
        container = subprocess.check_output(['docker', 'create', '--name', name, '--network', 'none', IMAGE, 'true'], text=True).strip()
        try:
            docker_sdk(container, destination)
        finally:
            subprocess.run(['docker', 'rm', container], check=True)
    return finish_manifest(destination, image)


def docker_sdk(container: str, destination: Path) -> None:
    for directory in ('vulkan', 'vk_video'):
        target = destination / 'include' / directory
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(['docker', 'cp', container + ':/opt/devkitpro/portlibs/switch/include/' + directory + '/.', str(target)], check=True)
    (destination / 'lib').mkdir(exist_ok=True)
    for library in ('libvulkan.a', 'libexpat.a', 'libzstd.a', 'libz.a'):
        subprocess.run(['docker', 'cp', container + ':/opt/devkitpro/portlibs/switch/lib/' + library,
                        str(destination / 'lib' / library)], check=True)
    metadata = destination / 'upstream-metadata'
    metadata.mkdir(exist_ok=True)
    for source, target in (
        ('/opt/devkitpro/portlibs/switch/lib/cmake/Vulkan/VulkanConfig.cmake', 'VulkanConfig.cmake'),
        ('/opt/devkitpro/portlibs/switch/lib/pkgconfig/vulkan.pc', 'vulkan.pc'),
    ):
        subprocess.run(['docker', 'cp', container + ':' + source, str(metadata / target)], check=True)


def finish_manifest(destination: Path, image: dict) -> int:
    if sha256(destination / 'lib/libvulkan.a') != VULKAN_SHA256:
        raise RuntimeError('Pinned native Vulkan archive checksum does not match')
    files = {path.relative_to(destination).as_posix(): sha256(path)
             for directory in ('include', 'lib', 'upstream-metadata')
             for path in sorted((destination / directory).rglob('*')) if path.is_file()}
    manifest = {
        'schema': 1, 'kind': 'native-horizon-mesa-nvk-sdk', 'image': IMAGE,
        'image_id': image['Id'], 'mesa_repository': 'https://github.com/danfromtico/mesa-switch',
        'acquisition': image.get('acquisition', 'stopped-Docker-container'),
        'platform_manifest': image.get('platform_manifest'), 'mesa_layer': image.get('mesa_layer'),
        'dependency_origins': image.get('dependency_origins', {}),
        'mesa_revision': MESA_REVISION,
        'container_source_revision': 'cf4bc5a5a67d772ff083aa90c9ab1fdd0624f4e8',
        'container_libnx_revision': '146c3d1446d35546943c7b6cfcd479bc1f5e9b67',
        'global_devkitpro_modified': False, 'vulkan_loader': False,
        'link_libraries': ['vulkan', 'expat', 'zstd', 'z', 'nx', 'stdc++', 'm'],
        'libelf_note': 'Pinned package CMake metadata mentions libelf, but native NVK has no references. No stub library is supplied; the final link must resolve all symbols.',
        'files': files,
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'sdk': str(destination), 'image': IMAGE, 'files': len(files),
                      'libvulkan_sha256': VULKAN_SHA256}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
