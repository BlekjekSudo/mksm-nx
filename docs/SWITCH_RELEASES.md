# Switch SD releases

Public release ZIPs contain the port NRO, player instructions, license notices and a minimal build record.
They do not contain original game files, generated translation sources, keys, logs or saves.
The archive has the `switch/shaolin-recomp/` path ready to copy to an SD root.
Users supply the complete extracted game in its `game/` directory.

## Prepare the NRO locally

Build with `tools/build_switch.py` or `tools/setup_switch.py` using your original external XBE.
Keep the resulting `build/switch/build-receipt.json` next to the build products.
Commit maintained source changes before releasing.
The package script rejects a build if its runtime source inputs differ from the receipt or committed source.

## Create the tag and draft

Authenticate GitHub CLI as the release maintainer with repository and workflow access.
Run this from a clean checkout with `origin` pointing to the release repository:

```powershell
python tools/publish_switch_release.py --tag switch-v0.1.0-alpha.1 --output dist/switch-v0.1.0-alpha.1
```

Choose a new version for each subsequent release.
The command packages only an explicit list of approved public files, verifies the ZIP, pushes `main`,
creates and pushes the annotated tag, then uploads the ZIP and manifest to a draft release.
It uploads `checksums.txt` last as the completion marker.
The script never uses the asset-populated output from `stage_switch.py`.

The `switch-v*` tag starts **Switch release** in GitHub Actions.
The workflow runs synthetic setup/release tests and checks the public source tree.
It waits for the prepared draft assets and downloads them with the repository token.
It verifies the actual tagged commit, source input hashes, ZIP allowlist, NRO metadata, executable hash and checksums.
After these checks pass, it publishes a regular release and marks it as Latest.
It does not compile or run the game on a hosted runner.

To resume an interrupted draft upload using the same tag and output:

```powershell
python tools/publish_switch_release.py --tag switch-v0.1.0-alpha.1 --output dist/switch-v0.1.0-alpha.1 --resume
```

The resume command requires the existing tag to match the checked-out commit and refuses public releases.
If the tag workflow has timed out, run **Switch release** manually with the same tag after the upload completes.
Do not move a published tag or replace public assets in place.

## Inspect without publishing

```powershell
python tools/package_switch_release.py --tag switch-v0.1.0-alpha.1 --output dist/inspection
python tools/package_switch_release.py --verify --tag switch-v0.1.0-alpha.1 --output dist/inspection
```

Each output contains exactly the SD ZIP, `release-manifest.json` and `checksums.txt`.
The manifest records the required original XBE hash without including the executable.
GitHub supplies source archives for the release tag separately.
Binary validation and a successful workflow do not establish new hardware or campaign testing.
