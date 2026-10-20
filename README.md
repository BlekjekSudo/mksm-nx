<p align="center">
  <img src="https://cdn2.steamgriddb.com/icon_thumb/1b3ecca102b57d735ac385a9c03fd15b.png" width="128" height="128" alt="Mortal Kombat: Shaolin Monks icon">
</p>

<h1 align="center">Shaolin Monks Recomp for Nintendo Switch</h1>
<p align="center">An experimental ARM64 recompilation port of the original Xbox game.<br>Switch port by <strong>ArdRecomp</strong> · Repository: <strong>mksm-nx</strong></p>

<p align="center">
  <a href="https://github.com/BlekjekSudo/mksm-nx/actions/workflows/source-checks.yml"><img src="https://github.com/BlekjekSudo/mksm-nx/actions/workflows/source-checks.yml/badge.svg" alt="Source checks"></a>
  <img src="https://img.shields.io/badge/target-ARM64%20%2F%20libnx-blue" alt="ARM64 and libnx">
  <img src="https://img.shields.io/badge/graphics-Vulkan%20%2F%20switch--mesa-7358db" alt="Vulkan and switch-mesa">
  <a href="LICENSE"><img src="https://img.shields.io/badge/source-GPL--2.0--or--later-green" alt="Source license"></a>
</p>

> [!IMPORTANT]
> **No original game files are provided.** You must supply your own legally obtained, complete Xbox copy of **Mortal Kombat: Shaolin Monks**.
> This repository does not distribute XBE/ISO files, movies, audio, WAD archives, generated game translations, or a prebuilt game NRO.
> The setup pipeline runs locally against your files. Its SD-card output contains your private game data and must not be uploaded or redistributed.

