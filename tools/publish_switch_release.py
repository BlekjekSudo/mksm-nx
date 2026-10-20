"""Prepare a draft SD release and push its tag for GitHub Actions to verify/publish."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from package_switch_release import ROOT, check_tag, git, make_package, require, verify_package


def run(*args: object) -> None:
    subprocess.run([str(value) for value in args], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', default='BlekjekSudo/mksm-nx')
    parser.add_argument('--resume', action='store_true', help='Resume uploading an existing matching draft')
    args = parser.parse_args()
    check_tag(args.tag)
    require(not git(ROOT, 'status', '--porcelain').strip(), 'Commit maintained source changes before releasing')
    require(git(ROOT, 'remote', 'get-url', 'origin').decode().strip()
            in ('https://github.com/' + args.repo + '.git', 'git@github.com:' + args.repo + '.git'),
            'Origin must match the requested release repository')
    output = args.output.resolve()
    if args.resume:
        verify_package(ROOT, output, args.tag)
        require(git(ROOT, 'rev-parse', args.tag + '^{commit}') == git(ROOT, 'rev-parse', 'HEAD'),
                'Existing tag does not match HEAD')
        draft = json.loads(subprocess.check_output(
            ['gh', 'release', 'view', args.tag, '--repo', args.repo, '--json', 'isDraft'], cwd=ROOT))
        require(draft['isDraft'], 'The release is already public; assets will not be replaced')
    else:
        require(not git(ROOT, 'tag', '--list', args.tag).strip(), 'Tag exists; use --resume for a matching draft')
        make_package(ROOT, ROOT / 'build/switch/arm64/ShaolinMonksRecomp.nro',
                     ROOT / 'build/switch/build-receipt.json', args.tag, output)
        run('git', 'push', 'origin', 'HEAD:refs/heads/main')
        run('git', 'tag', '-a', args.tag, '-m', 'Experimental Switch SD release ' + args.tag)
        run('git', 'push', 'origin', 'refs/tags/' + args.tag)
        manifest = json.loads((output / 'release-manifest.json').read_bytes())
        notes = ('## Experimental Switch SD package\n\n'
                 'Download the **sd-card.zip**, extract it, and copy its `switch/` folder to the SD root.\n\n'
                 '**Original game files are not included.** Put your complete legally obtained Xbox game '
                 'files in `switch/shaolin-recomp/game/`. The required `default.xbe` SHA-256 is:\n\n'
                 '`' + manifest['xbe_sha256'] + '`\n\n'
                 'For a different executable variant, build locally from your own XBE. '
                 'Use hbmenu title takeover for full application memory. Preserve existing `hdd/` saves '
                 'when updating. The NSP forwarder is optional and is not included in this asset.\n\n'
                 'NRO author: **ArdRecomp**. Build ID: `' + manifest['build_id'] + '`. '
                 'The 60 FPS host cap is enabled. Full campaign compatibility and sustained 60 FPS '
                 'on hardware are not established.\n\n'
                 'GitHub Actions verifies the ZIP allowlist, NRO metadata, tagged source provenance and '
                 'checksums before publishing this release. It does not run the game.\n')
        with tempfile.TemporaryDirectory(prefix='shaolin-release-notes-') as temp:
            note_path = Path(temp) / 'notes.md'
            note_path.write_text(notes)
            run('gh', 'release', 'create', args.tag, '--repo', args.repo, '--verify-tag', '--draft',
                '--prerelease=false', '--title', 'Shaolin Monks Recomp for Switch ' + args.tag,
                '--notes-file', note_path)
    manifest = json.loads((output / 'release-manifest.json').read_bytes())
    # Upload the readiness checksum last so CI cannot publish a partial upload.
    run('gh', 'release', 'upload', args.tag, '--repo', args.repo, '--clobber',
        output / manifest['archive']['name'], output / 'release-manifest.json')
    run('gh', 'release', 'upload', args.tag, '--repo', args.repo, '--clobber', output / 'checksums.txt')
    print('Draft assets uploaded. The Switch release workflow will verify and publish them.')


if __name__ == '__main__':
    main()
