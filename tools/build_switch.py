"""Generate the original game with XboxXBERecomp and compile for ARM64/libnx."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SDK_REVISION = "40e9c35451305f09ca9d86f677e4b9c2c37ff26b"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, **kwargs):
    cmake = args[0] == "cmake"
    subprocess.run([posix_path(a) if cmake and isinstance(a, Path) else str(a) for a in args],
                   check=True, cwd=ROOT, **kwargs)


def posix_path(path):
    path = Path(path).resolve()
    if os.name == "nt":
        return "/" + path.drive[0].lower() + path.as_posix()[2:]
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xbe", type=Path, required=True)
    default_devkit = Path(os.environ.get("DEVKITPRO", "/opt/devkitpro"))
    if os.name == "nt" and not default_devkit.is_dir():
        default_devkit = Path("C:/devkitPro")
    parser.add_argument("--devkitpro", type=Path, default=default_devkit)
    parser.add_argument("--sdk", type=Path, default=ROOT / "third_party/XboxXBERecomp")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--skip-codegen", action="store_true")
    parser.add_argument("--nro", action="store_true", help="Link the experimental game NRO")
    parser.add_argument("--mesa-sdk", type=Path, help="Verified native switch-mesa SDK with manifest.json")
    parser.add_argument("--icon", type=Path, help="External JPEG icon for the NRO")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    revision = subprocess.check_output(["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True).strip()
    if revision != SDK_REVISION:
        parser.error(f"SDK revision {revision} differs from pinned {SDK_REVISION}")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    icon = args.icon.resolve() if args.icon else None
    if icon and (not args.nro or not icon.is_file()):
        parser.error("--icon requires --nro and an existing JPEG file")
    xbe = args.xbe.resolve()
    if not xbe.is_file():
        parser.error("--xbe must name the external original game executable")
    work = ROOT / "build/switch"
    work.mkdir(parents=True, exist_ok=True)
    receipt_path = work / "translation.json"
    identity = {"sdk": revision, "profile_sha256": digest(ROOT / "config/game.toml"), "xbe_sha256": digest(xbe)}
    previous = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if args.skip_codegen:
        if not previous or previous.get("inputs") != identity:
            parser.error("Cannot reuse translation without a matching XBE/profile/SDK receipt")
        for name, expected in previous["generated"].items():
            path = (work / "generated" / name).resolve()
            if not path.is_relative_to(work / "generated") or not path.is_file() or digest(path) != expected:
                parser.error(f"Generated translation changed: {name}")
    if not args.skip_codegen:
        tools = ROOT / "build/switch-tools"
        run(["cmake", "-S", ROOT / "switch/host-tools", "-B", tools, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
             "-DCMAKE_C_COMPILER=clang", "-DCMAKE_CXX_COMPILER=clang++", "-DSHAOLIN_SDK=" + posix_path(sdk),
             "-DSHAOLIN_ZYDIS=" + posix_path(ROOT / "third_party/zydis"),
             "-DSHAOLIN_TOML=" + posix_path(ROOT / "third_party/tomlplusplus")])
        run(["cmake", "--build", tools, "--parallel", args.jobs])
        tool = tools / ("xbe_recomp.exe" if os.name == "nt" else "xbe_recomp")
        # The bundled Windows host tool is an MSYS executable and uses POSIX
        # paths. The guest compiler below is a native devkitA64 executable.
        convert = posix_path if os.name == "nt" else str
        run([tool, convert(ROOT / "config/game.toml"), convert(xbe), convert(work / "generated"), "--xdk", convert(sdk / "xdk")])
        outputs = {p.name: digest(p) for p in sorted((work / "generated").glob('*')) if p.is_file()}
        receipt_path.write_text(json.dumps({"inputs": identity, "generated": outputs}, indent=2) + "\n")
    run([sys.executable, ROOT / "tools/prepare_switch_runtime.py", "--sdk", sdk, "--out", work / "adapted"])
    # An unknown image may be translated for investigation, but is not
    # silently relabelled as the upstream-audited executable.
    if identity["xbe_sha256"] != "48240902bf4a04eb89376217c529531032d3370749bcce91a194878cc49db74a":
        print("WARNING: experimental XBE variant; upstream ABI compatibility is not yet verified.", flush=True)
    env = dict(os.environ, DEVKITPRO=posix_path(args.devkitpro))
    nro_args = ["-DSHAOLIN_BUILD_NRO=OFF"]
    if args.nro:
        if not args.mesa_sdk or not (args.mesa_sdk / "manifest.json").is_file():
            parser.error("--nro requires --mesa-sdk with manifest.json")
        mesa = args.mesa_sdk.resolve()
        manifest = json.loads((mesa / "manifest.json").read_text())
        for name, expected in manifest["files"].items():
            path = (mesa / name).resolve()
            if not path.is_relative_to(mesa) or digest(path) != expected:
                parser.error(f"Changed Mesa SDK file: {name}")
        nro_args = ["-DSHAOLIN_BUILD_NRO=ON", "-DSHAOLIN_MESA=" + posix_path(mesa),
                    "-DSHAOLIN_GAME_XBE=" + posix_path(xbe),
                    "-DSHAOLIN_ICON=" + (posix_path(icon) if icon else "")]
    run(["cmake", "-S", ROOT / "switch", "-B", work / "arm64", "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_TOOLCHAIN_FILE=" + posix_path(args.devkitpro / "cmake/Switch.cmake"), "-DSHAOLIN_SDK=" + posix_path(sdk)] + nro_args, env=env)
    run(["cmake", "--build", work / "arm64", "--parallel", args.jobs], env=env)
    if digest(xbe) != identity["xbe_sha256"]:
        raise RuntimeError("Original XBE changed during the build")
    if args.nro:
        nro = work / "arm64/ShaolinMonksRecomp.nro"
        elf = work / "arm64/ShaolinMonksRecomp.elf"
        if not elf.exists():
            elf = work / "arm64/ShaolinMonksRecomp"
        if nro.read_bytes()[0x10:0x14] != b'NRO0':
            raise RuntimeError("Invalid NRO header")
        maintained = [p for folder in ("src", "switch", "tools", "config") for p in (ROOT / folder).rglob('*')
                      if p.is_file() and p.suffix in (".cpp", ".h", ".inl", ".py", ".cmake", ".toml")]
        maintained += [ROOT / "switch/CMakeLists.txt", ROOT / "switch/host-tools/CMakeLists.txt"]
        record = {"translation": json.loads(receipt_path.read_text()), "nro_sha256": digest(nro),
                  "build_id": (work / "arm64/build_id.txt").read_text().strip(), "elf_sha256": digest(elf),
                  "nro_bytes": nro.stat().st_size, "mesa_manifest_sha256": digest(mesa / "manifest.json"),
                  "maintained_sources": {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(maintained)},
                  "gameplay_verified": False, "hardware_fps_verified": False}
        if icon:
            record["icon_sha256"] = digest(icon)
        (work / "build-receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    print("Experimental game NRO built; gameplay is unverified." if args.nro else
          "ARM64 guest and host-core archives built. Use --nro to link the game integration.")


if __name__ == "__main__":
    main()
