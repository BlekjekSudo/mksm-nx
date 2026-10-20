#pragma once
// Small Windows API surface used by shared code, with portable equivalents
// elsewhere. Windows builds include the real headers unchanged.
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#elif defined(__SWITCH__)
#include <switch.h>
#include <chrono>
inline unsigned long GetCurrentProcessId() { u64 id = 0; svcGetProcessId(&id, CUR_PROCESS_HANDLE); return id; }
inline unsigned long GetCurrentThreadId() { u64 id = 0; svcGetThreadId(&id, CUR_THREAD_HANDLE); return id; }
inline uint64_t GetTickCount64() {
    return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}
#else
#include <unistd.h>
#include <chrono>
#include <cstdint>
#include <sys/syscall.h>
inline unsigned long GetCurrentProcessId() { return static_cast<unsigned long>(getpid()); }
inline unsigned long GetCurrentThreadId() { return static_cast<unsigned long>(syscall(SYS_gettid)); }
inline uint64_t GetTickCount64() {
    return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
                        std::chrono::steady_clock::now().time_since_epoch()).count());
}
#endif