This Switch target is based on [OAleex/ShaolinMonksRecomp](https://github.com/OAleex/ShaolinMonksRecomp) and the pinned [XboxXBERecomp](https://github.com/TGP482/XboxXBERecomp) SDK. The original x86 game executable is statically translated into C++ and compiled to **native ARM64**. Switch host code supplies graphics, audio, input, storage and memory services.

## Current status

| Area | Status |
| --- | --- |
| Startup and movies | Tested through the opening sequences during development |
| Gameplay | Goro's Lair reached in Ryujinx and reported working on a physical Switch |
| Frame limit | 60 FPS host cap; original guest clocks and simulation constants retained |
| Performance | Removing the earlier 30 FPS cap improved reported game speed; stable 60 FPS across the game is **not verified** |
| Full campaign / Ko-op | Not validated for this Switch target |
| NSP forwarder | Package structure, icon, title and path validated; installation and boot on hardware remain untested |

The desktop upstream's campaign reports do not establish Switch compatibility. Expect remaining visual, audio, timing or compatibility issues. Reports are most useful with the NRO build ID, scene, console clocks and logs.

## What the Switch port adds

- ARM64/libnx runtime integration with a sparse 4 GiB guest address reservation and aliased Xbox physical-memory windows.
- Native switch-mesa/NVK Vulkan rendering and VI presentation, with shader and pipeline caching.
- Native audio output with a worker that replenishes device-owned buffers independently of game decoding.
- Read-only disc access, aligned filesystem transfers and descriptor-based metadata queries for Horizon file-sharing behavior.
- Thread cleanup and explicit propagation of guest-memory allocation failures.
- PS2-style button positions and exchanged shoulder/trigger functions.
- NRO metadata with author **ArdRecomp** and an optional external icon.
- Local build receipts, checksums and an SD setup pipeline that preserves existing outputs and user saves.
- Optional **MK Shaolin Monk** NSP forwarder tooling and a separate Ryujinx depth-format compatibility patch.

## Build your local SD setup

### Prerequisites

- Python 3.11 or later and Git.
- devkitPro with devkitA64, libnx and Switch support libraries: zlib, zstd and expat.
- CMake 3.25 or later, Ninja and host Clang with C++20 support.
- Your complete extracted Xbox game folder, including `default.xbe`.
- Pillow only if you use `--fetch-icon` (`python -m pip install Pillow`).

Use the devkitPro MSYS environment/tool paths for the supported Windows build workflow. The runtime is compiled with devkitA64; the recompiler uses host Clang. See [Switch build details](docs/SWITCH.md) for pinned dependencies and validation limits.

```powershell
git clone https://github.com/BlekjekSudo/mksm-nx.git
cd mksm-nx
python tools/setup_switch.py --game "<your complete extracted Xbox game folder>" --fetch-deps --fetch-mesa --fetch-icon --jobs 4
```

The command:

1. Fetches the pinned public source dependencies without replacing existing checkouts.
2. Prepares a checksum-verified native Mesa SDK under the ignored `.private/` directory.
3. Downloads the requested icon into private build storage and verifies its checksum.
4. Generates the game translation from **your** `default.xbe` and compiles the NRO.
5. Copies your original game files into a fresh local SD layout and checks every copied asset's SHA-256.

It does not download a game, install system toolchains, copy files onto a connected Switch or publish anything.

The output is:

```text
out/switch-sd/<build-id>/
├── staging.json
└── switch/
    └── shaolin-recomp/
        ├── ShaolinMonksRecomp.nro
        ├── build-receipt.json
        ├── checksums.txt
        ├── readme.txt
        ├── licenses/
        ├── game/                  # Your original game files
        ├── hdd/                   # Saves and game settings
        ├── cache/
        └── logs/
```

Copy the generated **`switch/` folder** to the root of your SD card. Launch the NRO through hbmenu using **title takeover** for full application memory. Applet mode is insufficient.

For later builds, use `--skip-codegen` to reuse a matching translation. Use `--output "<new folder>"` when an output for that build ID already exists. To stage an existing verified NRO without compiling again:

```powershell
python tools/setup_switch.py --game "<your complete extracted game folder>" --reuse-build --output "<new local SD setup folder>"
python tools/stage_switch.py --verify "<local SD setup folder>"
```

Existing outputs are never overwritten. When updating your installed copy, **replace only the NRO and preserve `hdd/`**. Keep your existing `game/` files. Do not replace an installed save folder with the empty folder from a fresh setup.

You can use an existing verified SDK with `--mesa-sdk "<SDK folder>"` and your own 256×256 RGB JPEG with `--icon "<icon.jpg>"`. Omit `--fetch-icon` to use the toolchain's default icon. Experimental XBE variants receive a warning; the runtime XBE must match the one used to generate the translation.

## Controls

The game continues to show its original Xbox prompts.

| Switch | PS2 position / function | Xbox report |
| --- | --- | --- |
| Y | Square | X |
| B | Cross | A |
| X | Triangle | Y |
| A | Circle | B |
| ZR | R1 | Black |
| ZL | L1 | White |
| R | R2 | Right trigger |
| L | L2 | Left trigger |

Plus, Minus, the directional pad and both sticks retain their normal functions. Brightness and gameplay settings remain in the original game's options.

## Optional NSP forwarder

The local forwarder is displayed as **MK Shaolin Monk**. It launches:

```text
sdmc:/switch/shaolin-recomp/ShaolinMonksRecomp.nro
```

The NSP contains neither the NRO nor game data. Keep the NRO at that path; future NRO updates do not require rebuilding the forwarder. [Forwarder build instructions](docs/SWITCH_FORWARDER.md) describe the explicit local inputs. Packaging keys are never provided or committed.

## Validation and reporting

Public [source checks](https://github.com/BlekjekSudo/mksm-nx/actions) use synthetic setup fixtures and inspect Git for private/generated files. They do not receive original game data or produce game binaries.

The local host checks cover memory aliases, allocation failures, filesystem I/O, file metadata and SPIR-V cache handling. Additional ABI/movie checks use a user-supplied executable locally. These tests do not replace physical-console gameplay testing.

Include the build ID, scene, Switch model/operating mode and relevant `logs/runtime.log`, `logs/native-stderr.log` or `logs/exception.txt` when reporting an issue. Remove personal paths before sharing logs. Avoid uploading game files, saves or keys.

## Credits and licenses

- **OAleex / ShaolinMonksRecomp** — upstream game port and host integration. Its source history and notices are retained.
- **TGP482 / XboxXBERecomp** — static recompiler and Xbox runtime.
- **devkitPro / libnx** — Switch toolchain and platform services.
- **danfromtico / mesa-switch**, Mesa/NVK contributors and the pinned **autorunhq/switch-dev** SDK image — native Vulkan support.
- **Khronos glslang and Vulkan-Headers**, **Dear ImGui**, **Zydis/Zycore** and **toml++** — graphics and build dependencies.
- **Sphaira / nx-hbloader / libnx** — optional forwarder loader; its separate source and notices accompany locally built forwarders.
- Requested icon artwork: [SteamGridDB-hosted image](https://cdn2.steamgriddb.com/icon_thumb/1b3ecca102b57d735ac385a9c03fd15b.png). It is not covered by the source-code license.

Project source retains **GPL-2.0-or-later**. The Vulkan build combines dependencies under **GPL version 3 or later**; the optional forwarder has its own notices. See [LICENSE](LICENSE), [UPSTREAM.md](UPSTREAM.md) and [licenses/](licenses/).

Original game code, files, names, trademarks and artwork belong to their respective owners. This project is not affiliated with or endorsed by the owners of Mortal Kombat, Xbox or Nintendo Switch.

## AI assistance

Development has used AI assistance for analysis, code changes, debugging, tests, build tooling and documentation. Automated checks, developer observations and user-reported hardware results are distinct; none establishes full campaign compatibility by itself.

Desktop development documentation remains in [docs/BUILDING.md](docs/BUILDING.md). For the original desktop user experience, consult the [upstream project](https://github.com/OAleex/ShaolinMonksRecomp).
