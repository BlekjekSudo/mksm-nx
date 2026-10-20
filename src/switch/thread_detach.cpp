#include <switch.h>
#include <sys/iosupport.h>
#include <atomic>
#include <cerrno>
#include <cstdlib>
#include "switch/thread_detach.h"

namespace {
// libnx supports joining pthreads but leaves its detach syscall unimplemented.
// Use public join/Thread APIs, without relying on the opaque pthread layout.
// Each detached pthread has a sleeping join worker; one reaper releases those
// workers after their kernel threads have exited, including their stacks/TLS.
struct JoinJob { Thread worker; __pthread_t* target; JoinJob* next; };
Mutex jobs_mutex;
Thread reaper;
bool reaper_started;
JoinJob* jobs;
std::atomic<uint64_t> accepted{}, cleaned{};

void Join(void* argument) {
    auto* job = static_cast<JoinJob*>(argument);
    __syscall_thread_join(job->target);
}

void Reap(void*) {
    for (;;) {
        mutexLock(&jobs_mutex);
        JoinJob** link = &jobs;
        while (*link) {
            JoinJob* job = *link;
            s32 index = 0;
            Result rc = svcWaitSynchronization(&index, &job->worker.handle, 1, 0);
            if (R_SUCCEEDED(rc) && R_SUCCEEDED(threadClose(&job->worker))) {
                *link = job->next;
                std::free(job);
                cleaned.fetch_add(1, std::memory_order_relaxed);
            } else {
                link = &job->next;
            }
        }
        mutexUnlock(&jobs_mutex);
        svcSleepThread(10000000);
    }
}
}

extern "C" int __syscall_thread_detach(__pthread_t* target) {
    if (!target || target == reinterpret_cast<__pthread_t*>(~uintptr_t(0))) return EINVAL;
    mutexLock(&jobs_mutex);
    // A completed pthread's allocation can already have been reused while
    // its join worker is still awaiting reaping. Never identify live targets
    // by comparing those old, now opaque pointer values.
    if (!reaper_started) {
        Result rc = threadCreate(&reaper, Reap, nullptr, nullptr, 0x4000, 0x3B, -2);
        if (R_SUCCEEDED(rc)) {
            rc = threadStart(&reaper);
            if (R_FAILED(rc)) threadClose(&reaper);
        }
        if (R_FAILED(rc)) { mutexUnlock(&jobs_mutex); return EAGAIN; }
        reaper_started = true;
    }
    auto* job = static_cast<JoinJob*>(std::calloc(1, sizeof(JoinJob)));
    if (!job) { mutexUnlock(&jobs_mutex); return ENOMEM; }
    job->target = target;
    Result rc = threadCreate(&job->worker, Join, job, nullptr, 0x4000, 0x3B, -2);
    if (R_SUCCEEDED(rc)) {
        rc = threadStart(&job->worker);
        if (R_FAILED(rc)) threadClose(&job->worker);
    }
    if (R_FAILED(rc)) {
        std::free(job); mutexUnlock(&jobs_mutex); return EAGAIN;
    }
    job->next = jobs;
    jobs = job;
    accepted.fetch_add(1, std::memory_order_relaxed);
    mutexUnlock(&jobs_mutex);
    return 0;
}

namespace nxthreads {
DetachedStats DetachedSnapshot() {
    return {accepted.load(std::memory_order_relaxed), cleaned.load(std::memory_order_relaxed)};
}
}
