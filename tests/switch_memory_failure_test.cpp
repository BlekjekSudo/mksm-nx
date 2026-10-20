#include "kernel/memory.h"
#include "switch/guest_memory.h"
#include <cassert>
#include <cstdint>
#include <new>

namespace {
unsigned attempts;
}

// No guest memory exists in this fixture: a failed commit must be reported
// before the loader or allocator writes a single byte through the pointer.
namespace nxmem {
uint8_t* Reserve() { return reinterpret_cast<uint8_t*>(uintptr_t(0x100000000ull)); }
bool Commit(uint32_t, size_t) { ++attempts; return false; }
void Decommit(uint32_t, size_t) {}
}

int main() {
    assert(mem::Init());
    bool rejected = false;
    try { mem::ReserveImage(0x10000, 0x8B1D20); }
    catch (const std::bad_alloc&) { rejected = true; }
    assert(rejected && attempts == 1);
    assert(!mem::IsCommitted(0x10000));

    uint32_t base = 0x02000000, size = 0x10000;
    assert(mem::AllocateVirtual(base, size, mem::kMemReserve | mem::kMemCommit, 4) == 0xC0000017);
    assert(!mem::IsCommitted(base));
    mem::RegionInfo info{};
    assert(mem::QueryVirtual(base, info) && info.state == mem::kMemReserve);

    assert(mem::AllocSystem(0x10000, 16) == 0);
    assert(mem::AllocStack(0x10000) == 0);
    assert(attempts == 4);
}
