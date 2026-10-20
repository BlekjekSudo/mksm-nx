"""Synthetic SD setup checks; no original game, console, or controller is used."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import stage_switch
import setup_switch


class SwitchSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / 'original game'
        self.game.mkdir()
        (self.game / 'default.xbe').write_bytes(b'synthetic executable fixture')
        (self.game / 'textures').mkdir()
        (self.game / 'textures/art.bin').write_bytes(bytes(range(256)) * 17)
        self.nro = self.root / 'fixture.nro'
        self.nro.write_bytes(bytes(16) + b'NRO0' + bytes(64))
        self.receipt = self.root / 'receipt.json'
        self.record = {'build_id': '0123456789abcdef', 'nro_sha256': stage_switch.sha256(self.nro),
                       'translation': {'inputs': {'xbe_sha256': stage_switch.sha256(self.game / 'default.xbe')}}}
        self.receipt.write_text(json.dumps(self.record))
        self.output = self.root / 'sd setup'

    def stage(self, **kwargs):
        return stage_switch.stage_game(self.game, self.nro, self.receipt, self.output, **kwargs)

    def test_copy_has_verified_contents_and_independent_storage(self):
        result = self.stage()
        manifest = stage_switch.verify_stage(result)
        self.assertEqual(manifest['game_files'], 2)
        self.assertTrue(manifest['not_for_redistribution'])
        self.assertEqual(len(manifest['files']), 3)
        app = result / stage_switch.APP
        for name in ('hdd', 'logs', 'cache', 'licenses'):
            self.assertTrue((app / name).is_dir())
        target = app / 'game/textures/art.bin'
        original_hash = stage_switch.sha256(self.game / 'textures/art.bin')
        target.write_bytes(b'changed copy')
        self.assertEqual(stage_switch.sha256(self.game / 'textures/art.bin'), original_hash)
        with self.assertRaisesRegex(ValueError, 'differs'):
            stage_switch.verify_stage(result)

    def test_existing_save_and_stage_are_never_overwritten(self):
        self.output.mkdir()
        save = self.output / 'save.bin'
        save.write_bytes(b'keep this save')
        with self.assertRaises(FileExistsError):
            self.stage()
        self.assertEqual(save.read_bytes(), b'keep this save')

    def test_wrong_nro_is_rejected_before_output_creation(self):
        self.nro.write_bytes(bytes(16) + b'NRO0' + b'wrong build')
        with self.assertRaisesRegex(ValueError, 'NRO differs'):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_wrong_game_is_rejected_before_output_creation(self):
        (self.game / 'default.xbe').write_bytes(b'wrong game')
        with self.assertRaisesRegex(ValueError, 'XBE does not match'):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_output_inside_original_game_is_rejected(self):
        self.output = self.game / 'output'
        with self.assertRaisesRegex(ValueError, 'separate'):
            self.stage()
        self.assertFalse(self.output.exists())

    def test_manifest_cannot_escape_stage(self):
        result = self.stage()
        manifest_path = result / 'staging.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['files'][0]['path'] = '../receipt.json'
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'Invalid staged file'):
            stage_switch.verify_stage(result)

    def test_interrupted_copy_is_never_a_completed_setup(self):
        with patch.object(stage_switch.shutil, 'copy2', side_effect=OSError('injected copy failure')):
            with self.assertRaises(OSError):
                self.stage()
        self.assertTrue((self.output / 'INCOMPLETE.txt').is_file())
        self.assertFalse((self.output / 'staging.json').exists())
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            stage_switch.verify_stage(self.output)

    def test_reuse_pipeline_reaches_stage_without_compiling(self):
        arguments = ['setup_switch.py', '--game', str(self.game), '--reuse-build', '--output', str(self.output)]
        with patch.object(sys, 'argv', arguments), patch.object(setup_switch, 'run') as build, \
                patch.object(setup_switch, 'stage_game', return_value=self.output) as stage:
            setup_switch.main()
        build.assert_not_called()
        self.assertEqual(stage.call_args.args[0], self.game)
        self.assertEqual(stage.call_args.args[3], self.output)


if __name__ == '__main__':
    unittest.main()
