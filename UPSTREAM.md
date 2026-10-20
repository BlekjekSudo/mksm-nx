# Tools and dependencies

## Switch source lineage

The Switch target is based on [OAleex/ShaolinMonksRecomp](https://github.com/OAleex/ShaolinMonksRecomp),
imported at revision `7d8309098dfe995a7985dc125f286b5585f45b32`.
Its existing source history, project license and dependency notices are preserved.
The Switch integration and local SD setup tooling are maintained in this repository by ArdRecomp.
They do not grant a license to the original Xbox executable or game assets.

Switch dependencies include devkitPro/libnx and [mesa-switch](https://github.com/danfromtico/mesa-switch).
`tools/setup_mesa_sdk.py` verifies an immutable public OCI image and Mesa archive hash before staging the SDK locally.
It does not execute the image or modify the global devkitPro installation.
The native Mesa revision is `0ff52079ca9e9e837ed27eb95a8d239f439a1864`.
Support archives come from the user's installed devkitPro libraries.
SDK output, source translations and local game setups are ignored by Git.

The optional NSP builder uses the hash-pinned Sphaira loader at revision
`72a94b905816de24817594109fb012a8f7107d8c`, based on nx-hbloader and libnx.
Locally generated forwarder packages include that loader's source and GPL/ISC notices.
Forwarder keys are separate user-provided local inputs and never enter packages.

## XboxXBERecomp

The port uses [XboxXBERecomp](https://github.com/TGP482/XboxXBERecomp), copyright 2026 TGP482, under the MIT license.
The supported SDK revision is `40e9c35451305f09ca9d86f677e4b9c2c37ff26b`.

The build script writes adapted SDK files in the build directory.
It preserves the SDK checkout.
The adaptations cover graphics, audio, movies, input, storage, mods, diagnostics and startup.
The controller report mapping derives from this SDK.

The executable includes SDL, Dear ImGui, toml++, miniz, XZ Embedded and Lua.
The launchers use Win32 dialogs or Dear ImGui with Vulkan.
SDL supplies controller input and platform services.
File selection can use SDL dialogs, `zenity` or `kdialog`.
The ImGui launcher uses an installed system font when available.
Supported fonts include Noto Sans, DejaVu Sans, Ubuntu and Liberation Sans.

Dependency notices cover the embedded ImGui font, stb code, SDL's HIDAPI and YUV conversion code.
The recompiler tools use Zydis and Zycore.
The game executable excludes those recompiler libraries.
LLVM and [Ghidra](https://github.com/NationalSecurityAgency/ghidra) are development tools.
Ghidra supplied analysis of the original XBE.
Release packages exclude these development tools.

See the [SDK license](licenses/XboxXBERecomp-MIT.txt) and [dependency notices](licenses/THIRD_PARTY.txt).

## miniz

ZIP imports and language resource decompression use [miniz 3.1.0](https://github.com/richgel999/miniz) under the MIT license.
The build pins revision `174573d60290f447c13a2b1b3405de2b96e27d6c`.
It checks the source download's SHA-256 hash.
See the [miniz license](licenses/miniz-MIT.txt).

The build applies a local fix for ZIP64 entries with extended file offsets.
The adaptation preserves the pinned download. Extraction still checks file bounds and CRC values.

## Vulkan headers and glslang

The Vulkan renderer uses [Khronos Vulkan headers](https://github.com/KhronosGroup/Vulkan-Headers) and [glslang](https://github.com/KhronosGroup/glslang).
glslang compiles HLSL shaders to SPIR-V during play.

| Dependency | Version | Pinned revision |
| --- | --- | --- |
| Vulkan-Headers | `v1.4.321` | `2cd90f9d20df57eac214c148f3aed885372ddcfe` |
| glslang | `15.4.0` | `8a85691a0740d390761a1008b4696f57facd02c4` |

The Direct3D 12 build excludes these dependencies.
SDL opens the system's Vulkan loader at runtime.
The Vulkan renderer follows the SDK's targets, texture cache, pipeline keys and constants.
Its BC texture decoder follows the Khronos Data Format Specification.

Parts of glslang use the Apache License 2.0.
Builds that combine glslang with this project's code use GNU GPL version 3 or later.
The Direct3D 12 build keeps GPL-2.0-or-later.
See the [glslang license](licenses/glslang-LICENSE.txt) and [Vulkan-Headers license](licenses/Vulkan-Headers-MIT.txt).

## Mod packages

The installer uses [XZ Embedded](https://github.com/tukaani-project/xz-embedded) under the 0BSD license.
The build pins tag `v2024-12-30`, revision `ae63ae3a36ed01724674e8f3d750dc47bf125410`.
It checks the source download's SHA-256 hash.
The decoder supports CRC64 and limits the LZMA2 dictionary to 64 MiB.
See the [XZ Embedded license](licenses/xz-embedded-0BSD.txt).

The USTAR reader, JSON reader, SHA-256 implementation and VCDIFF decoder use project code in `src/mods/`.
The VCDIFF decoder follows [RFC 3284](https://www.rfc-editor.org/rfc/rfc3284).
It also supports xdelta3 application headers and Adler-32 checks for each window.
The Krypt release builder uses xdelta3 with `-S none`.
The game applies patches without an xdelta3 executable.

## Routine scripts

Routine mods use [Lua 5.4.9](https://www.lua.org/) under the MIT license.
The build checks the source archive's SHA-256 hash.
The runtime exposes checked guest memory and registers to scripts.
Scripts have memory and instruction limits. Host files, processes and networking are unavailable.
See the [Lua license](licenses/Lua-MIT.txt).

## Downloads

HTTPS downloads use WinHTTP or the system's `libcurl.so.4`.
The runtime opens libcurl dynamically.
The download code checks certificates and restricts redirects to HTTPS.
Release packages do not include libcurl.

## References

Public xemu, Cxbx-Reloaded, nxdk and Microsoft documentation informed behavior and ABI checks.
These references do not imply endorsement of this port.
The source repository excludes game images, assets, generated translations, analysis databases and proprietary SDK files.
