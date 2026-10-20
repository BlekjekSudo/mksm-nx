#include "switch/posix_io.h"
#include <unistd.h>
#include <cerrno>
#include <mutex>
#include <algorithm>
#include <cstring>
#include <limits>

namespace nxposix {
namespace {
// Guest module-code aliases are not suitable FS IPC buffers on Horizon.
// Always use native .bss memory, including small and unaligned transfers.
alignas(4096) unsigned char buffer[256 * 1024];
std::mutex buffer_lock;
}

size_t Read(FILE* file, void* data, size_t count) {
    std::lock_guard guard(buffer_lock);
    size_t total = 0;
    while (total < count) {
        const size_t want = std::min(count - total, sizeof(buffer));
        const size_t got = std::fread(buffer, 1, want, file);
        if (got) std::memcpy(static_cast<unsigned char*>(data) + total, buffer, got);
        total += got;
        if (got < want) break;
    }
    return total;
}

size_t Write(FILE* file, const void* data, size_t count) {
    std::lock_guard guard(buffer_lock);
    size_t total = 0;
    while (total < count) {
        const size_t want = std::min(count - total, sizeof(buffer));
        std::memcpy(buffer, static_cast<const unsigned char*>(data) + total, want);
        const size_t done = std::fwrite(buffer, 1, want, file);
        total += done;
        if (done < want) break;
    }
    return total;
}

ssize_t Pread(int fd, void* data, size_t count, off_t offset) {
    if (count > size_t(std::numeric_limits<ssize_t>::max())) { errno = EINVAL; return -1; }
    // libnx/newlib declares pread but does not provide its implementation.
    // Disc owns this descriptor; all its concurrent positioned reads come
    // through this lock, leaving the original file position untouched.
    static std::mutex lock;
    std::lock_guard guard(lock);
    const off_t before = lseek(fd, 0, SEEK_CUR);
    if (before == off_t(-1) || lseek(fd, offset, SEEK_SET) == off_t(-1)) return -1;
    ssize_t result = 0;
    {
        std::lock_guard guard(buffer_lock);
        while (size_t(result) < count) {
            const size_t want = std::min(count - size_t(result), sizeof(buffer));
            const ssize_t got = read(fd, buffer, want);
            if (got < 0) { if (!result) result = -1; break; }
            if (got) std::memcpy(static_cast<unsigned char*>(data) + result, buffer, size_t(got));
            result += got;
            if (size_t(got) < want) break;
        }
    }
    const int saved_errno = errno;
    if (lseek(fd, before, SEEK_SET) == off_t(-1)) return -1;
    if (result < 0) errno = saved_errno;
    return result;
}
}
