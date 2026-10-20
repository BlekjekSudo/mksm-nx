"""Observe the actual Switch game in one isolated Ryujinx process, without input."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''): h.update(block)
    return h.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator', type=Path, required=True)
    parser.add_argument('--game-dir', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--show-window', action='store_true')
    parser.add_argument('--capture', action='store_true', help='Read back actual guest front targets; affects timing')
    parser.add_argument('--gdb-port', type=int, help='Enable a guest debugger for this isolated process')
    parser.add_argument('--thread-selftest', action='store_true', help='Check native detached-thread cleanup before boot')
    parser.add_argument('--descriptor-sets', action='store_true', help='Compare ordinary descriptor sets with native push descriptors')
    parser.add_argument('--nvk-debug', help='Explicit native NVK diagnostics for this isolated test')
    args = parser.parse_args()
    receipt = json.loads((ROOT / 'build/switch/build-receipt.json').read_text())
    nro = ROOT / 'build/switch/arm64/ShaolinMonksRecomp.nro'
    assert digest(nro) == receipt['nro_sha256']
    game = args.game_dir.resolve()
    assert digest(game / 'default.xbe') == receipt['translation']['inputs']['xbe_sha256']
    run = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    output = ROOT / '.private/ryujinx' / run
    profile = output / 'profile'
    runtime = profile / 'sdcard/switch/shaolin-recomp'
    runtime.mkdir(parents=True)
    if args.capture: (runtime / 'capture.flag').touch()
    if args.thread_selftest: (runtime / 'thread-selftest.flag').touch()
    if args.descriptor_sets: (runtime / 'descriptor-sets.flag').touch()
    if args.nvk_debug: (runtime / 'nvk-debug.txt').write_text(args.nvk_debug + '\n')
    # Keep emulator writes away from original assets, even if guest file-open
    # semantics are faulty. Subsequent runs reuse only these private copies.
    shadow = ROOT / '.private/test-game-copy'
    if not shadow.exists():
        print('Copying game into private test storage (original files remain untouched).', flush=True)
        shutil.copytree(game, shadow)
    for original in game.rglob('*'):
        if original.is_file():
            copied = shadow / original.relative_to(game)
            if not copied.is_file() or copied.stat().st_size != original.stat().st_size:
                raise RuntimeError(f'Incomplete private asset copy: {copied}')
    if digest(shadow / 'default.xbe') != receipt['translation']['inputs']['xbe_sha256']:
        raise RuntimeError('Private test executable differs from the original')
    for source in shadow.rglob('*'):
        target = runtime / 'game' / source.relative_to(shadow)
        if source.is_dir(): target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
    staged = runtime / nro.name
    shutil.copy2(nro, staged)
    normal = Path(os.environ['APPDATA']) / 'Ryujinx/Config.json'
    original_config = digest(normal) if normal.exists() else None
    command = [str(args.emulator.resolve()), '--no-gui', '--root-data-dir', str(profile),
               '--disable-main-input-config', '--disable-file-logging', '--disable-stub-logs',
               '--disable-docked-mode', '--audio-volume', '0', '--memory-manager-mode', 'HostMappedUnsafe',
               '--graphics-backend', 'Vulkan', '--resolution-scale', '1', '--vsync-mode', 'Switch', str(staged)]
    if args.gdb_port:
        if not 1024 <= args.gdb_port < 65536: parser.error('Invalid debugger port')
        command[1:1] = ['--enable-gdb-stub', '--gdb-stub-port', str(args.gdb_port)]
    report = {'run': run, 'build_id': receipt['build_id'], 'nro_sha256': receipt['nro_sha256'],
              'command': command, 'runtime': str(runtime), 'input_sent': False, 'game_frame_verified': False}
    report['emulator_hle_sha256'] = digest(args.emulator.resolve().parent / 'Ryujinx.HLE.dll')
    report['started_utc'] = datetime.now(timezone.utc).isoformat()
    startup = None
    if os.name == 'nt' and not args.show_window:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    with (output / 'stdout.log').open('wb') as stdout, (output / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command, cwd=output, stdout=stdout, stderr=stderr, startupinfo=startup)
        report['owned_pid'] = process.pid
        (output / 'report.json').write_text(json.dumps(report, indent=2))
        (ROOT / '.private/ryujinx/latest.json').write_text(json.dumps(report, indent=2))
        print(f'Observing PID {process.pid}: {output}', flush=True)
        deadline = time.monotonic() + args.seconds
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if (output / 'stop-requested.txt').exists(): break
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                report['controlled_stop'] = True
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        report['exit_code'] = process.returncode
    for file in (runtime / 'logs').glob('*'):
        if file.is_file(): shutil.copy2(file, output / file.name)
        elif file.is_dir(): shutil.copytree(file, output / file.name)
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['normal_config_unchanged'] = original_config == (digest(normal) if normal.exists() else None)
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    (ROOT / '.private/ryujinx/latest.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)

if __name__ == '__main__': main()
