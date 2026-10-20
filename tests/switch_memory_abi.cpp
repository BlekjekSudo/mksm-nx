#include "cpu/x86_ops.h"

extern "C" __attribute__((noinline)) uint64_t accept64(uint64_t value) { return value ^ 0xACE0ACE0ACE0ACE0ull; }
extern "C" __attribute__((noinline)) uint32_t accept32(uint32_t value) { return value + 7; }
extern "C" __attribute__((noinline)) double acceptf64(double value) { return value + 0.25; }

extern "C" uint32_t test_memory(uint8_t* base) {
    // All residues modulo sixteen include accesses crossing natural 4/8-byte
    // alignment. Guards surround each slot and every callee receives values
    // through the real AAPCS64 argument registers.
    for (uint32_t offset = 0; offset < 16; ++offset) {
        const uint32_t p = 32 + offset;
        for (uint32_t i = 0; i < 128; ++i) base[i] = 0xCD;
        X86_MEM16(p) = 0x1234;
        if (X86_MEM16(p) != 0x1234 || base[p - 1] != 0xCD || base[p + 2] != 0xCD) return 1;
        X86_MEM32(p) = 0xFEDCBA98;
        if (accept32(X86_MEM32(p)) != 0xFEDCBA9F || base[p + 4] != 0xCD) return 2;
        X86_MEM32(p) ^= 0xFFFFFFFF;
        if (X86_MEM32(p) != 0x01234567) return 3;
        X86_MEM64(p) = 0xFEDCBA9876543210ull;
        if (accept64(X86_MEM64(p)) != (0xFEDCBA9876543210ull ^ 0xACE0ACE0ACE0ACE0ull)) return 4;
        if (base[p - 1] != 0xCD || base[p + 8] != 0xCD) return 5;
        X86_MEMF32(p) = -1.25f;
        if (X86_MEM32(p) != 0xBFA00000) return 6;
        X86_MEMF64(p) = 3.5;
        if (acceptf64(X86_MEMF64(p)) != 3.75 || base[p + 8] != 0xCD) return 7;
    }
    return 0;
}
