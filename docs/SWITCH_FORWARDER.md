# Optional Switch forwarder

The NSP displays **MK Shaolin Monk**, with author **ArdRecomp** and title ID `0559208E2C6F4000`.
It forwards to `sdmc:/switch/shaolin-recomp/ShaolinMonksRecomp.nro`.
It contains no game NRO or original game data.

The builder requires explicit local paths for:

- The Sphaira forwarder loader at revision `72a94b905816de24817594109fb012a8f7107d8c`.
- The loader's Sphaira, nx-hbloader and libnx license texts.
- A 256 by 256 RGB JPEG icon.
- A locally supplied packaging keyset.
- devkitPro with devkitA64, libnx and Switch tools.
- A local hacBrewPack executable with its independently recorded SHA-256.
- Python with Pillow and cryptography.

See the pinned loader source in [Sphaira](https://github.com/ITotalJustice/sphaira/tree/72a94b905816de24817594109fb012a8f7107d8c).
Use that checkout's `hbl/` directory for `--loader`. It contains the hash-pinned `source/main.c` and `source/trampoline.s`.
The repository supplies the separate hash-pinned NPDM template in `switch/forwarder/base.json`.
The license directory must contain `Sphaira-GPL-3.0.txt`, `nx-hbloader-ISC.txt` and `libnx-ISC.txt`.
The script checks the input hashes before compiling.

```powershell
python tools/package_switch_forwarder.py --loader "<pinned loader directory>" --licenses "<loader license directory>" --icon "<external icon.jpg>" --keys "<local keyset>" --devkitpro "<devkitPro directory>" --packer "<hacBrewPack executable>" --packer-sha256 "<verified SHA-256>" --work ".private/forwarder-work" --output "out/nsp/MK-Shaolin-Monk-forwarder"
```

On Windows, the Linux hacBrewPack executable runs through WSL. `--wsl-distro` selects the distribution.
Use fresh work and output directories. Existing directories are rejected.
No key files are copied into the output; packer output is suppressed to avoid exposing keys.

The resulting folder contains the NSP, build receipt, icon, loader source and required notices.
Validation inspects actual embedded NACP, icon, NRO path/argv, NPDM capabilities, NCA hashes and CNMT references.
The loader uses full application memory and a standard 39-bit address space for the runtime's sparse guest reservation.
These static checks do not establish installation or successful boot on a physical console.

Keep the external NRO and existing `game/` and `hdd/` folders on the SD card.
Update the NRO at the same path without reinstalling the forwarder.
