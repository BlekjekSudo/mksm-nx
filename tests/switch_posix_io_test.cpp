#include "switch/posix_io.h"
#include <unistd.h>
#include <array>
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdlib>
#include <thread>
#include <vector>

int main() {
    char name[] = "/tmp/shaolin-pread-XXXXXX";
    int fd = mkstemp(name);
    assert(fd >= 0);
    unlink(name);
    std::array<unsigned char, 4096> bytes{};
    for (size_t i = 0; i < bytes.size(); ++i) bytes[i] = (i * 37 + 11) & 255;
    assert(write(fd, bytes.data(), bytes.size()) == ssize_t(bytes.size()));
    assert(lseek(fd, 7, SEEK_SET) == 7);
    std::vector<std::thread> workers;
    for (unsigned worker = 0; worker < 4; ++worker) workers.emplace_back([&, worker] {
        for (unsigned i = 0; i < 128; ++i) {
            const size_t at = (i * 31 + worker * 7) % bytes.size();
            std::array<unsigned char, 127> actual{};
            const size_t expected = std::min(actual.size(), bytes.size() - at);
            assert(nxposix::Pread(fd, actual.data(), actual.size(), off_t(at)) == ssize_t(expected));
            for (size_t j = 0; j < expected; ++j) assert(actual[j] == bytes[at + j]);
        }
    });
    for (auto& worker : workers) worker.join();
    assert(lseek(fd, 0, SEEK_CUR) == 7);
    unsigned char out;
    assert(nxposix::Pread(fd, &out, 1, -1) == -1 && errno == EINVAL);
    assert(lseek(fd, 0, SEEK_CUR) == 7);
    assert(nxposix::Pread(-1, &out, 1, 0) == -1 && errno == EBADF);
    close(fd);
}
