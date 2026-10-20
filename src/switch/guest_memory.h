#pragma once
#include <cstddef>
#include <cstdint>

// A real 4 GiB virtual reservation keeps the SDK's base+guest-address ABI.
// Commit groups contiguous 64 KiB chunks into writable module-code aliases.
namespace nxmem {
uint8_t* Reserve();
bool Commit(uint32_t address, size_t size);
void Decommit(uint32_t address, size_t size);
}
