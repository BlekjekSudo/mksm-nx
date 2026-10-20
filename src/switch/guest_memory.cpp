#include "switch/guest_memory.h"
#include <switch.h>
#include <malloc.h>
#include <algorithm>
#include <cstring>
#include <map>
#include <mutex>
#include "core/log.h"

namespace nxmem {
namespace {
constexpr uint64_t kSpace = 1ull << 32;
constexpr uint32_t kChunk = 0x10000;
constexpr uint32_t kPhysical = 0x80000000;
constexpr uint32_t kMirror = 0xF0000000;
constexpr size_t kRamSize = 64u << 20;
uint8_t* base;
VirtmemReservation* reservation;
std::mutex lock;
bool mirror_mapped;
Handle process_handle;
bool owns_process_handle;
bool mapping_error;
struct Mapping { void* source; size_t size; };
std::map<uint32_t, Mapping> mappings;

struct HandleRequest { Handle server; Handle process{}; Result result{}; };
void ReceiveProcessHandle(void* argument) {
    auto& request = *static_cast<HandleRequest*>(argument);
    hipcMakeRequest(armGetTls(), HipcMetadata{});
    s32 index = 0;
    request.result = svcReplyAndReceive(&index, &request.server, 1, INVALID_HANDLE, 5000000000ull);
    if (R_SUCCEEDED(request.result)) {
        auto message = hipcParseRequest(armGetTls());
        if (message.meta.num_copy_handles == 1)
            request.process = message.data.copy_handles[0];
    }
    // Closing the receiving endpoint also releases the synchronous sender.
    svcCloseHandle(request.server);
}

bool AcquireProcessHandle() {
    process_handle = envGetOwnProcessHandle();
    if (process_handle != INVALID_HANDLE) return true;
    // NSO-style emulator loaders can omit the optional homebrew process handle.
    // Obtain a real handle through HIPC copy semantics, as nx-hbloader does.
    // MapProcessMemory requires a real handle, not CUR_PROCESS_HANDLE.
    if (!envIsSyscallHinted(0x40) || !envIsSyscallHinted(0x43) ||
        !envIsSyscallHinted(0x21)) return false;
    HandleRequest request{};
    Handle client = INVALID_HANDLE;
    Result rc = svcCreateSession(&request.server, &client, false, 0);
    if (R_FAILED(rc)) { LOGE("memory: create handle session: %08X", rc); return false; }
    Thread receiver{};
    rc = threadCreate(&receiver, ReceiveProcessHandle, &request, nullptr, 0x4000, 0x2C, -2);
    if (R_FAILED(rc)) {
        svcCloseHandle(request.server); svcCloseHandle(client);
        LOGE("memory: create handle receiver: %08X", rc); return false;
    }
    rc = threadStart(&receiver);
    if (R_FAILED(rc)) {
        threadClose(&receiver); svcCloseHandle(request.server); svcCloseHandle(client);
        LOGE("memory: start handle receiver: %08X", rc); return false;
    }
    HipcMetadata metadata{};
    metadata.num_copy_handles = 1;
    hipcMakeRequest(armGetTls(), metadata).copy_handles[0] = CUR_PROCESS_HANDLE;
    svcSendSyncRequest(client); // Receiver closes its endpoint instead of replying.
    svcCloseHandle(client);
    threadWaitForExit(&receiver);
    threadClose(&receiver);
    if (R_FAILED(request.result) || request.process == INVALID_HANDLE) {
        LOGE("memory: receive own process handle: %08X", request.result); return false;
    }
    process_handle = request.process;
    owns_process_handle = true;
    LOGR("memory: obtained own process handle through HIPC");
    return true;
}

bool Map(uint32_t address, size_t size) {
    void* source = memalign(0x1000, size);
    if (!source) { LOGE("memory: backing allocation %08X+%zX failed", address, size); return false; }
    const auto destination = reinterpret_cast<u64>(base + address);
    // Horizon has only ten KCodeMemory objects system-wide. Module-code
    // aliases use the process handle instead of one such object per chunk.
    Result rc = svcMapProcessCodeMemory(process_handle, destination, reinterpret_cast<u64>(source), size);
    if (R_FAILED(rc)) {
        LOGE("memory: map process code %08X+%zX failed: %08X", address, size, rc);
        free(source);
        return false;
    }
    rc = svcSetProcessMemoryPermission(process_handle, destination, size, Perm_Rw);
    if (R_FAILED(rc)) {
        LOGE("memory: permission %08X+%zX failed: %08X", address, size, rc);
        const Result undo = svcUnmapProcessCodeMemory(process_handle, destination, reinterpret_cast<u64>(source), size);
        if (R_FAILED(undo)) {
            LOGE("memory: failed permission cleanup %08X: %08X; backing retained", address, undo);
            mappings.emplace(address, Mapping{source, size});
            mapping_error = true;
        } else {
            free(source);
        }
        return false;
    }
    mappings.emplace(address, Mapping{source, size});
    std::memset(base + address, 0, size);
    return true;
}

void Rollback() {
    if (mirror_mapped) {
        Result rc = svcUnmapProcessMemory(base + kMirror, process_handle, reinterpret_cast<uint64_t>(base + kPhysical), kRamSize);
        if (R_FAILED(rc)) { LOGE("memory: rollback physical mirror failed: %08X", rc); mapping_error = true; return; }
        mirror_mapped = false;
    }
    for (auto it = mappings.begin(); it != mappings.end();) {
        const auto& [address, mapping] = *it;
        Result rc = svcUnmapProcessCodeMemory(process_handle, reinterpret_cast<u64>(base + address),
                                               reinterpret_cast<u64>(mapping.source), mapping.size);
        if (R_FAILED(rc)) {
            LOGE("memory: rollback %08X failed: %08X; backing retained", address, rc);
            mapping_error = true;
            ++it;
        } else {
            free(mapping.source);
            it = mappings.erase(it);
        }
    }
    if (!mappings.empty()) return;
    if (owns_process_handle) svcCloseHandle(process_handle);
    process_handle = INVALID_HANDLE;
    owns_process_handle = false;
    virtmemLock();
    virtmemRemoveReservation(reservation);
    virtmemUnlock();
    reservation = nullptr;
    base = nullptr;
    mapping_error = false;
}
}

uint8_t* Reserve() {
    std::lock_guard guard(lock);
    if (base) return mapping_error ? nullptr : base;
    // Calling an unhinted SVC can terminate homebrew rather than return an
    // error. Never silently continue with disconnected physical RAM mirrors.
    for (u8 number : {0x73, 0x74, 0x75, 0x77, 0x78})
        if (!envIsSyscallHinted(number)) { LOGE("memory: required SVC %02X unavailable", number); return nullptr; }
    if (!AcquireProcessHandle()) return nullptr;
    virtmemLock();
    base = static_cast<uint8_t*>(virtmemFindAslr(kSpace, 0x10000));
    if (base) reservation = virtmemAddReservation(base, kSpace);
    virtmemUnlock();
    if (!reservation) {
        if (owns_process_handle) svcCloseHandle(process_handle);
        process_handle = INVALID_HANDLE; owns_process_handle = false;
        base = nullptr; return nullptr;
    }
    if (!Map(kPhysical, kRamSize)) { Rollback(); return nullptr; }
    Result rc = svcMapProcessMemory(base + kMirror, process_handle,
                                    reinterpret_cast<uint64_t>(base + kPhysical), kRamSize);
    if (R_FAILED(rc)) {
        LOGE("memory: physical RAM alias failed: %08X", rc);
        Rollback();
        return nullptr;
    }
    mirror_mapped = true;
    MemoryInfo info{};
    u32 page_info = 0;
    rc = svcQueryMemory(&info, &page_info, reinterpret_cast<u64>(base + kMirror));
    if (R_FAILED(rc) || (info.perm & Perm_Rw) != Perm_Rw) {
        LOGE("memory: physical mirror is not readable/writable: %08X permissions %X", rc, info.perm);
        Rollback();
        return nullptr;
    }
    for (size_t offset = 0; offset < kRamSize; offset += 0x100000) {
        auto* physical = reinterpret_cast<volatile u32*>(base + kPhysical + offset);
        auto* mirror = reinterpret_cast<volatile u32*>(base + kMirror + offset);
        const u32 saved = *physical;
        *physical = u32(0x514C0000 + offset);
        bool shared = *mirror == u32(0x514C0000 + offset);
        *mirror = u32(0xA7C30000 + offset);
        shared &= *physical == u32(0xA7C30000 + offset);
        *physical = saved;
        if (!shared) { LOGE("memory: physical alias coherence failed at %zX", offset); Rollback(); return nullptr; }
    }
    LOGR("memory: reserved 4 GiB at %p, physical RAM mirrors share 64 MiB; bidirectional check passed", static_cast<void*>(base));
    return base;
}

bool Commit(uint32_t address, size_t size) {
    if (!size) return true;
    if (size > kSpace - address) return false;
    std::lock_guard guard(lock);
    if (!base || mapping_error) return false;
    const uint64_t end = (uint64_t(address) + size + kChunk - 1) & ~uint64_t(kChunk - 1);
    for (uint64_t page = address & ~(kChunk - 1); page < end;) {
        if ((page >= kPhysical && page < kPhysical + kRamSize) ||
            (page >= kMirror && page < kMirror + kRamSize)) { page += kChunk; continue; }
        auto next = mappings.upper_bound(uint32_t(page));
        if (next != mappings.begin()) {
            const auto previous = std::prev(next);
            const uint64_t mapped_end = uint64_t(previous->first) + previous->second.size;
            if (page < mapped_end) { page = mapped_end; continue; }
        }
        uint64_t run_end = next == mappings.end() ? end : std::min(end, uint64_t(next->first));
        for (uint64_t boundary : {uint64_t(kPhysical), uint64_t(kMirror)})
            if (page < boundary) run_end = std::min(run_end, boundary);
        if (!Map(uint32_t(page), size_t(run_end - page))) return false;
        page = run_end;
    }
    return true;
}

void Decommit(uint32_t address, size_t size) {
    if (!size || size > kSpace - address) return;
    std::lock_guard guard(lock);
    if (!base || mapping_error) return;
    const uint64_t end = uint64_t(address) + size;
    // One backing allocation can contain neighboring committed pages. Keep
    // it for reuse; zero only the decommitted subrange, never neighbours.
    for (auto& [start, mapping] : mappings) {
        if (start == kPhysical) continue;
        const uint64_t lo = std::max<uint64_t>(address, start);
        const uint64_t hi = std::min<uint64_t>(end, uint64_t(start) + mapping.size);
        if (hi > lo) std::memset(base + lo, 0, size_t(hi - lo));
    }
}
}
