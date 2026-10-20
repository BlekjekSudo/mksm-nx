"""Exercise public release boundaries with a synthetic NRO and Git fixture."""
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import package_switch_release as release


def fixture_nro() -> bytes:
    module = bytearray(0x100)
    module[0x10:0x14] = b'NRO0'
    struct.pack_into('<I', module, 0x18, len(module))
    module[0x40:0x50] = b'0123456789abcdef'
    icon = b'\xff\xd8fixture\xff\xd9'
    nacp = bytearray(0x4000)
    title, author = b'Shaolin Monks Recomp', b'ArdRecomp'
    nacp[:len(title)] = title
    nacp[0x200:0x200 + len(author)] = author
    nacp[0x3060:0x3065] = b'0.2.0'
    struct.pack_into('<I', nacp, 0x302c, 1)
    return bytes(module) + b'ASET' + bytes(4) + struct.pack(
        '<6Q', 56, len(icon), 56 + len(icon), len(nacp), 0, 0) + icon + nacp


class SwitchReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = {name: b'fixture\n' for name in release.BUILD_INPUTS}
        files.update({'src/host.cpp': b'// fixture\n', 'LICENSE': b'project license\n',
                      'licenses/dependency.txt': b'dependency notice\n',
                      'resources/switch-game-files.txt': b'Supply your own game.\n',
                      'resources/switch-readme.txt': b'Copy switch/ to SD.\n'})
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for key, value in [('user.name', 'Fixture'), ('user.email', 'fixture@example.invalid')]:
            subprocess.run(['git', '-C', str(self.root), 'config', key, value], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'fixture'], check=True)
        self.nro = self.root / 'build/fixture.nro'
        self.nro.parent.mkdir()
        self.nro.write_bytes(fixture_nro())
        self.receipt = self.root / 'build/receipt.json'
        self.receipt.write_text(json.dumps({'nro_sha256': release.sha256(self.nro.read_bytes()),
            'build_id': '0123456789abcdef', 'translation': {'inputs': {'sdk': release.SDK,
            'xbe_sha256': '1' * 64}}, 'maintained_sources': {
                name: release.sha256((self.root / name).read_bytes())
                for name in release.source_names(self.root)}}))
        self.tag = 'switch-v0.1.0-alpha.1'
        self.output = self.root / 'dist/release'

    def package(self):
        return release.make_package(self.root, self.nro, self.receipt, self.tag, self.output)

    def test_public_zip_has_nro_and_instructions_without_game(self):
        archive = self.package()
        manifest = release.verify_package(self.root, self.output, self.tag)
        self.assertEqual(manifest['nro_metadata']['author'], 'ArdRecomp')
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn(release.APP + 'game/README.txt', bundle.namelist())
            self.assertNotIn(release.APP + 'game/default.xbe', bundle.namelist())
            self.assertFalse(any('/hdd/' in name or '/logs/' in name for name in bundle.namelist()))

    def test_private_game_injection_rejected_even_with_new_checksums(self):
        archive = self.package()
        with zipfile.ZipFile(archive, 'a') as bundle:
            bundle.writestr(release.APP + 'game/default.xbe', b'forbidden fixture')
        path = self.output / 'release-manifest.json'
        manifest = json.loads(path.read_bytes())
        manifest['archive'].update(sha256=release.sha256(archive.read_bytes()), bytes=archive.stat().st_size)
        path.write_bytes(release.json_bytes(manifest))
        (self.output / 'checksums.txt').write_bytes(''.join(
            release.sha256(p.read_bytes()) + '  ' + p.name + '\n' for p in (archive, path)).encode())
        with self.assertRaisesRegex(ValueError, 'unapproved files'):
            release.verify_package(self.root, self.output, self.tag)

    def test_wrong_tag_rejected(self):
        self.package()
        with self.assertRaisesRegex(ValueError, 'tag mismatch'):
            release.verify_package(self.root, self.output, 'switch-v9.9.9')

    def test_changed_runtime_requires_rebuild(self):
        (self.root / 'src/host.cpp').write_bytes(b'// new runtime\n')
        with self.assertRaisesRegex(ValueError, 'Rebuild the NRO'):
            self.package()

    def test_edited_nro_rejected(self):
        self.nro.write_bytes(self.nro.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'differs from its build receipt'):
            self.package()

    def test_existing_release_output_preserved(self):
        self.output.mkdir(parents=True)
        keep = self.output / 'keep.txt'
        keep.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, 'fresh release output'):
            self.package()
        self.assertEqual(keep.read_bytes(), b'preserve')

    def test_embedded_romfs_rejected(self):
        data = bytearray(fixture_nro())
        struct.pack_into('<Q', data, 0x100 + 48, 1)
        with self.assertRaisesRegex(ValueError, 'embedded RomFS'):
            release.inspect_nro(bytes(data))


if __name__ == '__main__':
    unittest.main()
