"""Compare an actual translated ARM64 movie kernel with its original x86 bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_ARCH_X86, UC_MODE_ARM, UC_MODE_32
from unicorn import arm64_const as ar, x86_const as xr

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--compiler', type=Path, required=True)
parser.add_argument('--xbe', type=Path, required=True)
args = parser.parse_args()
binary = args.xbe.read_bytes()
assert hashlib.sha256(binary).hexdigest() == '6153c7e525626daf9fef5a88f3186ca1ec5310d4dfb40598c08c3894b4db39c8', 'Unaudited fixture XBE'
image_base = struct.unpack_from('<I', binary, 0x104)[0]
count, table = struct.unpack_from('<II', binary, 0x11C)
code = None
for i in range(count):
    va, _, raw, size = struct.unpack_from('<IIII', binary, table - image_base + i * 56 + 4)
    if va <= 0x306530 < va + size:
        code = binary[raw + 0x306530 - va:raw + 0x306888 - va]
assert code is not None and len(code) == 856
translation = None
for path in (ROOT / 'build/switch/generated').glob('x86_recomp.*.cpp'):
    text = path.read_text()
    marker = 'X86_FUNC(__imp__sub_00306530) {'
    if marker in text:
        start = text.index(marker)
        translation = text[start:text.index('X86_WEAK_FUNC(sub_00306530', start)]
        break
assert translation
out = ROOT / 'build/switch-movie-test'
out.mkdir(parents=True, exist_ok=True)
source = out / 'kernel.cpp'
source.write_text('#include "cpu/x86_ops.h"\n#include <cstddef>\n' + translation +
                  '\nextern "C" const uint32_t context_layout[] = {sizeof(X86Context), offsetof(X86Context, xmm), offsetof(X86Context, mm)};\n')
elf = out / 'kernel.elf'
subprocess.run([str(args.compiler), '-std=gnu++20', '-O2', '-ffp-contract=off', '-fno-strict-aliasing', '-fwrapv',
                '-fno-stack-protector', '-nostdlib', '-Wl,-Ttext=0x1000000', '-Wl,-e,__imp__sub_00306530',
                '-I', str(ROOT / 'build/switch/adapted/include'), str(source), '-o', str(elf)], check=True)
arm = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
arm.mem_map(0x1000000, 0x100000)
arm.mem_map(0x2000000, 0x800000)  # base + guest VA
arm.mem_map(0x3000000, 0x10000)   # native context and stack
with elf.open('rb') as stream:
    image = ELFFile(stream)
    for segment in image.iter_segments():
        if segment['p_type'] != 'PT_LOAD' or not segment['p_filesz']: continue
        va = int(segment['p_vaddr'])
        if va < 0x1000000:
            arm.mem_map(va & ~0xFFF, (int(segment['p_memsz']) + (va & 0xFFF) + 0xFFF) & ~0xFFF)
        arm.mem_write(va, segment.data())
    symbols = image.get_section_by_name('.symtab')
    layout_va = next(s['st_value'] for s in symbols.iter_symbols() if s.name == 'context_layout')
    context_size, xmm_offset, mm_offset = struct.unpack('<3I', arm.mem_read(layout_va, 12))
    entry = int(image.header['e_entry'])
x86 = Uc(UC_ARCH_X86, UC_MODE_32)
x86.mem_map(0, 0x800000)
x86.mem_write(0x306530, code)
gprs = (xr.UC_X86_REG_EAX, xr.UC_X86_REG_ECX, xr.UC_X86_REG_EDX, xr.UC_X86_REG_EBX,
        xr.UC_X86_REG_ESP, xr.UC_X86_REG_EBP, xr.UC_X86_REG_ESI, xr.UC_X86_REG_EDI)
initial = (0x11111111, 0x22222222, 0x33333333, 0x44444444, 0x700000, 0x66666666, 0x77777777, 0x88888888)
rng = random.Random(0x306530)
for case in range(320):
    values = [rng.uniform(-1000, 1000) for _ in range(64)]
    if case < 64:
        values = [0.0] * 64
        values[case] = rng.choice((1.0, -1.0, 0.5, -0.5, 1024.0))
    fixture = struct.pack('<84f', *(values + [rng.uniform(-2, 2) for _ in range(20)]))
    writes = [(0x6FFF00, b'\xA5' * 512), (0x110000, b'\xA5' * 512), (0x120000, b'\xA5' * 512),
              (0x700000, struct.pack('<4I', 0x10000, 0x100000, 0x110000, 0x120000)),
              (0x74C828, struct.pack('<I', 0x500000)), (0x100000, fixture[:256]), (0x500000, fixture[256:])]
    for va, data in writes:
        x86.mem_write(va, data)
        arm.mem_write(0x2000000 + va, data)
    for register, value in zip(gprs, initial): x86.reg_write(register, value)
    for i in range(8): x86.reg_write(xr.UC_X86_REG_XMM0 + i, 0)
    x86.reg_write(xr.UC_X86_REG_MXCSR, 0x1F80)
    context = bytearray(context_size)
    struct.pack_into('<8I', context, 0, *initial)
    arm.mem_write(0x3000000, bytes(context))
    arm.reg_write(ar.UC_ARM64_REG_X0, 0x3000000)
    arm.reg_write(ar.UC_ARM64_REG_X1, 0x2000000)
    arm.reg_write(ar.UC_ARM64_REG_SP, 0x300FFF0)
    arm.reg_write(ar.UC_ARM64_REG_LR, 0x10FFFF0)
    arm.reg_write(ar.UC_ARM64_REG_CPACR_EL1, 3 << 20)
    arm.reg_write(ar.UC_ARM64_REG_FPCR, 0)
    x86.emu_start(0x306530, 0x10000, count=2000)
    arm.emu_start(entry, 0x10FFFF0, count=20000)
    actual = bytes(arm.mem_read(0x3000000, context_size))
    assert struct.unpack_from('<8I', actual) == tuple(x86.reg_read(r) for r in gprs), ('GPR', case)
    expected_xmm = b''.join(x86.reg_read(xr.UC_X86_REG_XMM0 + i).to_bytes(16, 'little') for i in range(8))
    assert actual[xmm_offset:xmm_offset + 128] == expected_xmm, ('XMM', case)
    for va in (0x110000, 0x120000, 0x6FFF00):
        assert bytes(arm.mem_read(0x2000000 + va, 512)) == bytes(x86.mem_read(va, 512)), ('memory', case, hex(va))
(out / 'result.json').write_text(json.dumps({'passed': True, 'original_xbe_kernel': '0x00306530', 'cases': 320,
                                            'checks': ['GPR', 'XMM', 'output/scratch/stack bytes'], 'gameplay_verified': False}, indent=2) + '\n')
print('PASS: original x86 movie kernel equals actual ARM64 translation across 320 cases (GPR/XMM/memory).')
