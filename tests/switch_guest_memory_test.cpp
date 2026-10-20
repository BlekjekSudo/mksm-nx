#include "switch/guest_memory.h"
#include <switch.h>
#include <sys/mman.h>
#include <unistd.h>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>

namespace {
constexpr uint64_t kSpace = 1ull << 32;
constexpr uint32_t kPhysical = 0x80000000;
constexpr uint32_t kMirror = 0xF0000000;
constexpr size_t kRamSize = 64u << 20;
constexpr Result kOutOfResource = 0xCE01;
constexpr Result kInjectedFailure = 0xD401;
constexpr Handle kProcess = 7;
struct Allocation { int fd; size_t size; };
enum class Kind { ModuleCode, CodeMemory, Mirror };
struct Mapping { void* allocation; size_t offset; size_t size; Kind kind; u32 permissions; };
std::map<void*, Allocation> allocations;
std::map<uintptr_t, Mapping> mapped;
std::map<Handle, void*> code_handles;
VirtmemReservation reservation{};
bool fail_map;
bool fail_permission;
bool fail_mirror;
bool fail_query;
unsigned map_calls;
unsigned code_object_calls;
unsigned hints[256]{};
u8 absent_hint;

Mapping* FindMapping(uintptr_t address) {
    auto it = mapped.upper_bound(address);
    if (it == mapped.begin()) return nullptr;
    --it;
    return address - it->first < it->second.size ? &it->second : nullptr;
}

Result Install(uintptr_t destination, void* source, size_t offset, size_t size, Kind kind, u32 permissions) {
    assert(reservation.address && size && size % 0x1000 == 0);
    assert(destination % 0x1000 == 0);
    assert(destination >= uintptr_t(reservation.address));
    assert(destination + size <= uintptr_t(reservation.address) + reservation.size);
    auto allocation = allocations.find(source);
    assert(allocation != allocations.end() && offset + size <= allocation->second.size);
    auto next = mapped.lower_bound(destination);
    assert(next == mapped.end() || destination + size <= next->first);
    assert(next == mapped.begin() || std::prev(next)->first + std::prev(next)->second.size <= destination);
    int protection = permissions == Perm_Rw ? PROT_READ | PROT_WRITE : PROT_READ;
#ifdef __CYGWIN__
    // The Windows host models the reservation but still maps real shared pages.
#endif
    void* result = mmap(reinterpret_cast<void*>(destination), size, protection,
                        MAP_SHARED | MAP_FIXED, allocation->second.fd, off_t(offset));
    if (result != reinterpret_cast<void*>(destination)) {
        std::fprintf(stderr, "mock map destination=%p size=%zX source=%p fd=%d offset=%zX\n",
                     reinterpret_cast<void*>(destination), size, source, allocation->second.fd, offset);
        std::perror("guest memory mock mmap");
    }
    assert(result == reinterpret_cast<void*>(destination));
    if (!permissions) assert(mprotect(result, size, PROT_NONE) == 0);
    mapped.emplace(destination, Mapping{source, offset, size, kind, permissions});
    return 0;
}

Result Remove(uintptr_t destination, size_t size, Kind kind) {
    auto mapping = mapped.find(destination);
    assert(mapping != mapped.end() && mapping->second.size == size && mapping->second.kind == kind);
#ifdef __CYGWIN__
    assert(munmap(reinterpret_cast<void*>(destination), size) == 0);
#endif
#ifndef __CYGWIN__
    void* result = mmap(reinterpret_cast<void*>(destination), size, PROT_NONE,
                        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    assert(result == reinterpret_cast<void*>(destination));
#endif
    mapped.erase(mapping);
    return 0;
}

uint8_t* Reserve() {
    auto* result = nxmem::Reserve();
    assert(result && nxmem::Reserve() == result);
    assert(mapped.size() == 2 && allocations.size() == 1);
    return result;
}

void TestImage() {
    auto* base = Reserve();
    constexpr uint32_t start = 0x10000;
    constexpr size_t size = 0x8B1D20;
    assert(nxmem::Commit(start, size));
    for (size_t at = 0; at < size; at += 4096) {
        assert(base[start + at] == 0);
        base[start + at] = uint8_t((at / 4096 * 37) + 1);
    }
    for (size_t at = 0; at < size; at += 4096)
        assert(base[start + at] == uint8_t((at / 4096 * 37) + 1));
    base[start + size - 1] = 0xAF;
    assert(nxmem::Commit(start + 123, size - 246));
    assert(base[start + size - 1] == 0xAF);
    assert(code_object_calls == 0 && map_calls == 2);
}

void TestSparse() {
    auto* base = Reserve();
    for (unsigned i = 0; i < 48; ++i) {
        uint32_t at = 0x01000000 + i * 0x20000;
        assert(nxmem::Commit(at + 123, 17));
        assert(base[at + 123] == 0);
        base[at + 123] = uint8_t(i + 1);
    }
    for (unsigned i = 0; i < 48; ++i)
        assert(base[0x01000000 + i * 0x20000 + 123] == i + 1);
    assert(code_object_calls == 0 && allocations.size() == 49);
}

void TestOverlapAndDecommit() {
    auto* base = Reserve();
    constexpr uint32_t start = 0x02000000;
    assert(nxmem::Commit(start, 0x40000));
    std::memset(base + start, 0x37, 0x40000);
    assert(nxmem::Commit(start - 0x10001, 0x70002));
    for (size_t i = 0; i < 0x40000; ++i) assert(base[start + i] == 0x37);
    assert(base[start - 1] == 0 && base[start + 0x40000] == 0);
    nxmem::Decommit(start + 0xFFF0, 0x20020);
    for (size_t i = 0; i < 0x40000; ++i)
        assert(base[start + i] == (i >= 0xFFF0 && i < 0x30010 ? 0 : 0x37));
    assert(nxmem::Commit(start + 0xFFF0, 0x20020));
    assert(base[start + 0xFFEF] == 0x37 && base[start + 0x30010] == 0x37);
    assert(base[start + 0xFFF0] == 0 && base[start + 0x3000F] == 0);
    assert(code_object_calls == 0);
}

void TestRetry(bool permission) {
    auto* base = Reserve();
    constexpr uint32_t start = 0x03000000;
    assert(nxmem::Commit(start, 0x10000));
    base[start] = 0x83;
    const auto allocation_count = allocations.size();
    const auto mapping_count = mapped.size();
    if (permission) fail_permission = true; else fail_map = true;
    assert(!nxmem::Commit(start - 0x10000, 0x30000));
    assert(allocations.size() == allocation_count && mapped.size() == mapping_count);
    assert(base[start] == 0x83);
    assert(nxmem::Commit(start - 0x10000, 0x30000));
    assert(base[start] == 0x83 && base[start - 1] == 0 && base[start + 0x10000] == 0);
}

void TestBoundary() {
    auto* base = Reserve();
    assert(nxmem::Commit(0xFFFFFFF0, 16));
    base[0xFFFFFFFF] = 0xA7;
    assert(!nxmem::Commit(0xFFFFFFF0, 17));
    assert(!nxmem::Commit(0xFFFFFFFF, 2));
    assert(!nxmem::Commit(1, size_t(-1)));
    assert(nxmem::Commit(0xFFFFFFFF, 0));
    nxmem::Decommit(0xFFFFFFF0, 17);
    assert(base[0xFFFFFFFF] == 0xA7);
    nxmem::Decommit(0xFFFFFFF0, 16);
    assert(base[0xFFFFFFFF] == 0);
}

void TestMirrors() {
    auto* base = Reserve();
    const auto count = allocations.size();
    assert(nxmem::Commit(kPhysical, kRamSize));
    assert(nxmem::Commit(kMirror, kRamSize));
    assert(allocations.size() == count);
    for (size_t i = 0; i < kRamSize; i += 4096) {
        base[kPhysical + i] = uint8_t(i / 4096);
        assert(base[kMirror + i] == uint8_t(i / 4096));
        base[kMirror + i + 1] = uint8_t(i / 4096 + 23);
        assert(base[kPhysical + i + 1] == uint8_t(i / 4096 + 23));
    }
    nxmem::Decommit(kPhysical, kRamSize);
    nxmem::Decommit(kMirror, kRamSize);
    assert(base[kPhysical + 1] == 23 && base[kMirror + 1] == 23);
    assert(nxmem::Commit(kPhysical - 1, kRamSize + 2));
    assert(nxmem::Commit(kMirror - 1, kRamSize + 2));
    assert(base[kPhysical + 1] == 23 && base[kMirror + 1] == 23);
    assert(base[kPhysical - 1] == 0 && base[kPhysical + kRamSize] == 0);
    assert(base[kMirror - 1] == 0 && base[kMirror + kRamSize] == 0);
    assert(code_object_calls == 0);
}

void TestReserveFailure(const std::string& failure) {
    if (failure == "reserve-map") fail_map = true;
    else if (failure == "reserve-permission") fail_permission = true;
    else if (failure == "reserve-mirror") fail_mirror = true;
    else if (failure == "reserve-query") fail_query = true;
    else std::abort();
    assert(!nxmem::Reserve());
    assert(mapped.empty() && allocations.empty() && !reservation.address);
    Reserve();
}
}

extern "C" void __real_free(void*);
extern "C" void* __wrap_memalign(size_t alignment, size_t size) {
    assert(alignment == 0x1000 && size % alignment == 0);
    char path[] = "/tmp/shaolin-memory-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    assert(unlink(path) == 0 && ftruncate(fd, off_t(size)) == 0);
    void* hint = nullptr;
#ifdef __CYGWIN__
    static uintptr_t next_source = 0x400000000000;
    hint = reinterpret_cast<void*>(next_source);
    next_source += (size + 0xFFFF) & ~uintptr_t(0xFFFF);
#endif
    void* source = mmap(hint, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(source != MAP_FAILED);
    assert(!reservation.address || uintptr_t(source) + size <= uintptr_t(reservation.address) ||
           uintptr_t(source) >= uintptr_t(reservation.address) + reservation.size);
    allocations.emplace(source, Allocation{fd, size});
    return source;
}

extern "C" void __wrap_free(void* source) {
    auto allocation = allocations.find(source);
    if (allocation == allocations.end()) { __real_free(source); return; }
    for (const auto& [address, mapping] : mapped) assert(mapping.allocation != source);
    assert(munmap(source, allocation->second.size) == 0);
    assert(close(allocation->second.fd) == 0);
    allocations.erase(allocation);
}

Handle envGetOwnProcessHandle() { return kProcess; }
bool envIsSyscallHinted(u8 number) { ++hints[number]; return number != absent_hint; }
void* armGetTls() { return nullptr; }
HipcRequest hipcMakeRequest(void*, HipcMetadata) { std::abort(); }
HipcParsedRequest hipcParseRequest(void*) { std::abort(); }
Result svcReplyAndReceive(s32*, const Handle*, s32, Handle, u64) { std::abort(); }
Result svcCreateSession(Handle*, Handle*, bool, u64) { std::abort(); }
Result svcSendSyncRequest(Handle) { std::abort(); }
Result threadCreate(Thread*, void (*)(void*), void*, void*, size_t, int, int) { std::abort(); }
Result threadStart(Thread*) { std::abort(); }
Result threadWaitForExit(Thread*) { std::abort(); }
Result threadClose(Thread*) { std::abort(); }
void virtmemLock() {}
void virtmemUnlock() {}
void* virtmemFindAslr(size_t size, size_t) {
    assert(size == kSpace && !reservation.address);
    void* memory = mmap(nullptr, size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(memory != MAP_FAILED);
#ifdef __CYGWIN__
    // Partial replacement of a Windows reservation is unavailable through mmap.
    // Keep its address interval in the kernel model, with real file-backed aliases.
    assert(munmap(memory, size) == 0);
#endif
    return memory;
}
VirtmemReservation* virtmemAddReservation(void* memory, size_t size) {
    reservation = {memory, size};
    return &reservation;
}
void virtmemRemoveReservation(VirtmemReservation* value) {
    assert(value == &reservation && mapped.empty() && allocations.empty());
#ifndef __CYGWIN__
    assert(munmap(value->address, value->size) == 0);
#endif
    reservation = {};
}
Result svcCreateCodeMemory(Handle* handle, void* source, u64) {
    ++code_object_calls;
    if (code_handles.size() == 10) return kOutOfResource;
    *handle = Handle(code_object_calls + 100);
    assert(allocations.contains(source));
    code_handles.emplace(*handle, source);
    return 0;
}
Result svcControlCodeMemory(Handle handle, CodeMapOperation operation, void* destination, u64 size, u64 permissions) {
    assert(code_handles.contains(handle));
    if (operation == CodeMapOperation_MapOwner)
        return Install(uintptr_t(destination), code_handles.at(handle), 0, size, Kind::CodeMemory, permissions);
    return Remove(uintptr_t(destination), size, Kind::CodeMemory);
}
Result svcCloseHandle(Handle handle) { assert(code_handles.erase(handle) == 1); return 0; }
Result svcMapProcessCodeMemory(Handle process, u64 destination, u64 source, u64 size) {
    assert(process == kProcess);
    ++map_calls;
    if (fail_map) { fail_map = false; return kInjectedFailure; }
    return Install(destination, reinterpret_cast<void*>(source), 0, size, Kind::ModuleCode, 0);
}
Result svcSetProcessMemoryPermission(Handle process, u64 destination, u64 size, u32 permissions) {
    assert(process == kProcess && permissions == Perm_Rw);
    if (fail_permission) { fail_permission = false; return kInjectedFailure; }
    auto& mapping = mapped.at(destination);
    assert(mapping.kind == Kind::ModuleCode && size == mapping.size);
    assert(mprotect(reinterpret_cast<void*>(destination), size, PROT_READ | PROT_WRITE) == 0);
    mapping.permissions = permissions;
    return 0;
}
Result svcUnmapProcessCodeMemory(Handle process, u64 destination, u64 source, u64 size) {
    assert(process == kProcess && mapped.at(destination).allocation == reinterpret_cast<void*>(source));
    return Remove(destination, size, Kind::ModuleCode);
}
Result svcMapProcessMemory(void* destination, Handle process, u64 source, u64 size) {
    assert(process == kProcess);
    if (fail_mirror) { fail_mirror = false; return kInjectedFailure; }
    auto mapping = mapped.find(source);
    assert(mapping != mapped.end() && mapping->second.kind == Kind::ModuleCode);
    assert(mapping->second.size == size && mapping->second.permissions == Perm_Rw);
    return Install(uintptr_t(destination), mapping->second.allocation, mapping->second.offset, size, Kind::Mirror, Perm_Rw);
}
Result svcUnmapProcessMemory(void* destination, Handle process, u64 source, u64 size) {
    assert(process == kProcess && FindMapping(source));
    return Remove(uintptr_t(destination), size, Kind::Mirror);
}
Result svcQueryMemory(MemoryInfo* info, u32* page_info, u64 address) {
    if (fail_query) { fail_query = false; return kInjectedFailure; }
    auto* mapping = FindMapping(address);
    assert(mapping);
    info->perm = mapping->permissions;
    *page_info = 0;
    return 0;
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string scenario = argv[1];
    if (scenario == "image") TestImage();
    else if (scenario == "sparse") TestSparse();
    else if (scenario == "overlap") TestOverlapAndDecommit();
    else if (scenario == "retry-map") TestRetry(false);
    else if (scenario == "retry-permission") TestRetry(true);
    else if (scenario == "boundary") TestBoundary();
    else if (scenario == "mirrors") TestMirrors();
    else if (scenario.starts_with("reserve-")) TestReserveFailure(scenario);
    else if (scenario == "hints") {
        absent_hint = 0x4B;
        Reserve();
        assert(hints[0x4B] == 0 && hints[0x4C] == 0);
    } else std::abort();
    std::printf("guest memory regression passed: %s (module maps=%u, KCodeMemory objects=%u)\n",
                scenario.c_str(), map_calls, code_object_calls);
}
