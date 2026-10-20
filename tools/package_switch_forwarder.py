"""Build and inspect a private, external-NRO Switch application forwarder.

Inputs are explicit local paths. No keys, NRO, or game data enter the NSP.
Requires devkitPro, a local hacBrewPack executable, Pillow, and cryptography.
On Windows, a Linux hacBrewPack executable is invoked through WSL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image


NRO_PATH = "/switch/shaolin-recomp/ShaolinMonksRecomp.nro"
TARGET = "sdmc:" + NRO_PATH
TITLE = "MK Shaolin Monk"
AUTHOR = "ArdRecomp"
VERSION = "0.2.0"
TITLE_ID = 0x0500000000000000 | (
    int.from_bytes(hashlib.sha256((NRO_PATH * 2).encode()).digest()[:8], "little")
    & 0x00FFFFFFFFFFF000
)
LOADER_REVISION = "72a94b905816de24817594109fb012a8f7107d8c"
LOADER_HASHES = {
    "source/main.c": "ee3ad5918d6883a2d289da83b945928b3cca88f390d698e0e056f67256d5cc13",
    "source/trampoline.s": "2f11a79cc9a4a162dcef557274076b66480bb256f353d47cea1ef56ab8e11f4e",
    "base.json": "b528d66f71a44c472e90cb81dc72b859f7c907da41e92f6a265d027c8831795f",
}
LICENSES = ("Sphaira-GPL-3.0.txt", "nx-hbloader-ISC.txt", "libnx-ISC.txt")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run(command: list[object], **kwargs) -> None:
    subprocess.run([str(p) for p in command], check=True, **kwargs)


def slice_checked(data: bytes, start: int, size: int) -> bytes:
    require(0 <= start <= len(data) and 0 <= size <= len(data) - start,
            "Container range is outside its payload")
    return data[start:start + size]


def pfs_files(data: bytes) -> dict[str, bytes]:
    require(data[:4] == b"PFS0", "Missing PFS0 magic")
    count, string_size = struct.unpack_from("<II", data, 4)
    require(count <= 1024, "Unexpected PFS0 file count")
    strings_start = 16 + count * 24
    payload_start = strings_start + string_size
    strings = slice_checked(data, strings_start, string_size)
    result: dict[str, bytes] = {}
    spans = []
    for i in range(count):
        offset, size, name_at = struct.unpack_from("<QQI", data, 16 + i * 24)
        require(name_at < len(strings), "Invalid PFS0 name offset")
        name = strings[name_at:strings.index(0, name_at)].decode("utf-8")
        require(name and "/" not in name and "\\" not in name
                and name not in (".", "..") and name not in result, "Invalid PFS0 name")
        result[name] = slice_checked(data, payload_start + offset, size)
        spans.append((offset, offset + size))
    spans.sort()
    require(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])),
            "Overlapping PFS0 file ranges")
    return result


def romfs_files(data: bytes) -> dict[str, bytes]:
    fields = struct.unpack_from("<10Q", data)
    require(fields[0] == 0x50, "Unexpected RomFS header")
    dirs = slice_checked(data, fields[3], fields[4])
    files = slice_checked(data, fields[7], fields[8])
    parent, sibling, child_dir, child_file, _, name_size = struct.unpack_from("<6I", dirs)
    require(parent == 0 and sibling == 0xFFFFFFFF and child_dir == 0xFFFFFFFF
            and name_size == 0, "Expected a flat RomFS root")
    result: dict[str, bytes] = {}
    visited = set()
    while child_file != 0xFFFFFFFF:
        require(child_file not in visited and len(visited) < 64, "Invalid RomFS file chain")
        visited.add(child_file)
        parent, sibling, offset, size, _, name_size = struct.unpack_from(
            "<IIQQII", files, child_file)
        name = slice_checked(files, child_file + 32, name_size).decode("utf-8")
        require(parent == 0 and name and "/" not in name and "\\" not in name
                and name not in result, "Invalid RomFS file record")
        result[name] = slice_checked(data, fields[9] + offset, size)
        child_file = sibling
    return result


def inspect_nca(blob: bytes, header_key: bytes) -> dict:
    """Decrypt only the NCA header; --plaintext leaves data sections readable."""
    require(len(blob) >= 0xC00, "Short NCA")
    header = bytearray()
    for offset in range(0, 0xC00, 0x200):
        crypt = Cipher(algorithms.AES(header_key),
                       modes.XTS((offset // 0x200).to_bytes(16, "big"))).decryptor()
        header += crypt.update(blob[offset:offset + 0x200]) + crypt.finalize()
    require(header[0x200:0x204] == b"NCA3", "Cannot decode packed NCA header")
    require(struct.unpack_from("<Q", header, 0x208)[0] == len(blob), "NCA size mismatch")
    require(struct.unpack_from("<Q", header, 0x210)[0] == TITLE_ID, "NCA title ID mismatch")
    require(not any(header[0x230:0x240]), "Unexpected title-key rights requirement")
    sections = []
    for i in range(4):
        start, end = struct.unpack_from("<II", header, 0x240 + i * 16)
        if not start and not end:
            continue
        section = slice_checked(blob, start * 0x200, (end - start) * 0x200)
        fs = header[0x400 + i * 0x200:0x600 + i * 0x200]
        require(hashlib.sha256(fs).digest() == header[0x280 + i * 32:0x2A0 + i * 32],
                "NCA filesystem header hash mismatch")
        require(fs[4] == 1, "Expected unencrypted homebrew payload section")
        if fs[2] == 1 and fs[3] == 2:
            block_size, layers = struct.unpack_from("<II", fs, 0x28)
            hash_at, hash_size, pfs_at, pfs_size = struct.unpack_from("<4Q", fs, 0x30)
            require(layers == 2 and block_size > 0, "Unsupported PFS0 hash layout")
            hashes = slice_checked(section, hash_at, hash_size)
            pfs = slice_checked(section, pfs_at, pfs_size)
            require(hashlib.sha256(hashes).digest() == fs[8:40], "PFS0 master hash mismatch")
            expected = b"".join(hashlib.sha256(pfs[o:o + block_size]).digest()
                                for o in range(0, len(pfs), block_size))
            require(hashes == expected, "PFS0 block hash mismatch")
            sections.append({"type": "PFS0", "files": pfs_files(pfs)})
        elif fs[2] == 0 and fs[3] == 3:
            require(fs[8:12] == b"IVFC", "Missing IVFC magic")
            master_size, levels = struct.unpack_from("<II", fs, 0x10)
            require(master_size == 32 and levels == 7, "Unexpected IVFC level count")
            level_offsets = [struct.unpack_from("<QQII", fs, 0x18 + n * 24)
                             for n in range(6)]
            level_data = [slice_checked(section, at, size)
                          for at, size, _, _ in level_offsets]
            require(hashlib.sha256(level_data[0]).digest() == fs[0xC8:0xE8],
                    "IVFC master hash mismatch")
            for n in range(1, 6):
                exponent = level_offsets[n][2]
                require(9 <= exponent <= 24, "Unexpected IVFC hash block size")
                block_size = 1 << exponent
                hashes = b"".join(hashlib.sha256(
                    level_data[n][o:o + block_size].ljust(block_size, b"\0")).digest()
                                  for o in range(0, len(level_data[n]), block_size))
                require(level_data[n - 1][:len(hashes)] == hashes
                        and not any(level_data[n - 1][len(hashes):]), "IVFC block hash mismatch")
            rom_at, rom_size, _, _ = level_offsets[-1]
            sections.append({"type": "RomFS",
                             "files": romfs_files(slice_checked(section, rom_at, rom_size))})
        else:
            raise ValueError("Unexpected packed filesystem type")
    return {"content_type": header[0x205], "sections": sections,
            "sha256": digest(blob), "bytes": len(blob)}


def inspect_npdm(data: bytes) -> dict:
    require(data[:4] == b"META" and data[12] & 1, "Expected AArch64 NPDM")
    require((data[12] >> 1) & 7 == 3, "Expected standard 39-bit address space")
    require(struct.unpack_from("<I", data, 0x14)[0] == 0x1000000,
            "Expected 16 MiB system resource area")
    aci, _, acid, _ = struct.unpack_from("<4I", data, 0x70)
    require(data[aci:aci + 4] == b"ACI0"
            and data[acid + 0x200:acid + 0x204] == b"ACID", "Missing NPDM access control")
    require(struct.unpack_from("<Q", data, aci + 0x10)[0] == TITLE_ID
            and struct.unpack_from("<QQ", data, acid + 0x210) == (TITLE_ID, TITLE_ID),
            "NPDM title ID mismatch")
    acid_flags = struct.unpack_from("<I", data, acid + 0x20C)[0]
    require((acid_flags >> 2) & 0xF == 0, "Expected full application memory pool")
    report = {}
    for name, base, field in (("requested", aci, 0x30), ("allowed", acid, 0x230)):
        offset, size = struct.unpack_from("<II", data, base + field)
        caps = [v[0] for v in struct.iter_unpack("<I", slice_checked(data, base + offset, size))]
        require([v for v in caps if v & 0xF == 7] == [0x030073F7],
                "Unexpected CPU/priority capability")
        require([v for v in caps if v & 0x1FFFF == 0xFFFF] == [0x0004FFFF],
                "Unexpected debug capability")
        app_types = [v >> 14 for v in caps if v & 0x3FFF == 0x1FFF]
        require(app_types == [1], "Expected Application capability")
        syscalls = set()
        for cap in caps:
            if cap & 0x1F == 0xF:
                group, mask = cap >> 29, (cap >> 5) & 0xFFFFFF
                syscalls.update(group * 24 + b for b in range(24) if mask & (1 << b))
        require(syscalls == set(range(0xC0)), "Expected complete homebrew syscall hints")
        report[name] = {"application_type": 1, "cpu_ids": [0, 1, 2, 3],
                        "priority_range": [28, 63], "syscalls": "0x00..0xbf",
                        "svc_debug": False, "same_process_mapping_svcs_present": True}
    return report


def inspect_nacp(data: bytes) -> dict:
    require(len(data) == 0x4000, "Unexpected NACP length")
    supported = struct.unpack_from("<I", data, 0x302C)[0]
    populated = 0
    for language in range(16):
        title = data[language * 0x300:language * 0x300 + 0x200].split(b"\0", 1)[0].decode()
        author = data[language * 0x300 + 0x200:(language + 1) * 0x300].split(b"\0", 1)[0].decode()
        if title or author or supported & (1 << language):
            require((title, author) == (TITLE, AUTHOR), "Packed title/author mismatch")
            populated += 1
    require(populated > 0 and supported != 0, "Missing supported language metadata")
    require(data[0x3060:0x3070].split(b"\0", 1)[0].decode() == VERSION,
            "Packed version mismatch")
    require(data[0x3025:0x3028] == bytes((0, 0, 1)), "Unexpected profile requirements")
    require(struct.unpack_from("<8Q", data, 0x30B0) == (TITLE_ID,) * 8,
            "Unexpected local communication IDs")
    return {"title": TITLE, "author": AUTHOR, "version": VERSION,
            "language_slots_checked": 16, "populated_languages": populated,
            "supported_language_mask": hex(supported)}


def selected_keys(path: Path) -> dict[str, str]:
    selected = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if separator and name in ("header_key", "key_area_key_application_00"):
            length = 64 if name == "header_key" else 32
            require(re.fullmatch("[0-9a-fA-F]{" + str(length) + "}", value) is not None,
                    "Invalid required packing key")
            selected[name] = value
    require(len(selected) == 2, "Local keyset lacks required packing keys")
    return selected


def wsl_path(path: Path) -> str:
    path = path.resolve()
    require(bool(re.fullmatch("[A-Za-z]:", path.drive)), "WSL requires a local drive path")
    return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("loader", "licenses", "icon", "keys", "devkitpro", "packer", "work", "output"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--packer-sha256", required=True)
    parser.add_argument("--base-config", type=Path,
                        default=Path(__file__).resolve().parents[1] / "switch/forwarder/base.json")
    parser.add_argument("--wsl-distro", default="Ubuntu")
    args = parser.parse_args()
    for option in ("loader", "licenses", "icon", "keys", "devkitpro", "packer", "work", "output"):
        setattr(args, option, getattr(args, option).resolve())
    require(not args.work.exists(), "Choose a fresh work directory")
    require(not args.output.exists(), "Choose a fresh output directory")
    require(digest(args.packer.read_bytes()) == args.packer_sha256.lower(),
            "Packer hash does not match the explicitly approved local executable")
    base_config = args.base_config.resolve()
    for relative, expected in LOADER_HASHES.items():
        source = base_config if relative == "base.json" else args.loader / relative
        require(digest(source.read_bytes()) == expected,
                "Pinned loader source changed: " + relative)
    icon = args.icon.read_bytes()
    with Image.open(io.BytesIO(icon)) as image:
        require(image.format == "JPEG" and image.size == (256, 256) and image.mode == "RGB",
                "Icon must be an RGB 256x256 JPEG")
        image.load()
    licenses = {name: (args.licenses / name).read_bytes() for name in LICENSES}
    keys = selected_keys(args.keys)
    for folder in ("source", "exefs", "romfs", "control"):
        (args.work / folder).mkdir(parents=True, exist_ok=False)
    for name in ("main.c", "trampoline.s"):
        shutil.copyfile(args.loader / "source" / name, args.work / "source" / name)
    (args.work / "source/version.h").write_text('#define VERSION "3.0.0"\n', encoding="ascii")
    for name in ("nextNroPath", "nextArgv"):
        (args.work / "romfs" / name).write_bytes(TARGET.encode("ascii"))
    (args.work / "control/icon_AmericanEnglish.dat").write_bytes(icon)
    config = json.loads(base_config.read_text(encoding="utf-8"))
    config.update(name="ShaolinForwarder", address_space_type=3, system_resource_size="0x1000000",
                  title_id=hex(TITLE_ID), title_id_range_min=hex(TITLE_ID),
                  title_id_range_max=hex(TITLE_ID))
    config["filesystem_access"]["content_owner_ids"] = [hex(TITLE_ID)]
    write_json(args.work / "forwarder.json", config)
    sdk = args.devkitpro
    suffix = ".exe" if os.name == "nt" else ""
    compiler = sdk / ("devkitA64/bin/aarch64-none-elf-gcc" + suffix)
    tool = lambda name: sdk / "tools/bin" / (name + suffix)
    env = dict(os.environ, DEVKITPRO=sdk.as_posix())
    common = ["-O2", "-march=armv8-a+crc", "-mtune=cortex-a57", "-ffixed-x18",
              "-fPIE", "-ffunction-sections", "-fdata-sections", "-D__SWITCH__", "-mtp=soft",
              "-isystem", (sdk / "libnx/include").as_posix(),
              "-include", (args.work / "source/version.h").as_posix()]
    objects = []
    for name in ("main.c", "trampoline.s"):
        obj = args.work / (name + ".o")
        run([compiler, *common, "-c", args.work / "source" / name, "-o", obj], env=env)
        objects.append(obj)
    run([compiler, *common, "-specs=" + (sdk / "libnx/switch.specs").as_posix(),
         "-Wl,-wrap,exit", *objects, "-L" + (sdk / "libnx/lib").as_posix(),
         "-lnx", "-o", args.work / "forwarder.elf"], env=env)
    run([tool("elf2nso"), args.work / "forwarder.elf", args.work / "exefs/main"])
    run([tool("npdmtool"), args.work / "forwarder.json", args.work / "exefs/main.npdm"])
    capabilities = inspect_npdm((args.work / "exefs/main.npdm").read_bytes())
    nacp_path = args.work / "control/control.nacp"
    run([tool("nacptool"), "--create", TITLE, AUTHOR, VERSION, nacp_path,
         f"--titleid={TITLE_ID:016x}"])
    nacp = bytearray(nacp_path.read_bytes())
    nacp[0x3025:0x3028] = bytes((0, 0, 1))
    nacp[0x30F1:0x30F4] = bytes(3)
    for offset in (0x3080, 0x3088, 0x3090, 0x3098, 0x3148, 0x3150, 0x3158, 0x3160):
        struct.pack_into("<Q", nacp, offset, 0)
    # Match hacBrewPack's documented local communication ID normalization.
    struct.pack_into("<8Q", nacp, 0x30B0, *([TITLE_ID] * 8))
    nacp_path.write_bytes(nacp)
    inspect_nacp(nacp)
    print("Loader compiled; packing private NSP. Packer output is suppressed to protect keys.", flush=True)
    with tempfile.TemporaryDirectory(prefix="shaolin-forwarder-keys-") as temp:
        keyfile = Path(temp) / "keys.dat"
        keyfile.write_text("".join(f"{k} = {v}\n" for k, v in keys.items()), encoding="ascii")
        keyfile.chmod(0o600)
        pack_args = ["--keyset", keyfile, "--titleid", f"{TITLE_ID:016x}", "--nologo",
                     "--nopatchnacplogo", "--plaintext", "--keepncadir"]
        if os.name == "nt" and args.packer.read_bytes()[:4] == b"\x7fELF":
            command = ["wsl", "-d", args.wsl_distro, "--cd", wsl_path(args.work), "--exec",
                       wsl_path(args.packer), *[wsl_path(p) if isinstance(p, Path) else p
                                                for p in pack_args]]
        else:
            command = [str(args.packer), *map(str, pack_args)]
        packed = subprocess.run(command, cwd=args.work, capture_output=True, timeout=300)
        require(packed.returncode == 0,
                f"Packing failed ({packed.returncode}); output suppressed to protect keys")
    payload = (args.work / "hacbrewpack_nsp" / f"{TITLE_ID:016x}.nsp").read_bytes()
    entries = pfs_files(payload)
    require(len(entries) == 3 and all(n.endswith(".nca") for n in entries),
            "Unexpected NSP contents")
    ncas = {}
    for name, blob in entries.items():
        require(name.split(".")[0] == digest(blob)[:32], "NCA content ID mismatch")
        nca = inspect_nca(blob, bytes.fromhex(keys["header_key"]))
        require(nca["content_type"] not in ncas, "Duplicate NCA content type")
        ncas[nca["content_type"]] = nca
    keys.clear()
    require(set(ncas) == {0, 1, 2}, "Expected Program, Meta, and Control NCAs")
    program = {s["type"]: s["files"] for s in ncas[0]["sections"]}
    require(len(ncas[0]["sections"]) == 2 and set(program) == {"PFS0", "RomFS"},
            "Unexpected Program NCA sections")
    require(set(program["PFS0"]) == {"main", "main.npdm"}, "Unexpected ExeFS files")
    require(set(program["RomFS"]) == {"nextNroPath", "nextArgv"}, "Unexpected loader RomFS")
    for name, content in program["PFS0"].items():
        require(content == (args.work / "exefs" / name).read_bytes(), "Packed ExeFS mismatch")
    for name, content in program["RomFS"].items():
        require(content == TARGET.encode("ascii"), "Packed forward path/argv mismatch")
    require(inspect_npdm(program["PFS0"]["main.npdm"]) == capabilities,
            "Packer changed access capabilities")
    require(len(ncas[2]["sections"]) == 1 and ncas[2]["sections"][0]["type"] == "RomFS",
            "Unexpected Control NCA section")
    control = ncas[2]["sections"][0]["files"]
    require(set(control) == {"control.nacp", "icon_AmericanEnglish.dat"},
            "Unexpected control files")
    require(control["icon_AmericanEnglish.dat"] == icon, "Packed icon differs from requested JPEG")
    require(control["control.nacp"] == nacp, "Packed control metadata differs")
    metadata = inspect_nacp(control["control.nacp"])
    require(len(ncas[1]["sections"]) == 1 and ncas[1]["sections"][0]["type"] == "PFS0",
            "Unexpected Meta NCA section")
    cnmts = ncas[1]["sections"][0]["files"]
    require(len(cnmts) == 1, "Unexpected CNMT count")
    cnmt = next(iter(cnmts.values()))
    require(struct.unpack_from("<Q", cnmt)[0] == TITLE_ID and cnmt[0xC] == 0x80,
            "CNMT application title mismatch")
    extended, count = struct.unpack_from("<HH", cnmt, 0xE)
    require(count == 2, "Unexpected CNMT content count")
    cnmt_types = set()
    for i in range(count):
        record = slice_checked(cnmt, 0x20 + extended + i * 0x38, 0x38)
        content_type = record[0x36]
        require(content_type in (1, 3), "Unexpected CNMT content type")
        cnmt_types.add(content_type)
        expected = ncas[0 if content_type == 1 else 2]
        require(record[:32].hex() == expected["sha256"]
                and record[32:48].hex() == expected["sha256"][:32]
                and int.from_bytes(record[48:54], "little") == expected["bytes"],
                "CNMT content reference mismatch")
    require(cnmt_types == {1, 3}, "Missing CNMT Program/Control entry")
    args.output.mkdir(parents=True)
    filename = f"MK Shaolin Monk [{TITLE_ID:016X}].nsp"
    (args.output / filename).write_bytes(payload)
    (args.output / "icon.jpg").write_bytes(icon)
    shutil.copytree(args.work / "source", args.output / "source")
    shutil.copyfile(args.work / "forwarder.json", args.output / "source/forwarder.json")
    shutil.copyfile(base_config, args.output / "source/base.json")
    shutil.copyfile(__file__, args.output / "source/package_switch_forwarder.py")
    (args.output / "licenses").mkdir()
    for name, content in licenses.items():
        (args.output / "licenses" / name).write_bytes(content)
    receipt = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        **metadata, "title_id": f"{TITLE_ID:016X}", "nsp": filename,
        "nsp_sha256": digest(payload), "nsp_bytes": len(payload),
        "target": TARGET, "argv": TARGET, "external_nro": True,
        "embedded_game_assets": False, "icon_sha256": digest(icon),
        "architecture": "AArch64", "address_space_bits": 39,
        "application_memory_pool": True, "system_resource_bytes": 0x1000000,
        "guest_virtual_reservation_bytes": 0x100000000,
        "loader_heap_safety_reserve_bytes": 96 * 1024 * 1024,
        "custom_memory_layout": False, "capabilities": capabilities,
        "loader_revision": LOADER_REVISION, "loader_source_hashes": LOADER_HASHES,
        "packer_sha256": args.packer_sha256.lower(), "builder_sha256": digest(Path(__file__).read_bytes()),
        "elf_sha256": digest((args.work / "forwarder.elf").read_bytes()),
        "packed_files": {section: {name: digest(value) for name, value in files.items()}
                         for section, files in {"exefs": program["PFS0"],
                                                "romfs": program["RomFS"], "control": control}.items()},
        "nca_files": {name: digest(value) for name, value in entries.items()},
        "validation": {"nsp_pfs0": True, "nca_headers": True, "nca_section_header_hashes": True,
                       "pfs0_block_hashes": True, "ivfc_block_hashes": True,
                       "packed_paths_and_argv": True,
                       "packed_npdm": True, "packed_metadata": True, "packed_icon": True,
                       "cnmt_content_references": True},
        "installed": False, "hardware_tested": False, "emulator_tested": False,
    }
    write_json(args.output / "build.json", receipt)
    (args.output / "README.txt").write_text(
        f"{TITLE}\nAuthor: {AUTHOR}\nTitle ID: {TITLE_ID:016X}\n\n"
        f"This private homebrew NSP forwards to:\n{TARGET}\n\n"
        "Keep the NRO and existing game/, hdd/, cache/, and logs/ folders under "
        "/switch/shaolin-recomp on the SD card. Preserve hdd/ when updating: it contains "
        "your saves. The NSP contains no NRO or game data. To update, replace the NRO "
        "at the same path; reinstalling the forwarder is unnecessary.\n\n"
        "The package structure, icon, title, author, and forward path were verified. "
        "This NSP has not been installed or boot-tested. It requires a homebrew-capable "
        "system that accepts unsigned homebrew application packages.\n\n"
        "Loader source and licenses are included. See build.json for hashes, technical "
        "settings, and validation results. No keys are included.\n",
        encoding="utf-8")
    print(json.dumps({"nsp": str(args.output / filename), "sha256": digest(payload),
                      "bytes": len(payload), "title_id": f"{TITLE_ID:016X}",
                      "structural_validation": "passed"}, indent=2))


if __name__ == "__main__":
    main()
