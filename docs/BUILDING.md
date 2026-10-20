# Building

These instructions cover the desktop targets. For the ARM64/libnx port and local SD setup pipeline, see [SWITCH.md](SWITCH.md).

The build script compiles the game, runs the requested tests and creates a release package.
The build uses the same SDK revision, game profile and generated code for each target.

## Required inputs

- Python 3.11 or later and Git.
- The original USA/Europe `default.xbe`, outside the repository.
- One of the toolchains below.

The supported XBE SHA-256 is:

```text
48240902bf4a04eb89376217c529531032d3370749bcce91a194878cc49db74a
```

## Toolchains

### Visual Studio

Use this toolchain for the Direct3D 12 executable.

- Visual Studio 2022 or later with the C++ workload and Windows SDK.
- Visual Studio's CMake and Ninja tools.
- LLVM 21.1.8 with Clang and LLD.

### System Clang

Use this toolchain for the Vulkan executable on Linux or in WSL.

- CMake 3.25 or later.
- Clang 18 or later, LLD and Ninja.
- X11, Wayland and audio development headers.

Install the required packages on Ubuntu or Debian:

```sh
sudo apt install clang lld cmake ninja-build git python3 pkg-config libx11-dev libxext-dev libxrandr-dev \
    libxcursor-dev libxfixes-dev libxi-dev libxss-dev libxtst-dev libxkbcommon-dev libwayland-dev \
    wayland-protocols libdecor-0-dev libasound2-dev libpulse-dev libpipewire-0.3-dev libdbus-1-dev \
    libudev-dev libusb-1.0-0-dev
```

The script reports missing headers before compilation.
The executable includes the C++ runtime.
The script prints the minimum glibc version required by the compiled executable.
Check that value before you distribute the package.

## SDK

Run these commands from the repository root:

```sh
git clone https://github.com/TGP482/XboxXBERecomp.git third_party/XboxXBERecomp
git -C third_party/XboxXBERecomp checkout 40e9c35451305f09ca9d86f677e4b9c2c37ff26b
```

## Compile and test

Set the paths below to your local files.

For the Visual Studio toolchain:

```powershell
python tools/build_recomp.py --xbe "C:/Games/ShaolinMonks/default.xbe" --sdk third_party/XboxXBERecomp --llvm "C:/Program Files/LLVM" --test
```

For the system Clang toolchain:

```sh
python3 tools/build_recomp.py --xbe ~/Games/ShaolinMonks/default.xbe --sdk third_party/XboxXBERecomp --test
```

The script downloads the pinned dependencies, including Lua for routine mods.
It loads the Visual Studio environment when that toolchain requires it.
The `--test` option requires passing tests before packaging.
Build logs use the repository's `build/` directory.

| Target | Executable | Package |
| --- | --- | --- |
| Direct3D 12 | `build/release/ShaolinMonksRecomp.exe` | `dist/ShaolinMonksRecomp-<version>-Windows-x64.zip` |
| Vulkan | `build/linux-release/ShaolinMonksRecomp` | `dist/ShaolinMonksRecomp-<version>-Linux-x64.tar.gz` |

When the source uses a mounted drive under `/mnt/`, build output uses `~/.cache/shaolin-monks-recomp/linux-release`.
The packages still use the repository's `dist/` directory.
Each successful build also writes `checksums.txt`.

| Option | Effect |
| --- | --- |
| `--configure-only` | Configure without compilation or packaging |
| `--jobs 2` | Limit compilation to two concurrent processes |
| `--build-dir <path>` | Select another build directory |
| `--disc <path>` | Enable real-image startup tests for the Direct3D 12 build |
| `--linux` | Also build the Vulkan package through WSL after the Direct3D 12 package |
| `--wsl-distro Ubuntu-24.04` | Select the WSL distribution |

## Additional package through WSL

Install WSL from PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

1. Restart the computer if the installer requests it.
2. Open Ubuntu from the Start menu.
3. Create the requested user account.
4. Install the packages listed under **System Clang**.
5. Add `--linux` to the build command.

```powershell
python tools/build_recomp.py --xbe "C:/Games/ShaolinMonks/default.xbe" --sdk third_party/XboxXBERecomp --llvm "C:/Program Files/LLVM" --linux --test
```

WSL reads the source, SDK and game executable through the mounted drive.
Its build output uses `~/.cache/shaolin-monks-recomp/linux-release`.
If this step fails, the first package remains in `dist/`.
Test the Vulkan package on a system with a working Vulkan driver.

## Validation and build files

Automated tests use isolated data and saves.
Native dialog tests use a private desktop.
Real-image checks read the original game without changing it.
The manual menu and Ko-op scripts in `tests/` accept `--help`.

Close the game and compiler before you remove a build directory.
Preserve any custom files in that directory.
Keep game files, the SDK and LLVM outside `build/`.

## Release package

To create a package without compilation:

```powershell
python tools/package_release.py --exe build/release/ShaolinMonksRecomp.exe --version 0.2.0
python tools/package_release.py --exe build/linux-release/ShaolinMonksRecomp --version 0.2.0
```

Set `--exe` to the compiled executable's path.
The packager selects the format from the executable.
Each package contains the executable, `readme.txt` and `licenses/`.
The `.tar.gz` places those files inside one `ShaolinMonksRecomp` folder.
The project license uses `licenses/LICENSE.txt`.

The explicit package list excludes game files, mods, logs, saves and settings.
Keep `README.md`, `UPSTREAM.md`, `AGENTS.md` and `docs/` in the source repository.
Check package contents before publication.
Provide the corresponding source and required dependency notices with a binary release.
The project license does not grant rights to the original game or generated translations.

See [language packs](LANGUAGE_PACKS.md) for mod authoring and package instructions.
See [general mods](MODS.md) for resource formats, dependencies and Lua routine hooks.
Installed packs use the data folder's `mods/languages/` directory.

## Source layout

| Directory | Contents |
| --- | --- |
| `src/` | Host integration and compatibility code |
| `src/mods/` | Catalog, downloads, package installation and mod state |
| `src/linux/` | SDL platform services and the Vulkan renderer |
| `cmake/` | Shared modules and target build configuration |
| `config/` | Executable profile and ABI metadata |
| `resources/` | Application resources and player instructions |
| `tests/` | Automated and manual checks |
| `tools/` | Build, SDK adaptation and packaging scripts |
| `docs/` | User and developer instructions |
| `licenses/` | Dependency licenses and notices |
