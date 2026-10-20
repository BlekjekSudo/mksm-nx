"""Execute compiled ARM64 packed memory operations and ABI calls in Unicorn."""
import argparse
import json
from pathlib import Path
import subprocess
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_CPACR_EL1

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--compiler', type=Path, required=True)
args = parser.parse_args()
output = ROOT / 'build/switch-memory-test'
output.mkdir(parents=True, exist_ok=True)
elf = output / 'memory.elf'
subprocess.run([str(args.compiler), '-std=gnu++20', '-O2', '-fno-strict-aliasing', '-fno-stack-protector',
                '-fno-builtin', '-nostdlib', '-Wl,-Ttext=0x100000', '-Wl,-e,test_memory',
                '-I', str(ROOT / 'build/switch/adapted/include'), str(ROOT / 'tests/switch_memory_abi.cpp'),
                '-o', str(elf)], check=True)
machine = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
machine.mem_map(0x100000, 0x10000)
machine.mem_map(0x200000, 0x10000)
machine.mem_map(0x300000, 0x10000)
with elf.open('rb') as stream:
    image = ELFFile(stream)
    for segment in image.iter_segments():
        if segment['p_type'] == 'PT_LOAD' and segment['p_filesz']:
            address = int(segment['p_vaddr'])
            if address < 0x100000:
                machine.mem_map(address & ~0xFFF, ((int(segment['p_memsz']) + (address & 0xFFF) + 0xFFF) & ~0xFFF))
            machine.mem_write(address, segment.data())
    entry = int(image.header['e_entry'])
machine.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
machine.reg_write(UC_ARM64_REG_X0, 0x200000)
machine.reg_write(UC_ARM64_REG_SP, 0x30FFF0)
machine.reg_write(UC_ARM64_REG_LR, 0x10FFF0)
machine.emu_start(entry, 0x10FFF0, count=100000)
status = machine.reg_read(UC_ARM64_REG_X0)
if status:
    raise AssertionError(f'ARM64 memory/ABI case failed: {status}')
(output / 'result.json').write_text(json.dumps({'passed': True, 'alignments': 16, 'types': ['u16', 'u32', 'u64', 'f32', 'f64'],
                                               'hardware_validation': False}, indent=2) + '\n')
print('PASS: ARM64 unaligned accesses, canaries and AAPCS64 scalar arguments (16 alignments).')
