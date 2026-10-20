#pragma once
#include <switch.h>
#include "core/log.h"

namespace nxthreads {
inline void Configure(int priority) {
    u64 allowed = 0;
    if (R_SUCCEEDED(svcGetInfo(&allowed, InfoType_CoreMask, CUR_PROCESS_HANDLE, 0))) {
        const u64 application_cores = allowed & 7;
        if (application_cores) {
            Result rc = svcSetThreadCoreMask(CUR_THREAD_HANDLE, -1, application_cores);
            if (R_FAILED(rc)) LOGE("thread: application core affinity failed: %08X", rc);
        }
    }
    Result rc = svcSetThreadPriority(CUR_THREAD_HANDLE, priority);
    if (R_FAILED(rc)) LOGE("thread: native priority %d failed: %08X", priority, rc);
}
}
