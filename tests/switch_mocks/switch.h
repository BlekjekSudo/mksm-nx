#pragma once
#include <cstddef>
#include <cstdint>

using u8 = uint8_t;
using u32 = uint32_t;
using u64 = uint64_t;
using s32 = int32_t;
using Result = uint32_t;
using Handle = uint32_t;
constexpr Handle INVALID_HANDLE = 0;
constexpr Handle CUR_PROCESS_HANDLE = 0xFFFF8001;
constexpr u32 Perm_Rw = 3;
constexpr bool R_SUCCEEDED(Result result) { return result == 0; }
constexpr bool R_FAILED(Result result) { return result != 0; }
enum CodeMapOperation { CodeMapOperation_MapOwner, CodeMapOperation_UnmapOwner };
struct VirtmemReservation { void* address; size_t size; };
struct MemoryInfo { u32 perm{}; };
struct Thread {};
struct HipcMetadata { u32 num_copy_handles{}; };
struct HipcRequest { Handle* copy_handles; };
struct HipcParsedRequest { HipcMetadata meta; HipcRequest data; };

Handle envGetOwnProcessHandle();
bool envIsSyscallHinted(u8 number);
void* armGetTls();
HipcRequest hipcMakeRequest(void*, HipcMetadata);
HipcParsedRequest hipcParseRequest(void*);
Result svcReplyAndReceive(s32*, const Handle*, s32, Handle, u64);
Result svcCreateSession(Handle*, Handle*, bool, u64);
Result svcSendSyncRequest(Handle);
Result svcCloseHandle(Handle);
Result threadCreate(Thread*, void (*)(void*), void*, void*, size_t, int, int);
Result threadStart(Thread*);
Result threadWaitForExit(Thread*);
Result threadClose(Thread*);
void virtmemLock();
void virtmemUnlock();
void* virtmemFindAslr(size_t size, size_t guard_size);
VirtmemReservation* virtmemAddReservation(void*, size_t);
void virtmemRemoveReservation(VirtmemReservation*);
Result svcCreateCodeMemory(Handle*, void*, u64);
Result svcControlCodeMemory(Handle, CodeMapOperation, void*, u64, u64);
Result svcMapProcessCodeMemory(Handle, u64, u64, u64);
Result svcUnmapProcessCodeMemory(Handle, u64, u64, u64);
Result svcSetProcessMemoryPermission(Handle, u64, u64, u32);
Result svcMapProcessMemory(void*, Handle, u64, u64);
Result svcUnmapProcessMemory(void*, Handle, u64, u64);
Result svcQueryMemory(MemoryInfo*, u32*, u64);
