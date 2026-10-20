# Experimental Switch target

This target recompiles the original Xbox executable to ARM64 with the pinned XboxXBERecomp SDK.
It uses this project's Vulkan renderer, native VI presentation, libnx controller snapshots and libnx audio output.
Development testing has reached Goro's Lair in Ryujinx and on a physical Switch.
The hardware tester reported improved game speed with the 60 FPS host limit.
Stable performance across the game, full campaign compatibility and audible-output correctness are not established by these reports.

## Build prerequisites

- devkitPro with devkitA64, libnx, CMake and Ninja.
- Host Clang with C++20 support.
- Python 3.11 or later.
- A native switch-mesa SDK containing Vulkan headers, static libraries and a verified `manifest.json`.
- Your original external game executable and complete game files.

Clone these dependencies into the ignored `third_party/` directory:

| Directory | Repository | Revision |
| --- | --- | --- |
| `XboxXBERecomp` | https://github.com/TGP482/XboxXBERecomp | `40e9c35451305f09ca9d86f677e4b9c2c37ff26b` |
| `zydis` | https://github.com/zyantific/zydis | `v4.1.1`, with its Zycore submodule |
| `tomlplusplus` | https://github.com/marzer/tomlplusplus | `v3.4.0` |
| `imgui` | https://github.com/ocornut/imgui | `v1.91.0` |
| `glslang` | https://github.com/KhronosGroup/glslang | `8a85691a0740d390761a1008b4696f57facd02c4` |

Run the build from the repository root:

```powershell
python tools/build_switch.py --xbe "<external default.xbe>" --nro --mesa-sdk "<verified Switch Mesa SDK>" --jobs 4
```

For the complete local dependency/build/SD setup pipeline, run:

```powershell
python tools/setup_switch.py --game "<complete extracted game folder>" --fetch-deps --fetch-mesa --fetch-icon --jobs 4
```

The pipeline needs the installed toolchain and support libraries listed in the README.
`--fetch-icon` also needs Pillow. All original game inputs remain local.
The default staging mode copies and hashes every asset. Existing output folders are rejected.
Use `--output` to select a fresh folder, or `--reuse-build` to stage an existing NRO without compilation.
`tools/stage_switch.py --verify "<setup folder>"` checks the completed local manifest.
`--hardlink` is reserved for local development and shares original file storage.

The Windows host script uses the devkitPro MSYS CMake and Clang tools.
It preserves the SDK checkout and original XBE.
Generated translations, adapted SDK sources and build products stay under `build/switch/`.
The translation receipt records the XBE, SDK and profile identities.
`--skip-codegen` reuses a translation only when those identities match.
`--icon` embeds an external 256 by 256 JPEG without adding the artwork to the source tree.
The NRO author is `ArdRecomp`.
Omit `--nro` to compile the ARM64 guest and host-core archives alone.
Archives are development outputs, not a bootable game.

The upstream-audited XBE hash remains unchanged in the desktop build.
The Switch builder permits an experimental variant and prints a warning.
Matching title and entry addresses do not prove all ABI overrides compatible.
The NRO verifies that its runtime XBE matches the executable used for translation.

## Runtime layout

```text
sdmc:/switch/shaolin-recomp/
    ShaolinMonksRecomp.nro
    game/default.xbe
    game/...complete original files...
    hdd/
    cache/
    logs/runtime.log
```

Use title takeover for full application memory.
The optional NSP forwarder uses the same fixed NRO and data paths.
Preserve `hdd/` when replacing the NRO.
The original game menus control gameplay options.
Desktop launchers, mod installation, language packs, rumble and secondary game executables are not integrated into this target.
The framebuffer renders at the original guest resolution and is presented at the display resolution.
The host frame limiter targets 60 FPS, matching the desktop target and the guest's 60 Hz vertical blank.
The original game clocks and simulation code are unchanged. A 60 FPS limit does not establish hardware performance.
The log reports guest swap frequency and time spent in the host limiter every five seconds.

The default controller layout follows PS2 button positions:

| Switch button | PS2 function | Xbox report |
| --- | --- | --- |
| Y | Square | X |
| B | Cross | A |
| X | Triangle | Y |
| A | Circle | B |
| ZR | R1 | Black |
| ZL | L1 | White |
| R | R2 | Right trigger |
| L | L2 | Left trigger |

Plus, Minus, the directional pad and both sticks retain their existing functions.

