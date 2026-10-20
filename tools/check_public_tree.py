"""Reject private game data, binaries, generated translations and keys in Git."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'.private', 'assets', 'game', 'gamedata', 'original', 'generated', 'build', 'work',
                'out', 'dist', 'third_party', 'hdd', 'savedata', 'runtime-data', 'cache', 'logs', 'crashes'}
PRIVATE_SUFFIXES = {'.xbe', '.iso', '.xiso', '.sfd', '.wad', '.wae', '.waf', '.wag', '.wah', '.wai',
                    '.waj', '.wak', '.xmv', '.bik', '.nro', '.nsp', '.nso', '.nca', '.npdm', '.nacp',
                    '.elf', '.exe', '.dll', '.o', '.a', '.obj', '.lib', '.pdb', '.dmp', '.keys', '.log',
                    '.zip', '.7z', '.rar', '.tar', '.gz', '.xz'}
SECRET = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,}|'
                    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                    rb'(?m:^\s*(?:header_key|key_area_key_application_[0-9a-f]{2}|master_key_[0-9a-f]{2})'
                    rb'\s*=\s*[0-9a-fA-F]{32,}))')


def prohibited_path(name: str) -> bool:
    path = Path(name)
    return (path.parts[0].lower() in PRIVATE_DIRS or path.suffix.lower() in PRIVATE_SUFFIXES
            or path.name.lower() in {'keys.dat', 'wadcrc.bin'}
            or path.name.lower().startswith('x86_recomp.')
            or (path.name.startswith('.env') and path.name != '.env.example'))


def main() -> None:
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    problems = []
    count = 0
    for name in filter(None, names):
        count += 1
        path = ROOT / name
        if prohibited_path(name):
            problems.append(name + ': private or generated file type')
            continue
        data = path.read_bytes()
        if SECRET.search(data):
            problems.append(name + ': possible credential material')
    # A clean tip is insufficient if an earlier commit contains private payloads.
    history = subprocess.check_output(['git', 'rev-list', '--objects', 'HEAD'], cwd=ROOT, text=True)
    with subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE) as reader:
        for row in history.splitlines():
            object_id, _, name = row.partition(' ')
            if name and prohibited_path(name):
                problems.append(name + ': prohibited path in reachable history')
            reader.stdin.write(object_id.encode() + b'\n')
            reader.stdin.flush()
            _, kind, size = reader.stdout.readline().split()
            content = reader.stdout.read(int(size))
            reader.stdout.read(1)
            if kind == b'blob' and SECRET.search(content):
                problems.append(name + ': possible credential material in reachable history')
        reader.stdin.close()
        if reader.wait():
            raise RuntimeError('Could not inspect reachable Git objects')
    if problems:
        print('\n'.join(sorted(set(problems))), file=sys.stderr)
        raise SystemExit(1)
    print(f'Public source check passed: {count} tracked files; no prohibited paths in reachable history.')


if __name__ == '__main__':
    main()