Guest memory retains the SDK's direct `base + address` ABI through a 4 GiB virtual reservation.
Only committed chunks allocate physical backing.
The physical RAM and write-combined windows must alias the same bytes.
The loader must permit code-memory services and mapping the process's own memory.
When the loader omits its optional process handle, the runtime obtains one through a local HIPC handle copy.
Physical RAM uses writable module-code memory before creating its second process-memory view.
Other guest ranges use the same module-code mapping mechanism, grouping consecutive committed chunks.
This avoids allocating a kernel code-memory object for every 64 KiB chunk.
Kernel code-memory objects are limited to ten system-wide in the Atmosphere kernel.
See the [kernel resource counts](https://github.com/Atmosphere-NX/Atmosphere/blob/master/libraries/libmesosphere/include/mesosphere/init/kern_init_slab_setup.hpp).
Failed image commitments stop startup before the loader copies any section data.
Virtual, system and stack allocations report commitment failures without returning unmapped memory.
Guest file reads and writes pass through an aligned native 256 KiB staging buffer before copying to or from guest memory.
This keeps guest aliases out of filesystem IPC and avoids the service's small-buffer fallback.
The same staging applies to positioned disc reads and persisted launch/display data.
Extracted disc assets open with read access, including names resolved through device links.
Open-file metadata and size changes use the existing descriptor instead of reopening its path.
This avoids Horizon sharing conflicts when a file is open for writing.
Metadata failures return an I/O error rather than a successful size of `UINT64_MAX`.
Release logs retain bounded file-open/read events and include paths, offsets, `errno` and native FS results on failures.
Startup checks bidirectional writes through both physical windows.
Startup fails explicitly when these mappings are unavailable.
Decommitted subranges are zeroed; backing chunks remain allocated for reuse until process exit.
Page protection and stack guard faults are not implemented by this backing allocator.

Audio uses the upstream DirectSound mixer with six device-owned 10 ms buffers.
A worker replenishes released buffers independently of game/video decoding.
Native workers can run on the loader-permitted application CPU cores without changing console clocks.
Detached pthreads use join workers and a reaper to release completed thread handles, stacks and TLS.
The Vulkan backend selects push descriptors when the device advertises support and retains a descriptor-set fallback.
Compiled SPIR-V is cached under `cache/vulkan_spirv_v1/` using the generated shader source, stage and entry point.
Cache entries have bounded sizes, instruction-length checks and a payload checksum.
Missing or damaged entries compile again. The first encounter with a shader still requires compilation.
The driver pipeline cache remains separate. Bump the SPIR-V directory version when updating glslang or its options.

The optional emulator patch `patches/ryujinx/nvk-depth-texture-format.patch` recognizes Mesa's
`0x2512A` and `0x2512B` depth/stencil descriptors without falling back to RGBA8.
It changes the emulator's format table, not the game's render targets or Switch driver.
`tests/ryujinx_depth_format_test.cs` checks the actual deployed GPU assembly through reflection.
Pass the emulator directory and `before` or `after` to its test executable.
Visual shadow correctness still requires checking the affected gameplay scene.

## Isolated emulator checks

Build the NRO before running the observer:

```powershell
python tools/test_switch_boot.py --emulator "<Ryujinx executable>" --game-dir "<original extracted game folder>" --seconds 180 --thread-selftest
```

The observer copies the original files to private test storage and uses an isolated profile.
It mutes output and sends no controller input.
`--thread-selftest` checks completion and cleanup of 64 native threads before boot.
`--capture` reads back a few original front targets and affects timing.
`--descriptor-sets` selects the fallback for comparison.
`--gdb-port` enables a debugger for the isolated guest.
Record visual checks separately from timing and audible-output checks.

## Focused validation

```powershell
python tests/switch_memory_abi_test.py --compiler "<devkitPro>/devkitA64/bin/aarch64-none-elf-g++.exe"
```

This test needs Unicorn and pyelftools in the host Python environment.
It executes compiled ARM64 memory accesses and scalar ABI calls across sixteen alignments.
It checks neighbouring bytes, integer and floating-point loads/stores and compound assignments.
It does not validate Horizon services, the GPU, audible output or game performance.

The host CTest suite also compiles the Switch allocator against a synthetic kernel with a ten-object code-memory limit.
It covers image commitments, sparse ranges, physical aliases, overlap, partial clearing and injected mapping failures.
A separate check compiles the prepared SDK allocator and verifies that failed commitments stop image loading
and return allocation failures without writing through an unmapped pointer.
These kernel-model checks do not replace testing on a physical console.

`tests/switch_movie_kernel_test.py` compares the original x86 movie transform at `0x00306530` against its actual compiled ARM64 translation.
It checks all general registers, XMM registers and output/scratch/stack bytes across 320 fixtures.
The test accepts the audited development XBE hash shown in the script.
Generated game code remains in ignored build output.
Generated code compiles with floating-point contraction disabled to preserve separate x86 rounding steps.

The existing project and dependency notices apply.
The Vulkan target combines glslang with project code under GPL version 3 or later, as described in `UPSTREAM.md`.
